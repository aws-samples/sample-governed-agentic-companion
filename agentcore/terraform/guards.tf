# Deploy-time safety interlocks. These fail `terraform plan/apply` early with a clear message
# rather than letting a misconfigured auth mode reach AWS. Preconditions run during plan.

resource "terraform_data" "auth_guards" {
  # No-op resource whose only job is to host validation preconditions.
  input = var.auth_mode

  lifecycle {
    # auth_mode="none" must be explicitly confirmed — it is an unauthenticated front door.
    precondition {
      condition     = !local.auth_is_none || var.allow_unauthenticated
      error_message = "auth_mode=\"none\" is UNAUTHENTICATED (internal test only). Set allow_unauthenticated=true to confirm you intend this. Never use it outside an isolated dev account."
    }

    # entraid requires the discovery URL + at least one front-door client id.
    precondition {
      condition     = !local.auth_is_entraid || (var.entraid_discovery_url != "" && length(var.entraid_frontdoor_client_ids) > 0)
      error_message = "auth_mode=\"entraid\" requires entraid_discovery_url and at least one entraid_frontdoor_client_ids value."
    }

    # A gateway in front of an unauthenticated runtime would publish an open tool surface.
    precondition {
      condition     = !(local.auth_is_none && var.deploy_gateway)
      error_message = "Refusing to deploy a Gateway with auth_mode=\"none\": that would expose an unauthenticated tool surface. Use cognito or entraid when deploy_gateway=true."
    }

    # The Guardrails PII backend needs a guardrail: EITHER Terraform creates one
    # (create_bedrock_guardrail=true) OR you supply an existing guardrail_id. Fail loud at
    # plan time rather than a silent no-op at runtime.
    precondition {
      condition     = var.pii_backend != "guardrails" || var.create_bedrock_guardrail || var.guardrail_id != ""
      error_message = "pii_backend=\"guardrails\" requires a guardrail: set create_bedrock_guardrail=true to have Terraform create one, or supply an existing guardrail_id."
    }
  }
}
