variable "name_prefix" {
  type        = string
  description = "Prefix for the shared pool and domain (for example \"companion\" for a neutral shared layer, or an owning companion's prefix during a migration). Keep customer-agnostic."
}

variable "apps" {
  type        = map(string)
  description = "Map of app key -> display label; one entitlement group `<key>-users` per entry. Keys are companion names, never customer identifiers."

  validation {
    # Guard the genericization rule at plan time: app keys stay short, lowercase, name-shaped.
    condition     = alltrue([for k in keys(var.apps) : can(regex("^[a-z][a-z0-9-]{1,31}$", k))])
    error_message = "Each app key must be a short lowercase companion name (letters, digits, hyphens), not a customer identifier."
  }
}

variable "advanced_security_mode" {
  type        = string
  description = "Cognito advanced security (adaptive auth + compromised-credential detection): ENFORCED | AUDIT | OFF."
  default     = "ENFORCED"
  validation {
    condition     = contains(["ENFORCED", "AUDIT", "OFF"], var.advanced_security_mode)
    error_message = "advanced_security_mode must be ENFORCED, AUDIT, or OFF."
  }
}

variable "mfa_configuration" {
  type        = string
  description = "Pool MFA policy: OPTIONAL (default; TOTP available, clears the no-MFA finding) | ON (require MFA) | OFF."
  default     = "OPTIONAL"
  validation {
    condition     = contains(["OPTIONAL", "ON", "OFF"], var.mfa_configuration)
    error_message = "mfa_configuration must be OPTIONAL, ON, or OFF."
  }
}
