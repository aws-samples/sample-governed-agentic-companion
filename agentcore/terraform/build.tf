# Container build + push automation.
#
# When auto_build_push_images = true, Terraform runs build_and_push.sh for each image BEFORE
# the runtime that references it is created. The build context is the REPO ROOT so the
# single-source engine (agents/, knowledge/, governance/) is COPYd directly — no staged copy.
#
# The trigger hashes the Dockerfile + entrypoint + requirements so a code change forces a
# rebuild on the next apply. Set auto_build_push_images = false to build/push out-of-band
# (e.g. in CI) and have Terraform just reference the existing :image_tag.

locals {
  build_script = "${path.module}/scripts/build_and_push.sh"
  # Diagram icons (deployer-staged, gitignored) and the diagram code: staging or replacing the
  # zip, or editing the diagram engine, forces a rebuild. A rebuild needs a NEW image_tag (ECR
  # tags are immutable). Other engine edits (agents/, knowledge/ outside the diagram KB files)
  # are not hashed here; see docs/DEPLOY.md.
  diagram_icons_dir  = "${local.repo_root}/agentcore/diagram-icons"
  diagram_icons_zips = sort(fileset(local.diagram_icons_dir, "*.zip"))
  diagram_icons_trigger = length(local.diagram_icons_zips) == 0 ? "absent" : sha1(join("", [
    for f in local.diagram_icons_zips : filesha256("${local.diagram_icons_dir}/${f}")
  ]))
  # The diagram KB (presets, vocabulary, facts) is part of what a view draws, so it is hashed too.
  diagram_code_trigger = sha1(join("", concat(
    [for f in sort(fileset(local.repo_root, "agents/diagram/**/*.py")) : filesha1("${local.repo_root}/${f}")],
    [for f in sort(fileset(local.repo_root, "knowledge/**/diagram/*.yaml")) : filesha1("${local.repo_root}/${f}")],
    [for f in sort(fileset(local.repo_root, "knowledge/**/diagram_facts.yaml")) : filesha1("${local.repo_root}/${f}")],
    [filesha1("${local.repo_root}/agents/specialists/diagram_specialist.py"),
      filesha1("${local.repo_root}/scripts/build_icon_catalog.py"),
    filesha1("${local.repo_root}/agentcore/app/diagram-assets/requirements.txt")],
  )))

  # Files whose change should force a rebuild. Kept coarse but meaningful.
  orchestrator_build_trigger = sha1(join("", [
    filesha1("${local.repo_root}/agentcore/app/orchestrator/Dockerfile"),
    filesha1("${local.repo_root}/agentcore/app/orchestrator/runtime_entrypoint.py"),
    filesha1("${local.repo_root}/agentcore/app/orchestrator/requirements.txt"),
    local.diagram_icons_trigger,
    local.diagram_code_trigger,
    var.diagram_icons_sha256,
    var.image_tag,
  ]))

  mcp_runtime_build_trigger = sha1(join("", [
    filesha1("${local.repo_root}/agentcore/app/mcp_runtime/Dockerfile"),
    filesha1("${local.repo_root}/agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py"),
    filesha1("${local.repo_root}/agentcore/app/mcp_runtime/requirements.txt"),
    local.diagram_icons_trigger,
    local.diagram_code_trigger,
    var.diagram_icons_sha256,
    var.image_tag,
  ]))
}

resource "null_resource" "build_push_orchestrator" {
  count = var.auto_build_push_images ? 1 : 0

  triggers = {
    build = local.orchestrator_build_trigger
  }

  provisioner "local-exec" {
    command     = local.build_script
    working_dir = path.module
    environment = {
      REPO_ROOT    = local.repo_root
      DOCKERFILE   = "agentcore/app/orchestrator/Dockerfile"
      ECR_REPO_URL = aws_ecr_repository.orchestrator.repository_url
      IMAGE_TAG    = var.image_tag
      AWS_REGION   = local.region
      # Optional icons-zip checksum, forwarded as a --build-arg (never echoed back in full).
      GAC_DIAGRAM_ICONS_SHA256 = var.diagram_icons_sha256
    }
  }

  depends_on = [aws_ecr_repository.orchestrator]
}

resource "null_resource" "build_push_mcp_runtime" {
  count = var.auto_build_push_images ? 1 : 0

  triggers = {
    build = local.mcp_runtime_build_trigger
  }

  provisioner "local-exec" {
    command     = local.build_script
    working_dir = path.module
    environment = {
      REPO_ROOT    = local.repo_root
      DOCKERFILE   = "agentcore/app/mcp_runtime/Dockerfile"
      ECR_REPO_URL = aws_ecr_repository.mcp_runtime.repository_url
      IMAGE_TAG    = var.image_tag
      AWS_REGION   = local.region
      # Optional icons-zip checksum, forwarded as a --build-arg (never echoed back in full).
      GAC_DIAGRAM_ICONS_SHA256 = var.diagram_icons_sha256
    }
  }

  depends_on = [aws_ecr_repository.mcp_runtime]
}
