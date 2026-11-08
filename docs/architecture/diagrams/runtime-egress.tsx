// Runtime egress in private mode: both runtimes run in VPC network mode, so their ENIs sit in
// the private subnets and reach AWS services only through the VPC endpoints.
// Every node and arrow traces to agentcore/terraform/ (paths relative to it):
//   Runtimes in VPC mode ........ runtimes.tf (network_configuration), network.tf (runtime SG)
//   ECR endpoints ............... locals.tf managed_endpoint_keys (ecr.api, ecr.dkr)
//   Runtime endpoints ........... locals.tf managed_endpoint_keys (logs, xray, bedrock-runtime;
//                                 comprehend with pii_backend = "comprehend")
//   Data-plane endpoint ......... modules/private-network/endpoints.tf, resource_policies.tf
//   S3 gateway endpoint ......... modules/private-network/endpoints.tf, resource_policies.tf
//                                 (KB bucket and the ECR layer bucket)
//   Amazon ECR .................. ecr.tf DenyPullNotViaEcrEndpointsOrAdmins: pulls not via the ECR
//                                 endpoints are denied, except the runtime role, listed admins and
//                                 AWS service principals (ArnNotLike / aws:PrincipalIsAWSService),
//                                 so the policy does not force this path: the line is the path an
//                                 AgentCore runtime in VPC mode needs, drawn dashed as an image pull
//   KB bucket ................... s3_kb.tf (denied unless via the S3 endpoint or a knowledge admin)
//   Amazon Bedrock (opt-in) ..... iam.tf (InvokeModel, ApplyGuardrail), guardrail.tf; the shipped
//                                 engine calls no model, so the line is dashed (a fork's LLM tier
//                                 or pii_backend = "guardrails")
//   CloudWatch Logs / X-Ray ..... iam.tf, modules/private-network/flow_logs.tf (create_vpc only)
//   KB read/write ............... iam.tf KBObjectRead and KBObjectWrite, resource_policies.tf
//                                 (the S3 endpoint policy KbBucketObjects allows both)
// Arrows follow the data: logs, traces, events and model calls run out from the runtimes; the
// image runs from ECR to the runtimes; the KB line has two heads (read and write), and the
// ENI to S3 gateway endpoint hop carries that two-way traffic plus the image layers.
// Dashed lines are opt-in, plus the image pull (a deploy-time input).
//   AgentCore Memory (opt-in) ... memory.tf (enable_memory), resource_policies.tf
// One node stands for both runtimes: they share one execution role, one runtime security group
// and the same private subnets (runtimes.tf network_configuration), so their egress is identical.
// The group is the AgentCore Runtime part of figure 1's "Regional services (outside the VPC)":
// the runtimes are AgentCore-hosted; only their ENIs sit in the private subnets.
// AgentCore Runtime and Memory have no icons of their own: each uses the Amazon Bedrock
// AgentCore service icon, captioned with its real name.
// Render: see docs/ARCHITECTURE.md "Regenerating the diagrams".
import { Diagram, Group, HStack, VStack, ZStack, Node, Arrow, Rect, Anchor } from "../src/engine.tsx";
import type { Figure } from "../src/engine.tsx";
import {
  AmazonVPCEndpoints, AmazonVPCElasticNetworkInterface, AmazonSimpleStorageServiceBucket,
} from "../src/icons/resource.js";
import {
  AmazonBedrockAgentCore, AmazonBedrock, AmazonCloudWatch, AmazonElasticContainerRegistry,
} from "../src/icons/service.js";

