# Third-Party Licenses

This project is licensed under MIT-0 (see [`LICENSE`](LICENSE)). It depends on the open source
packages listed below, each under its own license. This file is provided for attribution; it is
not exhaustive of transitive dependencies. Run a dependency/license scan (for example
`pip-licenses`) against your resolved environment for a complete Bill of Materials.

## Core (deterministic tier)

| Package | License |
|---|---|
| PyYAML | MIT |
| pytest (dev/test only) | MIT |
| defusedxml (local icon catalog, image build, tests) | PSF-2.0 |
| python-hcl2 (dev/test only) | MIT |
| httpx (dev/test only) | BSD-3-Clause |

## Optional — cloud paths and front doors

These are only required when a cloud tier or a front door is enabled (imports are guarded).

| Package | License |
|---|---|
| boto3 | Apache-2.0 |
| botocore | Apache-2.0 |
| requests (front-door helpers) | Apache-2.0 |
| mcp (Model Context Protocol SDK) | MIT |
| bedrock-agentcore | Apache-2.0 |
| aws-opentelemetry-distro | Apache-2.0 |
| opentelemetry-api | Apache-2.0 |
| PyJWT (with `crypto` extra → cryptography) | MIT (PyJWT); Apache-2.0 OR BSD-3-Clause (cryptography) |
| uvicorn | BSD-3-Clause |

## Diagram specialist

| Component | Where | License |
|---|---|---|
| resvg-py 0.5.0 (copyright 2024 baseplate-admin) | runtime images only | MIT |
| resvg and usvg 0.48.1 (bundled in resvg-py) | runtime images only | Apache-2.0 OR MIT |
| tiny-skia (bundled) | runtime images only | BSD-3-Clause |
| 71 further Rust crates bundled in resvg-py | runtime images only | permissive (MIT, Apache-2.0, Zlib, BSD-2/3-Clause, 0BSD, Unlicense, Unicode-3.0); the complete list is in the wheel's `resvg_py-0.5.0.dist-info/sboms/resvg_py.cyclonedx.json` |
| ttf-dejavu 2.37-r9 (Wolfi): `DejaVuSans.ttf`, `DejaVuSans-Bold.ttf` | runtime images only | Bitstream Vera Fonts licence with public-domain DejaVu changes (SPDX `Bitstream-Vera`). The Wolfi package ships no separate licence file; the copyright notice and the full licence text are embedded in each font file (name table records 0 and 13) |
| defusedxml 0.7.1 | `./run.sh diagram-assets`, image build stage and tests | PSF-2.0 |
| pip 26.2.2 | image build only (used to install, then uninstalled from the final images) | MIT |

All of these are installed in the image the deployer builds; none is shipped in this repository.
No copyleft component is added.

## Runtime base image

| Component | Where | License and terms |
|---|---|---|
| Chainguard Python (`cgr.dev/chainguard/python`, `latest` and `latest-dev`, Wolfi), pinned by digest in both Dockerfiles | base of both runtime images and their build stages | Chainguard's free Starter tier, available to everyone ([Chainguard containers FAQ](https://www.chainguard.dev/docs/chainguard/containers/faq)); the deployer pulls it and pushes the built image to their own private ECR. Python itself is PSF-2.0. Trivy's licence scan of the runtime base (2026-11-02, first pin; the 2026-11-03 re-pin, Python 3.15.2, uses the same tag and terms) lists 22 notice, 12 restricted (GPL and LGPL, for example glibc), 3 reciprocal (MPL-2.0, for example ca-certificates) and 7 unknown OS-package licences |

The base image is not part of this repository and the kit never redistributes it. Its OS packages,
including the copyleft-licensed C library, come from the upstream image, not from the kit.
Read Chainguard's terms before you use the image in your organization.

**AWS Architecture Icons.** Governed by the AWS Architecture Icons terms at
<https://aws.amazon.com/architecture/icons/>. The icons are **not redistributed in this
repository**. The deployer downloads the package and, by staging it, accepts those terms for
their use. The runtime image contains a sanitized derivative catalog built on the deployer's
machine. The rendered `.drawio`, `.svg` and `.png` embed sanitized official icon artwork from that
catalog, the same way the kit's own committed figures do; they are generated in the deployer's
environment, written to `outputs/` or the developer's workspace, and never committed to the kit.
No draw.io `mxgraph.aws4` shape is used. Teams that publish rendered diagrams should check the
terms and the AWS trademark guidelines themselves.

**Diagram layout and quality rules.** The layout engine, router and quality check in
`agents/diagram/` are the kit's own code (MIT-0, like the rest of the kit). The rule table is
`MESSAGES` plus `NOT_APPLICABLE` in `agents/diagram/quality.py`; no third-party code is reused.

## CI tooling (not shipped)

These run in the GitHub Actions workflow (`.github/workflows/ci.yml`) or the local security gate
(`scripts/security_gate.sh`). None is installed in the runtime images or redistributed here.

| Tool | Where | License |
|---|---|---|
| actions/checkout, actions/setup-python, actions/upload-artifact | CI | MIT |
| hashicorp/setup-terraform | CI | MPL-2.0 |
| Terraform CLI | CI and local, tool only | BUSL-1.1 |
| Checkov, Bandit, pip-audit, detect-secrets | CI and the local gate | Apache-2.0 |
| KICS | CI and the local gate | Apache-2.0 |
| actionlint, zizmor | CI | MIT |
| Trivy, Grype | local gate only | Apache-2.0 |

License identifiers use SPDX names. Each dependency's authoritative license text is distributed
with that package. If you add or remove dependencies, update this file and re-run a license scan.
