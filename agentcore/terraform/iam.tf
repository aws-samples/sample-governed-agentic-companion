# AgentCore Runtime execution role — SHARED by both runtimes.
#
# Using one role for both the orchestrator and the mcp_runtime deliberately closes a common
# gap: if each runtime gets its own synthesized role, one of them can end up WITHOUT S3 read,
# so the Tier-B overlay read fails AccessDenied. Here the S3 KB read+write grant is part of the
# role by construction — no out-of-band `put-role-policy` step, no role churn to chase.

data "aws_iam_policy_document" "exec_assume" {
  statement {
    sid     = "AssumeRolePolicy"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    # Scope the trust to this account + AgentCore ARNs (confused-deputy protection).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:*"]
    }
  }
}

# kics-scan ignore-line: "IAM Access Analyzer Not Enabled" is an account/org baseline control
# made opt-in here via enable_access_analyzer (access_analyzer.tf) — docs/SECURITY.md SD-8.
resource "aws_iam_role" "runtime_exec" {
  name               = "${var.name_prefix}-runtime-exec-role"
  assume_role_policy = data.aws_iam_policy_document.exec_assume.json
}

# AWS-managed baseline for AgentCore runtimes.
resource "aws_iam_role_policy_attachment" "runtime_exec_managed" {
  role       = aws_iam_role.runtime_exec.name
  policy_arn = "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess"
}

data "aws_iam_policy_document" "runtime_exec_inline" {
  # --- ECR: pull the runtime image ---
  statement {
    sid    = "ECRImageAccess"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [
      aws_ecr_repository.orchestrator.arn,
      aws_ecr_repository.mcp_runtime.arn,
    ]
  }
  statement {
    sid       = "ECRTokenAccess"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # --- CloudWatch Logs (AgentCore runtime log group) ---
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  # --- X-Ray + CloudWatch metrics (observability / ADOT) ---
  statement {
    sid    = "XRayTracing"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  # --- Bedrock model invocation (Tier-3 LLM) ---
  # Scoped to foundation-model + inference-profile ARNs in this region (the engine uses an
  # inference profile id). Kept region-scoped rather than "*" for least privilege.
  statement {
    sid    = "BedrockModelInvocation"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:${local.region}::foundation-model/*",
      "arn:aws:bedrock:${local.region}:${local.account_id}:inference-profile/*",
    ]
  }

  # --- Workload access tokens (AgentCore identity) ---
  statement {
    sid    = "GetAgentAccessToken"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
      "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    ]
    resources = [
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*",
    ]
  }

  # --- S3 KB read (self-learning store + curated overlay) — the durable fix ---
  # kics-scan ignore-line: reviewed FALSE POSITIVE for "IAM policy allows for data exfiltration".
  # s3:GetObject here is resource-scoped to a SINGLE bucket + the kb/ prefix (not "*"), so it
  # cannot retrieve arbitrary data — it is the least-privilege read the runtime needs for the KB.
  statement {
    sid       = "KBObjectRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.kb.arn}/${var.kb_prefix}/*"]
  }

  # --- S3 KB write (the self-learning store persists learnings/candidates/freshness) ---
  statement {
    sid       = "KBObjectWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.kb.arn}/${var.kb_prefix}/*"]
  }
  statement {
    sid       = "KBBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.kb.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.kb_prefix}/*"]
    }
  }

  # NOTE: KMS use for the KB bucket's CMK is granted by the key's own policy
  # (aws_kms_key.kb -> "AllowRuntimeRoleUse" in s3_kb.tf), which references this role's ARN. In
  # a single account a key-policy grant is sufficient, so no identity-based kms statement is
  # duplicated here.

  # --- AgentCore Memory data-plane (OPT-IN; session Events + cross-builder task summaries) ---
  # Only granted when memory is provisioned (var.enable_memory). Scoped to THIS memory
  # resource. No delete/admin — the runtime reads/writes memory, it does not manage the
  # resource. (Tenet 3: memory is a write-your-own-context/read surface, not a system of record
  # the engine mutates.)
  dynamic "statement" {
    for_each = local.memory_enabled ? [1] : []
    content {
      sid    = "AgentCoreMemoryDataPlane"
      effect = "Allow"
      actions = [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:ListSessions",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:GetMemoryRecord",
      ]
      resources = [
        aws_bedrockagentcore_memory.this[0].arn,
        "${aws_bedrockagentcore_memory.this[0].arn}/*",
      ]
    }
  }

  # --- Optional data-protection PII backend (Tenet 6, off by default) ---
  # Granted ONLY when a managed backend is enabled. Comprehend PII detection is a stateless
  # analysis call (no resource ARN); Guardrails ApplyGuardrail is scoped to the guardrail.
  dynamic "statement" {
    for_each = var.pii_backend == "comprehend" ? [1] : []
    content {
      sid       = "DataProtectionComprehend"
      effect    = "Allow"
      actions   = ["comprehend:DetectPiiEntities"]
      resources = ["*"] # Comprehend detection is not resource-scoped
    }
  }
  dynamic "statement" {
    for_each = var.pii_backend == "guardrails" ? [1] : []
    content {
      sid     = "DataProtectionGuardrails"
      effect  = "Allow"
      actions = ["bedrock:ApplyGuardrail"]
      # A Terraform-created guardrail exposes its ARN directly; a bring-your-own id is
      # composed into the standard guardrail ARN form.
      resources = [
        var.create_bedrock_guardrail ? one(aws_bedrock_guardrail.this[*].guardrail_arn) : "arn:aws:bedrock:${local.region}:${local.account_id}:guardrail/${var.guardrail_id}",
      ]
    }
  }

  # NOTE: no Secrets Manager read grant for a gateway M2M secret. The gateway authenticates its
  # outbound hop via the native AgentCore Identity OAuth2 credential provider (gateway.tf), which
  # manages the credential in its own Token Vault — the runtime role does not read a hand-managed
  # secret. A fork that supplies its own gateway_m2m_secret_arn for a non-gateway path grants the
  # matching secretsmanager:GetSecretValue itself, scoped to that secret.
}

resource "aws_iam_role_policy" "runtime_exec_inline" {
  name   = "${var.name_prefix}-runtime-exec-policy"
  role   = aws_iam_role.runtime_exec.id
  policy = data.aws_iam_policy_document.runtime_exec_inline.json
}
