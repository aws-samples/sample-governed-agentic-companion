# Platform interlocks. Counted on either network toggle, so with both off (the adoption shape)
# the platform holds only module.backend and module.identity resources, and precondition 2 still
# fires when only the resolver is enabled.
resource "terraform_data" "platform_guards" {
  count = var.enable_private_network || var.enable_resolver_inbound_endpoint ? 1 : 0
  input = "platform"

  lifecycle {
    precondition {
      condition     = !var.enable_private_network || length(var.private_client_cidrs) > 0
      error_message = "enable_private_network=true needs private_client_cidrs (your VPN / Direct Connect / desktop ranges); an all-addresses /0 CIDR is never accepted."
    }

    precondition {
      condition     = !var.enable_resolver_inbound_endpoint || var.enable_private_network
      error_message = "enable_resolver_inbound_endpoint=true needs enable_private_network=true (the endpoint lives in the shared network)."
    }
  }
}
