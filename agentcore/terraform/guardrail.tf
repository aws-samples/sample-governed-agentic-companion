# Optional starter Amazon Bedrock Guardrail (create_bedrock_guardrail=true).
# ---------------------------------------------------------------------------
# A managed, ML-backed second line of defense on model interactions, COMPLEMENTING the
# deterministic agents/data_protection.py guard (defense in depth — Tenet 6). When enabled and
# pii_backend="guardrails", the runtime applies this guardrail on each write via
# bedrock:ApplyGuardrail (the id/version flow through GAC_GUARDRAIL_ID/GAC_GUARDRAIL_VERSION).
#
# It provides:
#   - a PROMPT_ATTACK content filter (jailbreak / instruction-override / goal-hijack)
#   - standard content filters (sexual / violence / hate / insults / misconduct)
#   - a sensitive-information filter: PII anonymize/block + regex DENIES for credential SHAPES
#     (AWS access-key ids, PEM private-key headers, JWTs). The regexes match the SHAPE of a
#     secret so the guardrail blocks it — they are detectors, not secrets themselves.
#
# Modeled on the AWS AgentCore reference samples. OFF by default (create_bedrock_guardrail=
# false) so a minimal deploy provisions nothing extra; a fork turns it on for production.
#
# NOTE (deliberately omitted): ContextualGroundingPolicyConfig requires a grounding source on
# every model call. A governed companion is generative (not pure RAG on every call), so
# enabling contextual grounding without a grounding document would block valid responses. A
# fork that always supplies grounding can add it.

# kics-scan ignore-line: "IAM Access Analyzer Not Enabled" is an account/org baseline control
# made opt-in here via enable_access_analyzer (access_analyzer.tf) — docs/SECURITY.md SD-8.
resource "aws_bedrock_guardrail" "this" {
  count       = var.create_bedrock_guardrail ? 1 : 0
  name        = "${var.name_prefix}-guardrail"
  description = "Starter guardrail for the governed agentic companion: prompt-attack, content, and secret/PII filtering (defense in depth with agents/data_protection.py)."

  blocked_input_messaging   = "Your request was blocked because it contains content that violates the security policy (for example, an attempt to override system instructions or elicit secret material). Please rephrase."
  blocked_outputs_messaging = "The response was blocked because it contained content that violates the security policy, such as credentials or disallowed content."

  # Prompt-attack + standard content filters.
  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE" # PROMPT_ATTACK is an input-only classifier
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # PII + credential-shape regex denies. The regexes match the SHAPE of a secret so the
  # guardrail can block it; they contain no real secret value.
  sensitive_information_policy_config {
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "PHONE"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "NAME"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "US_SOCIAL_SECURITY_NUMBER"
      action = "BLOCK"
    }
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"
    }

    regexes_config {
      name        = "AwsAccessKeyId"
      description = "Matches AWS access key id shapes (AKIA/ASIA prefix)."
      pattern     = "(?:AKIA|ASIA)[A-Z0-9]{16}"
      action      = "BLOCK"
    }
    regexes_config {
      name        = "PrivateKeyBlock"
      description = "Matches PEM private key headers."
      pattern     = "-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"
      action      = "BLOCK"
    }
    regexes_config {
      name        = "JwtToken"
      description = "Matches JWT token shapes (three base64url segments)."
      pattern     = "eyJ[A-Za-z0-9_-]+\\.eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+"
      action      = "BLOCK"
    }
  }

  # Deny topics that a governed builder companion should never assist with.
  topic_policy_config {
    topics_config {
      name       = "GenerateExploitCode"
      definition = "Requests to generate exploit code, malware, or vulnerability exploits."
      type       = "DENY"
      examples = [
        "Write a reverse shell payload",
        "Create an exploit that bypasses the authentication",
      ]
    }
  }

  tags = var.tags
}

resource "aws_bedrock_guardrail_version" "this" {
  count         = var.create_bedrock_guardrail ? 1 : 0
  guardrail_arn = aws_bedrock_guardrail.this[0].guardrail_arn
  description   = "Initial version for the governed agentic companion starter guardrail."
}
