#!/bin/sh
# sync_gateway_target.sh: re-sync the gateway target's tool list after the MCP runtime changed.
#
# Invoked by terraform_data.gateway_tool_sync (gateway.tf) inside a HUMAN-RUN apply, with:
#   AWS_REGION      the stack's Region
#   GATEWAY_ID      the gateway id
#   TARGET_ID       the gateway target id (10 upper-case letters or digits)
#   SETTLE_SECONDS  seconds to let the new runtime version settle behind DEFAULT (0..120)
#   POLL_SECONDS    optional poll interval (default 10, 0..10; only tests shorten it)
#
# The gateway target syncs its tool list only when it is created, so a new MCP runtime image
# leaves the gateway serving the old list ("Unknown tool"). This script runs one
# synchronize-gateway-targets call and waits for READY.
#
# GOVERNANCE (Tenet 1): runs only inside an apply a human started with their own credentials.
# The engine role is denied SynchronizeGatewayTargets (iam.tf DenyBoundarySelfModification).
# Nothing is echoed except the ids the deployer already sees.
set -eu

fail_input() {
  echo "sync_gateway_target: invalid $1" >&2
  exit 2
}

# check NAME VALUE REGEX: one line, matching REGEX (grep matches per line, so newlines are refused).
check() {
  [ "$(printf '%s' "$2" | wc -l | tr -d ' ')" -eq 0 ] || fail_input "$1"
  printf '%s' "$2" | grep -Eq "$3" || fail_input "$1"
}

AWS_REGION="${AWS_REGION:-}"
GATEWAY_ID="${GATEWAY_ID:-}"
TARGET_ID="${TARGET_ID:-}"
SETTLE_SECONDS="${SETTLE_SECONDS:-}"
POLL_SECONDS="${POLL_SECONDS:-10}"

check AWS_REGION "$AWS_REGION" '^[a-z]{2}(-[a-z]+)+-[0-9]$'
check GATEWAY_ID "$GATEWAY_ID" '^[a-z0-9-]{1,100}$'
check TARGET_ID "$TARGET_ID" '^[A-Z0-9]{10}$'
check SETTLE_SECONDS "$SETTLE_SECONDS" '^[0-9]{1,3}$'
[ "$SETTLE_SECONDS" -le 120 ] || fail_input SETTLE_SECONDS
check POLL_SECONDS "$POLL_SECONDS" '^[0-9]{1,2}$'
[ "$POLL_SECONDS" -le 10 ] || fail_input POLL_SECONDS

TOO_OLD="AWS CLI too old: this CLI has no bedrock-agentcore-control synchronize-gateway-targets. Upgrade the AWS CLI, or set sync_gateway_tools = false and run the command in docs/DEPLOY.md after apply."

# Detect support: the call without arguments exits non-zero by design; its message tells
# whether this CLI knows the subcommand (measured on AWS CLI 2.31.24).
out=$(aws bedrock-agentcore-control synchronize-gateway-targets 2>&1) || true
if printf '%s' "$out" | grep -q "invalid choice"; then
  echo "$TOO_OLD" >&2
  exit 3
fi
if ! printf '%s' "$out" | grep -q "arguments are required"; then
  echo "$TOO_OLD (unrecognized AWS CLI output)" >&2
  exit 3
fi

target_status() {
  aws bedrock-agentcore-control get-gateway-target --region "$AWS_REGION" \
    --gateway-identifier "$GATEWAY_ID" --target-id "$TARGET_ID" \
    --query status --output text
}

# The API refuses a sync while authorization is pending or another sync is still running (for
# example a manual synchronize-gateway-targets): wait it out (10 s x 30).
i=0
while :; do
  status=$(target_status) || { echo "sync_gateway_target: could not read target $TARGET_ID" >&2; exit 1; }
  case "$status" in
    CREATE_PENDING_AUTH | UPDATE_PENDING_AUTH | SYNCHRONIZE_PENDING_AUTH | CREATING | UPDATING | \
      SYNCHRONIZING) ;;
    *) break ;;
  esac
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "sync_gateway_target: target $TARGET_ID still $status after 300 s; check the target in the console" >&2
    exit 1
  fi
  sleep "$POLL_SECONDS"
done

sleep "$SETTLE_SECONDS"

echo "sync_gateway_target: synchronizing target $TARGET_ID on gateway $GATEWAY_ID"
aws bedrock-agentcore-control synchronize-gateway-targets --region "$AWS_REGION" \
  --gateway-identifier "$GATEWAY_ID" --target-id-list "$TARGET_ID" >/dev/null

i=0
while [ "$i" -lt 30 ]; do
  sleep "$POLL_SECONDS"
  status=$(target_status) || { echo "sync_gateway_target: could not read target $TARGET_ID" >&2; exit 1; }
  case "$status" in
    READY)
      echo "sync_gateway_target: target $TARGET_ID is READY"
      exit 0
      ;;
    FAILED | SYNCHRONIZE_UNSUCCESSFUL)
      reasons=$(aws bedrock-agentcore-control get-gateway-target --region "$AWS_REGION" \
        --gateway-identifier "$GATEWAY_ID" --target-id "$TARGET_ID" \
        --query statusReasons --output text) || reasons="(no reasons returned)"
      echo "sync_gateway_target: target $TARGET_ID is $status: $reasons" >&2
      exit 1
      ;;
  esac
  i=$((i + 1))
done

echo "sync_gateway_target: still synchronizing after 300 s; check the target in the console" >&2
exit 1
