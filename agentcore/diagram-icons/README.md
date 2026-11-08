# AWS Architecture Icons staging folder

The diagram specialist draws with the official AWS Architecture Icons. The icons are **never**
committed to this repository. You download them and stage them here; the image build turns them
into a sanitized, checksummed catalog inside the runtime images.

## What to stage

- Download the AWS Architecture Icons **Asset Package** zip yourself from
  <https://aws.amazon.com/architecture/icons/>. Read the terms on that page; by staging the package
  you accept them for your use.
- Put **exactly one** `.zip` in this folder, with any filename (the AWS download name changes by
  release). Two or more zips fail the build: `build_and_push.sh` (the Terraform build path) stops first with
  `ERROR: more than one .zip in agentcore/diagram-icons/`; a manual `docker build` fails in the
  catalog stage with `FATAL: more than one .zip in agentcore/diagram-icons/; ...`.
- Do not unzip it. The build reads it member by member and keeps only sanitized 48 px service,
  resource and 32 px group icons.

Everything in this folder except this README is gitignored. Never commit the zip or any icon.

## Optional checksum

AWS does not publish a checksum for the zip. If you want the build to refuse any other file, set
the SHA-256 of your download (64 lowercase hex characters):

- Terraform: `diagram_icons_sha256 = "<sha256-hex>"`
- Manual build: `GAC_DIAGRAM_ICONS_SHA256=<sha256-hex> agentcore/terraform/scripts/build_and_push.sh`

A mismatch fails the build with `FATAL: icons zip sha256 mismatch`.

## No icons staged

The images still build. The diagram specialist stays registered and declines honestly ("the AWS
Architecture Icons were not staged when this image was built"). See `docs/DEPLOY.md`, "Stage the
AWS Architecture Icons".

For a local preview from the CLI, build a local catalog with
`./run.sh diagram-assets --zip /path/to/the-asset-package.zip` (written to the gitignored
`.diagram-assets/`).
