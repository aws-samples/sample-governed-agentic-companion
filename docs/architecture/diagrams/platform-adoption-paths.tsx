// Adoption paths: a standalone companion, or a shared platform/ stack consumed by companions.
// Every node and arrow traces to agentcore/terraform/ (paths relative to it):
//   Platform stack .............. platform/main.tf (module.backend, module.identity), platform/network.tf
//                                 (module.network, enable_private_network, default false)
//   State + access-log buckets .. modules/backend-bootstrap/main.tf (aws_s3_bucket.state, .state_logs;
//                                 the DynamoDB lock table is a module-level opt-in the platform does
//                                 not pass, so it is not drawn)
//   Shared Cognito pool ......... modules/shared-identity/main.tf (pool, domain, one group per app)
//   Shared private network ...... platform/network.tf, platform/network_policies.tf (opt-in)
//   Consuming companion ......... cognito.tf (manage_identity_pool = false: no pool, but its OWN
//                                 resource-server scope and clients in the shared pool); inputs from
//                                 platform/outputs.tf companion_consumer_inputs
//   Endpoint SG ingress ......... network.tf (manage_existing_endpoint_ingress, shared network only)
//   Standalone companion ........ cognito.tf (manage_identity_pool = true, the default: owns its pool);
//                                 network.tf (module.private_network: its own network)
//   Remote state (opt-in) ....... backend.tf.example (backend.tf is gitignored; a key per stack)
// Path A reads first (left), Path B second (right). Every arrow starts at its companion node.
// Arrows follow the data: each companion stack writes into the platform resources (its own scope
// and clients in the pool, its state object, its SG ingress rules); dashed lines are opt-in.
// The AgentCore icon stands for a companion Terraform stack: its runtimes and gateway.
// Render: see docs/ARCHITECTURE.md "Regenerating the diagrams".
import { Diagram, Group, VStack, ZStack, Node, Arrow, Rect } from "../src/engine.tsx";
import type { Figure } from "../src/engine.tsx";
import { AmazonSimpleStorageServiceBucket, AmazonVPCVirtualPrivateCloudVPC } from "../src/icons/resource.js";
import { AmazonBedrockAgentCore, AmazonCognito } from "../src/icons/service.js";
const COL = 160;      // every node sits in a cell this wide, so the columns line up
const MID = 192;      // the platform column: room for the arrow labels that end on it
const ROW = 48;       // one row pitch
const blank = (w: number, h = 8) => <Rect w={w} h={h} stroke="none" fill="none" />;
const cell = (node: Figure, w = COL) => <ZStack>{blank(w)}{node}</ZStack>;
// The consuming companion sits on the shared-pool row inside a top-aligned box (measured,
// see the render report), so its three arrows leave three faces of one node.
const CONSUMER_DROP = 136;
export default (
  <Diagram title="Adoption paths"
    subtitle="Path A consumes the shared platform layer; Path B owns its pool and network"
    layout="row" gap={48} align="start">
    <Group kind="aws-cloud" id="cloud" label="AWS Cloud">
      <Group kind="region" id="region" label="Region" layout="row" gap={32} align="start">
        <Group kind="generic" id="consumer" label="Path A: consumer" layout="col" gap={0}>
          {blank(8, CONSUMER_DROP)}
          {cell(<Node id="ca" icon={AmazonBedrockAgentCore} label="Companion stack" sub={"runtimes + gateway,\nconsumes platform outputs"} />)}
        </Group>
        <Group kind="generic" id="platform" label="Platform stack (platform/)" layout="col" gap={ROW}>
          {cell(<Node id="state" icon={AmazonSimpleStorageServiceBucket} label="Amazon S3" sub="Terraform state + access logs" />, MID)}
          {cell(<Node id="pool" icon={AmazonCognito} label="Amazon Cognito" sub={"shared user pool,\ndomain + app groups"} />, MID)}
          {cell(<Node id="snet" icon={AmazonVPCVirtualPrivateCloudVPC} label="Shared private network" sub="opt-in" />, MID)}
        </Group>
        <Group kind="generic" id="standalone" label="Path B: standalone" layout="col" gap={ROW}>
          {cell(<Node id="cb" icon={AmazonBedrockAgentCore} label="Companion stack" sub={"runtimes + gateway,\nowns pool + network"} />)}
        </Group>
      </Group>
    </Group>
    <Arrow from="ca" to="pool">{"own scope\n+ clients"}</Arrow>
    <Arrow from="ca" to="state" fromSide="top" toSide="left" dashed labelAt="end">remote state</Arrow>
    <Arrow from="ca" to="snet" fromSide="bottom" toSide="left" dashed labelAt="end">{"endpoint\nSG ingress"}</Arrow>
    <Arrow from="cb" to="state" dashed labelAt="end">remote state</Arrow>
  </Diagram>
);
