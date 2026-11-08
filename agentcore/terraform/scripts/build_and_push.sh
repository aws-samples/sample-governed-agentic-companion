#!/usr/bin/env bash
# build_and_push.sh — build one runtime image from the repo-root context and push to ECR.
#
# Invoked by Terraform's null_resource.build_push_* provisioners with these env vars:
#   REPO_ROOT     absolute path to the repo root (the Docker build context)
#   DOCKERFILE    Dockerfile path RELATIVE to REPO_ROOT (e.g. agentcore/app/orchestrator/Dockerfile)
#   ECR_REPO_URL  the target ECR repository URL (…dkr.ecr.<region>.amazonaws.com/<repo>)
#   IMAGE_TAG     the tag to push (e.g. a git short SHA, or 'latest')
#   AWS_REGION    region for the ECR login
#   GAC_DIAGRAM_ICONS_SHA256  optional SHA-256 the staged AWS Architecture Icons zip must match
#
# GOVERNANCE (Tenet 1): this builds+pushes an IMAGE ARTIFACT only. It performs no deployment
# and mutates no running environment — the runtime is created by Terraform, applied by a human.

set -euo pipefail

: "${REPO_ROOT:?REPO_ROOT is required}"
: "${DOCKERFILE:?DOCKERFILE is required}"
: "${ECR_REPO_URL:?ECR_REPO_URL is required}"
: "${IMAGE_TAG:?IMAGE_TAG is required}"
: "${AWS_REGION:?AWS_REGION is required}"

REGISTRY="${ECR_REPO_URL%%/*}"   # strip the /repo suffix -> the registry host
IMAGE="${ECR_REPO_URL}:${IMAGE_TAG}"

command -v docker >/dev/null 2>&1 || { echo "docker not found on PATH." >&2; exit 1; }
command -v aws    >/dev/null 2>&1 || { echo "aws CLI not found on PATH." >&2; exit 1; }

echo "==> Region:     $AWS_REGION"
echo "==> Context:    $REPO_ROOT"
echo "==> Dockerfile: $DOCKERFILE"
echo "==> Image:      $IMAGE"

# Diagram icons: the deployer stages exactly one AWS Architecture Icons zip (any name) in
# agentcore/diagram-icons/ (gitignored). None staged is fine (the diagram specialist declines);
# two or more is refused before anything is built.
ICONS_DIR="$REPO_ROOT/agentcore/diagram-icons"
shopt -s nullglob
ICON_ZIPS=("$ICONS_DIR"/*.zip)
shopt -u nullglob
if [ "${#ICON_ZIPS[@]}" -gt 1 ]; then
  echo "ERROR: more than one .zip in agentcore/diagram-icons/" >&2
  exit 1
elif [ "${#ICON_ZIPS[@]}" -eq 1 ]; then
  ICON_SHA="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:12])" "${ICON_ZIPS[0]}")"
  echo "==> Diagram icons: staged ($(basename "${ICON_ZIPS[0]}"), sha256 $ICON_SHA)"
else
  echo "==> Diagram icons: NOT staged; the diagram specialist will decline in this image (docs/DEPLOY.md)"
fi
echo "==> Remember: set a new image_tag for this build (ECR tags are immutable)"
BUILD_ARGS=(--pull)
if [ -n "${GAC_DIAGRAM_ICONS_SHA256:-}" ]; then
  BUILD_ARGS+=(--build-arg "GAC_DIAGRAM_ICONS_SHA256=${GAC_DIAGRAM_ICONS_SHA256}")
fi

# Use a per-invocation docker config dir that is a COPY of the user's real config (so Docker
# Desktop contexts, buildx builders, and daemon connection are all preserved) but with the
# credential helper (credsStore) STRIPPED. That stores the ECR token as plain text in this
# throwaway dir instead of the macOS keychain, avoiding the shared-keychain race on concurrent
# builds. The user's ~/.docker is untouched.
export DOCKER_CONFIG
DOCKER_CONFIG="$(mktemp -d "${TMPDIR:-/tmp}/ecrcfg.XXXXXX")"
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
SRC_CFG="${HOME}/.docker/config.json"
if [ -f "$SRC_CFG" ] && command -v python3 >/dev/null 2>&1; then
  python3 -c "import json,sys; c=json.load(open('$SRC_CFG')); c.pop('credsStore',None); c.pop('credHelpers',None); json.dump(c,open('$DOCKER_CONFIG/config.json','w'))" \
    || echo '{}' > "$DOCKER_CONFIG/config.json"
else
  echo '{}' > "$DOCKER_CONFIG/config.json"
fi
if [ -d "${HOME}/.docker/contexts" ]; then
  cp -R "${HOME}/.docker/contexts" "$DOCKER_CONFIG/contexts" 2>/dev/null || true
fi

echo "==> ECR login…"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> Building (linux/arm64 — AgentCore Runtime requires arm64)…"
# AgentCore Runtime requires a single-arch arm64 image. Use buildx when available (single-arch
# manifest + push in one step); fall back to the legacy builder, which produces a single-arch
# manifest natively (no --provenance flag — legacy rejects it).
if docker buildx version >/dev/null 2>&1; then
  docker buildx build \
    "${BUILD_ARGS[@]}" \
    --platform linux/arm64 \
    --provenance=false \
    -f "$REPO_ROOT/$DOCKERFILE" \
    -t "$IMAGE" \
    --push \
    "$REPO_ROOT"
  echo "==> Done (buildx, pushed): $IMAGE"
else
  docker build \
    "${BUILD_ARGS[@]}" \
    --platform linux/arm64 \
    -f "$REPO_ROOT/$DOCKERFILE" \
    -t "$IMAGE" \
    "$REPO_ROOT"
  echo "==> Pushing…"
  docker push "$IMAGE"
  echo "==> Done (legacy builder, pushed): $IMAGE"
fi
