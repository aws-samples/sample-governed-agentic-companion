// Private request path (network_access = "private", the default) with the opt-in gateway
// (deploy_gateway = true; the default is false, and then the stdio bridge path is the front door).
// Every node and arrow traces to agentcore/terraform/ (paths relative to it):
//   VPC, private subnets ........ modules/private-network/main.tf (create_vpc) or var.vpc_id
//   Resolver inbound (opt-in) ... modules/private-network/resolver.tf (enable_resolver_inbound_endpoint)
//   Gateway / data-plane VPCE ... modules/private-network/endpoints.tf, locals.tf managed_endpoint_keys
//   Probe (opt-in) .............. probe.tf (enable_private_access_probe)
//   AgentCore Gateway ........... gateway.tf (CUSTOM_JWT), resource_policies.tf (aws:SourceVpce)
//   MCP runtime ................. runtimes.tf, resource_policies.tf (gateway role only, SigV4)
//   Orchestrator runtime ........ runtimes.tf, resource_policies.tf (aws:SourceVpce)
//   Identity interceptor ........ interceptor.tf (enable_identity_interceptor, default true);
//                                 an account Lambda outside the VPC, invoked by the gateway
//   Amazon Cognito .............. cognito.tf (owned, or shared from platform/); gateway.tf
//                                 CUSTOM_JWT discovery URL: the signing keys come from Cognito
//                                 to the gateway ("verify token", dashed: not a request hop).
//                                 The builder signs in on Cognito's public endpoint: the kit
//                                 creates no Cognito VPC endpoint (locals.tf
//                                 managed_endpoint_keys) and cognito.tf creates a public domain.
//   Corporate network to VPC .... private_client_cidrs on the client endpoint SG, TCP 443
//                                 (modules/private-network/security_groups.tf); the VPN or
//                                 Direct Connect path itself is yours, not in this Terraform
//   DNS forward (opt-in) ........ your corporate DNS forwarder to the Resolver inbound endpoint
//   Outside caller denied ....... resource_policies.tf: gateway DenyInvokeNotViaApprovedGatewayEndpoint,
//                                 orchestrator deny unless aws:SourceVpce, MCP runtime DenyOtherPrincipals
//                                 (sandbox tests 2, 4a; a JWT call to the MCP runtime is refused as an
//                                 authorization-method mismatch because it accepts SigV4 only)
// Arrows follow the data: a request runs from the caller to the callee, and the token keys run
// from Cognito to the gateway. Dashed lines are opt-in, the public sign-in step, the token check
// or non-gateway paths; red dashed lines are denied calls.
//   MCP runtime engine .......... agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py runs
//                                 Orchestrator().handle() in-process (no call to the orchestrator runtime)
// AgentCore Gateway and Runtime have no icons of their own: each uses the Amazon Bedrock
// AgentCore service icon, captioned with its real name.
// Render: see docs/ARCHITECTURE.md "Regenerating the diagrams".
import { Diagram, Group, VStack, HStack, ZStack, Node, Arrow, Rect, Anchor } from "../src/engine.tsx";
import type { Figure } from "../src/engine.tsx";
import { Users, Internet, AmazonVPCEndpoints, AmazonRoute53Resolver } from "../src/icons/resource.js";
import { AmazonBedrockAgentCore, AmazonCognito, AWSLambda } from "../src/icons/service.js";

const COL = 128;      // the managed column
const VPC_COL = 144;  // the endpoint column
const ROW = 48;       // one row pitch in every column, so the rows line up across groups
const BAND = 48;      // a group's label band plus its top padding
const blank = (w: number, h = 8) => <Rect w={w} h={h} stroke="none" fill="none" />;
const cell = (node: Figure, w = COL) => <ZStack>{blank(w)}{node}</ZStack>;
const lowered = (h: number, node: Figure) => <VStack gap={0} align="center">{blank(8, h)}{node}</VStack>;

// The IDE sits on the gateway-endpoint row. The sign-in and DNS lines leave the top border of the
// Corporate Network box (the DNS forwarder is part of that network), left and right of its middle,
// so neither runs through the box title or beside the main path.
// Coordinates are layout coordinates read from measure.ts.
const IDE_DROP = 280;
const EXT_DROP = 616;   // the outside caller on the orchestrator row
const CORP_TOP = 280;   // the Corporate Network box top edge
const TOK_X = 24;       // the sign-in line's climb, near the box's left end
const DNS_X = 160;      // the DNS line's climb, near the box's right end
const TOP_LANE = 88;    // the sign-in line's lane: under the Region title, above the VPC
const RIN_Y = 216;      // the Resolver inbound row
const COG_X = 456;      // the sign-in line's drop, in the gap between the VPC and Regional services
const COG_Y = 216;      // the Cognito row

