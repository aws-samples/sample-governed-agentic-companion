# IAM Access Analyzer (kics "IAM Access Analyzer Not Enabled").
# Access Analyzer is an ACCOUNT/ORG-level control — there is normally ONE analyzer per account
# (or a delegated org analyzer). Creating it unconditionally in a per-workload starter kit
# would collide with a landing-zone-managed analyzer and duplicate across every fork. So it is
# opt-in: set enable_access_analyzer=true only when this stack owns the account baseline (e.g.
# an isolated sandbox). When enabled it satisfies the scanner control directly.
resource "aws_accessanalyzer_analyzer" "this" {
  count         = var.enable_access_analyzer ? 1 : 0
  analyzer_name = "${var.name_prefix}-analyzer"
  type          = "ACCOUNT"
  tags          = var.tags
}
