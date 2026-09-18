"""Tier-B curated KB overlay (image baseline + optional S3 overlay).

The curated KB is split by update velocity:
  - Tier A (locked): stays in the repo/image, code-reviewed only, NEVER overlaid.
  - Tier B (medium-velocity): may be overridden/extended by an S3 overlay at load time,
    through a human-reviewed promotion, updatable without a redeploy.

The overlay FAILS OPEN to the image baseline: a missing/unreachable/broken overlay degrades to
the baked-in KB, never to an empty KB (the KB is inside the grounding path).

Opt-in via env; default OFF = byte-for-byte the repo baseline, so local/dev/tests are unchanged.
  GAC_KB_OVERLAY=s3
  GAC_KB_BUCKET=<bucket>            (bucket name embeds the account id -> env, never committed)
  GAC_KB_OVERLAY_KEY=kb/curated/overlay.json   (an object mapping knowledge-key -> data)
  GAC_AWS_REGION=<region>

Tier membership is an EXPLICIT allowlist (below), so a file can never silently become
cloud-mutable. Only keys under an allowlisted prefix are accepted from the overlay.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict

logger = logging.getLogger(__name__)

# Only keys under these prefixes may be supplied by the S3 overlay (Tier B). Anything else in
# the overlay is ignored — the locked Tier-A knowledge can never be overridden from S3.
_TIER_B_PREFIXES = ("tier_b/",)


def _is_tier_b(key: str) -> bool:
    return key.startswith(_TIER_B_PREFIXES)


def apply_overlay(baseline: Dict[str, dict]) -> Dict[str, dict]:
    """Merge the S3 Tier-B overlay on top of the baseline; fail open to the baseline."""
    if os.getenv("GAC_KB_OVERLAY") != "s3":
        return baseline
    bucket = os.getenv("GAC_KB_BUCKET")
    if not bucket:
        return baseline
    key = os.getenv("GAC_KB_OVERLAY_KEY", "kb/curated/overlay.json")
    region = os.getenv("GAC_AWS_REGION")
    try:
        import boto3  # guarded — only needed on the overlay path
        s3 = boto3.client("s3", region_name=region)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        overlay = json.loads(body)
    except Exception as e:
        logger.warning("KB overlay unavailable (%s); using image baseline", type(e).__name__)
        return baseline  # FAIL OPEN

    merged = dict(baseline)
    applied = skipped = 0
    for k, v in overlay.items():
        if _is_tier_b(k):
            merged[k] = v  # override matching / add new
            applied += 1
        else:
            skipped += 1  # a non-Tier-B key in the overlay is ignored (never overrides Tier A)
    logger.info("KB overlay applied: %d Tier-B keys merged, %d non-Tier-B keys ignored",
                applied, skipped)
    return merged