export default (
  <Diagram title="Private request path (deploy_gateway = true)"
    subtitle="Private mode with the opt-in gateway; without it, the stdio bridge is the path"
    layout="row" gap={24} align="start">
    {lowered(IDE_DROP, (
      <Group kind="datacenter" id="corp" label="Corporate Network">
        {cell(<Node id="ide" icon={Users} label="Builder IDE" sub="MCP client" />, 112)}
      </Group>
    ))}

    <Group kind="aws-cloud" id="cloud" label="AWS Cloud">
      <Group kind="region" id="region" label="Region" layout="row" gap={24} align="start">
        <Group kind="vpc" id="vpc" label="VPC">
          <Group kind="private-subnet" id="subnet" label="Private Subnets" layout="col" gap={ROW}>
            {cell(<Node id="rin" icon={AmazonRoute53Resolver} label="Resolver inbound" sub="opt-in" />, VPC_COL)}
            {cell(<Node id="vgw" icon={AmazonVPCEndpoints} label="Gateway endpoint" sub="HTTPS 443, JWT" />, VPC_COL)}
            {cell(<Node id="probe" icon={AWSLambda} label="Access probe" sub="opt-in test stand-in" />, VPC_COL)}
            {cell(<Node id="vdp" icon={AmazonVPCEndpoints} label="Data-plane endpoint" sub="interface endpoint" />, VPC_COL)}
          </Group>
        </Group>

        <VStack gap={0} align="start">
          {blank(8, BAND)}
          <Group kind="generic" id="managed" label="Regional services" layout="col" gap={ROW} align="start">
            <HStack gap={24}>
              {cell(<Node id="cog" icon={AmazonCognito} label="Amazon Cognito" sub="public endpoint" />)}
              {blank(COL)}
            </HStack>
            <HStack gap={24}>
              {cell(<Node id="gw" icon={AmazonBedrockAgentCore} label="AgentCore Gateway" sub="JWT + SourceVpce" />)}
              {cell(<Node id="mcp" icon={AmazonBedrockAgentCore} label="MCP runtime" sub={"SigV4 only,\nengine in-process"} />)}
            </HStack>
            {cell(<Node id="icpt" icon={AWSLambda} label="AWS Lambda" sub="identity interceptor" />)}
            {cell(<Node id="orch" icon={AmazonBedrockAgentCore} label="Orchestrator runtime" sub="engine + gate" />)}
          </Group>
        </VStack>
      </Group>
    </Group>

    {lowered(EXT_DROP, cell(<Node id="ext" icon={Internet} label="Outside caller" sub="valid JWT" />, 88))}

    <Anchor id="tok-out" x={TOK_X} y={CORP_TOP} />
    <Anchor id="dns-out" x={DNS_X} y={CORP_TOP} />
    <Arrow from="tok-out" to="cog" toSide="left" dashed
      via={[[TOK_X, TOP_LANE], [COG_X, TOP_LANE], [COG_X, COG_Y]]} labelAt="middle">sign in</Arrow>
    <Arrow from="dns-out" to="rin" toSide="left" dashed via={[[DNS_X, RIN_Y]]} labelAt="start">DNS forward</Arrow>
    <Arrow from="ide" to="vgw" labelAt="end">VPN / DX</Arrow>
    <Arrow from="ide" to="vdp" fromSide="bottom" toSide="left" dashed labelAt="start">stdio bridge</Arrow>
    <Arrow from="vgw" to="gw" />
    <Arrow from="vdp" to="orch" />
    <Arrow from="gw" to="mcp">SigV4</Arrow>
    <Arrow from="cog" to="gw" fromSide="bottom" toSide="top" dashed labelSide="left">verify token</Arrow>
    <Arrow from="gw" to="icpt" fromSide="bottom" toSide="top">hook</Arrow>
    <Arrow from="probe" to="vgw" fromSide="top" toSide="bottom" dashed />
    <Arrow from="probe" to="vdp" fromSide="bottom" toSide="top" dashed />
    <Arrow from="ext" to="orch" dashed color="#C7243A">denied 403</Arrow>
    <Anchor id="deny-j" x="ext" y="mcp" />
    <Anchor id="deny-up" x="ext" y="gw" dy={-48} />
    <Anchor id="deny-turn" x="gw" dx={24} y="gw" dy={-48} />
    <Anchor id="deny-in" x="gw" dx={24} y="gw" dy={-32} />
    <Arrow from="ext" to="deny-j" fromSide="top" head="none" dashed color="#C7243A" labelSide="left">denied 403</Arrow>
    <Arrow from="deny-j" to="mcp" dashed color="#C7243A" />
    <Arrow from="deny-j" to="deny-up" head="none" dashed color="#C7243A" />
    <Arrow from="deny-up" to="deny-turn" head="none" dashed color="#C7243A" />
    <Arrow from="deny-turn" to="deny-in" dashed color="#C7243A" />
  </Diagram>
);
