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

## Optional — cloud paths and front doors

These are only required when a cloud tier or a front door is enabled (imports are guarded).

| Package | License |
|---|---|
| boto3 | Apache-2.0 |
| mcp (Model Context Protocol SDK) | MIT |
| bedrock-agentcore | Apache-2.0 |
| aws-opentelemetry-distro | Apache-2.0 |
| opentelemetry-api | Apache-2.0 |
| PyJWT (with `crypto` extra → cryptography) | MIT (PyJWT); Apache-2.0 OR BSD-3-Clause (cryptography) |
| uvicorn | BSD-3-Clause |

License identifiers use SPDX names. Each dependency's authoritative license text is distributed
with that package. If you add or remove dependencies, update this file and re-run a license scan.