const COL = 160;      // every node sits in a cell this wide, so the columns line up
const ROW = 48;       // one row pitch
const GAP = 48;       // between columns
const PAD = 16;       // a group's side padding
const blank = (w: number, h = 8) => <Rect w={w} h={h} stroke="none" fill="none" />;
const cell = (node: Figure, w = COL) => <ZStack>{blank(w)}{node}</ZStack>;
const lowered = (h: number, node: Figure) => <VStack gap={0} align="center">{blank(8, h)}{node}</VStack>;
// Drops that put nodes on the rows they connect to (measured, see the render report).
const RUNTIME_DROP = 184;  // the runtimes on the ENI row
const ENI_DROP = 136;      // the ENIs on the ECR-endpoints row (the middle of the fan-out)
const RIGHT_DROP = 96;     // Memory on the data-plane endpoint row
const KB_DROP = 184;      // the KB bucket on the S3 gateway endpoint row
const FLOW_X = 440;        // the flow-logs line leaves the VPC border here
const VPC_BOTTOM = 720;    // the VPC box bottom edge (measured)
export default (
  <Diagram title="Runtime egress (private mode)"
    subtitle="Arrows follow the data; the runtimes reach AWS only through VPC endpoints"
    layout="row" gap={48} align="start">
    <Group kind="aws-cloud" id="cloud" label="AWS Cloud">
      <Group kind="region" id="region" label="Region" layout="row" gap={48} align="start">
        <VStack gap={0} align="center">
          {lowered(RUNTIME_DROP, (
            <Group kind="generic" id="managed" label="AgentCore Runtime" layout="col" gap={ROW}>
              {cell(<Node id="rt" icon={AmazonBedrockAgentCore} label="MCP + orchestrator" sub="runtimes, VPC mode" />)}
            </Group>
          ))}
          {blank(8, KB_DROP)}
          {cell(<Node id="kb" icon={AmazonSimpleStorageServiceBucket} label="Amazon S3" sub="knowledge base" />)}
        </VStack>
        <VStack gap={ROW} align="start">
          <Group kind="vpc" id="vpc" label="VPC" layout="col" gap={ROW} align="start">
            <Group kind="private-subnet" id="subnet" label="Private Subnets" layout="row" gap={GAP} align="start">
              {lowered(ENI_DROP, cell(<Node id="eni" icon={AmazonVPCElasticNetworkInterface} label="Runtime ENIs" sub="VPC egress only" />))}
              <VStack gap={ROW} align="center">
                {cell(<Node id="vdp" icon={AmazonVPCEndpoints} label="Data-plane endpoint" sub="bedrock-agentcore" />)}
                {cell(<Node id="vecr" icon={AmazonVPCEndpoints} label="ECR endpoints" sub="ecr.api, ecr.dkr" />)}
                {cell(<Node id="vrt" icon={AmazonVPCEndpoints} label="Runtime endpoints" sub="logs, X-Ray, Bedrock" />)}
              </VStack>
            </Group>
            <HStack gap={0}>
              {blank(PAD)}
              {cell(<Node id="vs3" icon={AmazonVPCEndpoints} label="S3 gateway endpoint" sub="KB + ECR layers" />)}
            </HStack>
          </Group>
          <HStack gap={0} align="start">
            {blank(2 * PAD)}
            <HStack gap={GAP} align="start">
              {blank(COL)}
              {cell(<Node id="cw" icon={AmazonCloudWatch} label="Amazon CloudWatch" sub="logs, traces, flow logs" />)}
            </HStack>
          </HStack>
        </VStack>
        {lowered(RIGHT_DROP, (
          <VStack gap={ROW} align="center">
            {cell(<Node id="mem" icon={AmazonBedrockAgentCore} label="AgentCore Memory" sub="opt-in" />)}
            {cell(<Node id="ecr" icon={AmazonElasticContainerRegistry} label="Amazon ECR" sub="runtime images" />)}
            {cell(<Node id="br" icon={AmazonBedrock} label="Amazon Bedrock" sub="models, guardrail" />)}
          </VStack>
        ))}
      </Group>
    </Group>
    <Arrow from="rt" to="eni" />
    <Anchor id="fan" between={["eni", "vecr"]} />
    <Arrow from="eni" to="fan" head="none" />
    <Arrow from="fan" to="vdp" toSide="left" />
    <Arrow from="fan" to="vecr" head="start" />
    <Arrow from="fan" to="vrt" toSide="left" />
    <Arrow from="eni" to="vs3" fromSide="bottom" toSide="top" />
    <Arrow from="vdp" to="mem" dashed>events</Arrow>
    <Arrow from="ecr" to="vecr" dashed>image</Arrow>
    <Arrow from="vrt" to="br" dashed>LLM, opt-in</Arrow>
    <Arrow from="vrt" to="cw" fromSide="bottom" toSide="top">{"logs,\ntraces"}</Arrow>
    <Arrow from="vs3" to="kb" head="both">read/write</Arrow>
    <Anchor id="vpc-out" x={FLOW_X} y={VPC_BOTTOM} />
    <Arrow from="vpc-out" to="cw" toSide="left" dashed via={[[FLOW_X, 792]]} labelAt="end">flow logs</Arrow>
  </Diagram>
);
