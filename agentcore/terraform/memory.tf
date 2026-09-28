# AgentCore Memory — session management + cross-builder recall (OPT-IN, READY INFRASTRUCTURE).
# ---------------------------------------------------------------------------
# Provisions ONE AgentCore Memory resource intended for two things in a fork that wires it:
#   1. Short-term session Events (per builder, isolated by actorId) — a builder's multi-step
#      task session survives runtime restarts and resumes from any front door.
#   2. A long-term SUMMARY strategy that consolidates task summaries into a NAMESPACE, powering
#      cross-builder recall of related work.
#
# The kit's EXAMPLE engine has no session/memory seam, so this is created only when
# var.enable_memory=true (default false). Provisioning it wires the runtime role's Memory
# data-plane grant + the GAC_SESSION_STORE/GAC_MEMORY_* env; a fork's engine then consumes them.
# (See variables.tf enable_memory and locals.tf memory_env.)
#
# Governance: Memory stores conversational context + sanitized, customer-agnostic task
# summaries only. It never deploys or mutates an environment or system of record (Tenet 1/3);
# a fork sanitizes summaries before write (Tenet 6). Human-owned deployment is unchanged.

locals {
  memory_enabled = var.enable_memory

  # Namespace the SUMMARY strategy consolidates task summaries into.
  #
  # AWS constraint: a SUMMARIZATION strategy summarizes PER SESSION, so {sessionId} is a
  # MANDATORY part of the namespace (validated at create). We also template {actorId} so every
  # summary is organized by builder + session. This single template is used in BOTH sharing
  # modes — the shared-vs-isolated distinction is NOT a different storage path, it is the
  # RETRIEVAL SCOPE: cross-builder recall in shared mode retrieves ACROSS all {actorId} values;
  # isolated mode retrieves only the caller's own {actorId}. A fork's engine MUST match this
  # template in its retrieval config.
  task_summary_namespace = "gac/tasks/{actorId}/{sessionId}"
}

# ---- Memory execution role (the strategy invokes Bedrock to extract/consolidate) --------
data "aws_iam_policy_document" "memory_assume" {
  count = local.memory_enabled ? 1 : 0
  statement {
    sid     = "AssumeRoleAgentCoreMemory"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
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

resource "aws_iam_role" "memory_exec" {
  count              = local.memory_enabled ? 1 : 0
  name               = "${var.name_prefix}-memory-exec-role"
  assume_role_policy = data.aws_iam_policy_document.memory_assume[0].json
}

data "aws_iam_policy_document" "memory_exec_inline" {
  count = local.memory_enabled ? 1 : 0

  # The summary/semantic strategy invokes the configured Bedrock model to extract summaries.
  statement {
    sid    = "MemoryStrategyBedrockInvoke"
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
}

resource "aws_iam_role_policy" "memory_exec_inline" {
  count  = local.memory_enabled ? 1 : 0
  name   = "${var.name_prefix}-memory-exec-policy"
  role   = aws_iam_role.memory_exec[0].id
  policy = data.aws_iam_policy_document.memory_exec_inline[0].json
}

# ---- The Memory resource (short-term Events) --------------------------------------------
resource "aws_bedrockagentcore_memory" "this" {
  count                     = local.memory_enabled ? 1 : 0
  name                      = replace("${var.name_prefix}_memory", "-", "_")
  description               = "Governed companion session context (per-builder Events) + cross-builder task summaries"
  event_expiry_duration     = var.memory_event_expiry_days
  memory_execution_role_arn = aws_iam_role.memory_exec[0].arn

  tags = var.tags
}

# ---- Long-term SUMMARY strategy (task-summary consolidation -> recall namespace) --------
# The built-in SUMMARIZATION strategy produces condensed summaries of each session's key
# topics/tasks/decisions and consolidates them into task_summary_namespace. Cross-builder
# recall retrieves from that namespace via semantic search. The namespace is the isolation
# control (see locals): shared -> a team path (cross-builder); isolated -> per-{actorId}.
#
# NOTE: `configuration` (extraction/consolidation prompt + model overrides) is only valid for
# type="CUSTOM". For the built-in SUMMARIZATION strategy we rely on the managed defaults and
# control ONLY the namespace — which is exactly the isolation lever we need. The
# customer-agnostic "no secrets/hostnames/IPs" discipline must be enforced in a fork's engine
# (a _sanitize_summary step) before a summary is ever written, so it holds regardless of the
# managed prompt. To customize the extraction prompt, switch to a CUSTOM strategy.
#
# The reference enterprise platform additionally shows UserPreference and Semantic strategies
# (namespaces /preferences/{actorId}, /facts/{actorId}) — a fork can add those the same way.
resource "aws_bedrockagentcore_memory_strategy" "task_summary" {
  count     = local.memory_enabled ? 1 : 0
  memory_id = aws_bedrockagentcore_memory.this[0].id
  name      = "gac_task_summary"
  type      = "SUMMARIZATION"

  namespace_templates = [local.task_summary_namespace]
}
