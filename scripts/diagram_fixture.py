"""Synthetic icon fixtures for tests and the parity guard. NO AWS artwork: every icon is a plain
coloured square at the official package's path layout, so the real catalog builder can run on it.
The resulting catalog is marked `source: test-fixture`."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Iterable

REL = "07312026"
SERVICE = {
    "AmazonBedrockAgentCore": "Arch_Artificial-Intelligence/48/Arch_Amazon-Bedrock-AgentCore_48.svg",
    "AmazonBedrock": "Arch_Artificial-Intelligence/48/Arch_Amazon-Bedrock_48.svg",
    "AmazonCognito": "Arch_Security-Identity/48/Arch_Amazon-Cognito_48.svg",
    "AWSKeyManagementService": "Arch_Security-Identity/48/Arch_AWS-Key-Management-Service_48.svg",
    "AWSSecretsManager": "Arch_Security-Identity/48/Arch_AWS-Secrets-Manager_48.svg",  # pragma: allowlist secret
    "AWSIdentityAndAccessManagement":
        "Arch_Security-Identity/48/Arch_AWS-Identity-and-Access-Management_48.svg",
    "AWSLambda": "Arch_Compute/48/Arch_AWS-Lambda_48.svg",
    "AmazonElasticContainerRegistry": "Arch_Containers/48/Arch_Amazon-Elastic-Container-Registry_48.svg",
    "AmazonSimpleStorageService": "Arch_Storage/48/Arch_Amazon-Simple-Storage-Service_48.svg",
    "AmazonCloudWatch": "Arch_Management-Tools/48/Arch_Amazon-CloudWatch_48.svg",
    "AWSXRay": "Arch_Developer-Tools/48/Arch_AWS-X-Ray_48.svg",
    "AmazonSimpleQueueService": "Arch_App-Integration/48/Arch_Amazon-Simple-Queue-Service_48.svg",
    "AmazonRoute53Resolver": "Arch_Networking/48/Arch_Amazon-Route-53-Resolver_48.svg",
    "AWSPrivateLink": "Arch_Networking/48/Arch_AWS-PrivateLink_48.svg",
    "AWSClientVPN": "Arch_Networking/48/Arch_AWS-Client-VPN_48.svg",
    "AWSSiteToSiteVPN": "Arch_Networking/48/Arch_AWS-Site-to-Site-VPN_48.svg",
    "AWSDirectConnect": "Arch_Networking/48/Arch_AWS-Direct-Connect_48.svg",
}
RESOURCE = {
    "AmazonVPCEndpoints": "Res_Networking-Content-Delivery/Res_Amazon-VPC_Endpoints_48.svg",
    "AmazonVPCElasticNetworkInterface":
        "Res_Networking-Content-Delivery/Res_Amazon-VPC_Elastic-Network-Interface_48.svg",
    "AmazonSimpleStorageServiceBucket": "Res_Storage/Res_Amazon-Simple-Storage-Service_Bucket_48.svg",
    "User": "Res_General-Icons/Res_48_Light/Res_User_48_Light.svg",
    "Client": "Res_General-Icons/Res_48_Light/Res_Client_48_Light.svg",
}
GROUP = {
    "AWSCloudLogo": ("AWS-Cloud-logo_32.svg", "#242F3E"),
    "Region": ("Region_32.svg", "#00A4A6"),
    "VirtualPrivateCloudVPC": ("Virtual-private-cloud-VPC_32.svg", "#8C4FFF"),
    "PrivateSubnet": ("Private-subnet_32.svg", "#00A4A6"),
    "PublicSubnet": ("Public-subnet_32.svg", "#7AA116"),
    "CorporateDataCenter": ("Corporate-data-center_32.svg", "#7D8998"),
    "AWSAccount": ("AWS-Account_32.svg", "#E7157B"),
}


def _color(key: str) -> str:
    return "#" + hashlib.sha256(key.encode()).hexdigest()[:6].upper()


def square_svg(color: str, size: int = 64, pad_bytes: int = 0) -> bytes:
    pad = ""
    if pad_bytes:
        # low-compressibility padding so the builder's ratio cap does not trip
        digits = hashlib.sha256(color.encode()).hexdigest()
        while len(digits) < pad_bytes:
            digits += hashlib.sha256(digits[-64:].encode()).hexdigest()
        pts = " ".join(f"L{int(digits[i:i + 2], 16)} {int(digits[i + 2:i + 4], 16)}"
                       for i in range(0, pad_bytes // 2, 4))
        pad = f'<path d="M0 0 {pts}" fill="none"/>'

    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
            f'xmlns="http://www.w3.org/2000/svg"><rect width="{size}" height="{size}" '
            f'fill="{color}"/>{pad}</svg>').encode()


def fixture_zip_bytes(*, omit: Iterable[str] = (), fillers: int = 300, pad_bytes: int = 0,
                      extra: dict = None) -> bytes:
    omit = set(omit)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for key, path in sorted(SERVICE.items()):
            if key not in omit:
                z.writestr(f"Architecture-Service-Icons_{REL}/{path}",
                           square_svg(_color(key), pad_bytes=pad_bytes))
        for key, path in sorted(RESOURCE.items()):
            if key not in omit:
                z.writestr(f"Resource-Icons_{REL}/{path}", square_svg(_color(key), 48, pad_bytes))
        for key, (name, color) in sorted(GROUP.items()):
            if key not in omit:
                z.writestr(f"Architecture-Group-Icons_{REL}/{name}", square_svg(color, 40))
        for i in range(fillers):
            z.writestr(f"Architecture-Service-Icons_{REL}/Arch_Fixture/48/Arch_Fixture-Icon-{i:03d}_48.svg",
                       square_svg(_color(f"f{i}")))
        z.writestr(f"__MACOSX/Architecture-Service-Icons_{REL}/._x.svg", b"junk")
        for name, data in sorted((extra or {}).items()):
            z.writestr(name, data)
    return buf.getvalue()


def build_fixture_catalog(out: Path, **kw) -> dict:
    """Write a fixture zip into a sibling staging folder and build a test-fixture catalog."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_icon_catalog import build
    staging = Path(str(out) + "-staging")
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "fixture.zip").write_bytes(fixture_zip_bytes(**kw))
    return build(staging, out, source="test-fixture", log=lambda *_: None)
