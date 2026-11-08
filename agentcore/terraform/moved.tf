# 3.2.0: the private network moved from network.tf into modules/private-network
# (module.private_network). These blocks move every resource a deployment created before 3.2.0
# to its module address without replacement. Each moves a whole resource (every instance), so
# count indices, service-name keys and CIDR keys carry over unchanged.
#
# Keep them permanently: any deployment created before 3.2.0 needs them on its next plan. In
# public mode none of the `from` addresses exist, so every block is a no-op.

moved {
  from = aws_vpc.companion
  to   = module.private_network[0].aws_vpc.this
}

moved {
  from = aws_default_security_group.companion
  to   = module.private_network[0].aws_default_security_group.this
}

moved {
  from = aws_subnet.private
  to   = module.private_network[0].aws_subnet.private
}

moved {
  from = aws_route_table.private
  to   = module.private_network[0].aws_route_table.private
}

moved {
  from = aws_route_table_association.private
  to   = module.private_network[0].aws_route_table_association.private
}

moved {
  from = aws_vpc_endpoint.interface
  to   = module.private_network[0].aws_vpc_endpoint.interface
}

moved {
  from = aws_vpc_endpoint.s3
  to   = module.private_network[0].aws_vpc_endpoint.s3
}

moved {
  from = aws_security_group.vpce_client
  to   = module.private_network[0].aws_security_group.vpce_client
}

moved {
  from = aws_security_group.vpce_runtime
  to   = module.private_network[0].aws_security_group.vpce_runtime
}

moved {
  from = aws_vpc_security_group_ingress_rule.vpce_client_from_cidrs
  to   = module.private_network[0].aws_vpc_security_group_ingress_rule.vpce_client_from_cidrs
}
