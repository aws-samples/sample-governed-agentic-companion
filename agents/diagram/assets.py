"""Readiness and the checksummed icon catalog at runtime (design D7, D9).

Image mode: GAC_DIAGRAM_ASSETS_DIR is set (both Dockerfiles set it); the PNG rasterizer and the
pinned DejaVu fonts are required. Local mode: the catalog in <repo>/.diagram-assets/ built by
`./run.sh diagram-assets`; the rasterizer is optional (local preview without PNG).

The runtime never parses XML: icon files are in the builder's canonical form and are only
checked (bytes + SHA-256) and embedded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .style import GENERIC_ICON_SVG, GENERIC_ICON_VIEWBOX, GROUP_ICON_KEYS

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_ASSETS_DIR = _REPO_ROOT / ".diagram-assets"
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT_FILES = (f"{FONT_DIR}/DejaVuSans.ttf", f"{FONT_DIR}/DejaVuSans-Bold.ttf")
_KEY = re.compile(r"^[A-Za-z0-9]{2,64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SVG_OPEN = re.compile(r'^<svg xmlns="http://www\.w3\.org/2000/svg" viewBox="([0-9. -]+)">')


@dataclass(frozen=True)
class Readiness:
    mode: str                 # image | local
    icons: str                # present | absent | invalid
    rasterizer: str           # available | absent
    enabled: bool
    source: str = ""          # official-aws-architecture-icons | test-fixture
    catalog_sha256: str = ""
    package_release: str = ""

    @property
    def icon_manifest_source(self) -> str:
        return f"icon-manifest:sha256:{self.catalog_sha256[:12]}" if self.catalog_sha256 else ""


@dataclass(frozen=True)
class IconRef:
    key: str
    viewbox: str
    body: str                 # inner SVG markup (no outer <svg>)
    generic: bool = False
    warn: Optional[str] = None   # DQ-I101 (unknown key) | DQ-I103 (checksum)

    @property
    def standalone(self) -> str:
        """A standalone 48 px SVG document of this icon (drawio data URIs)."""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{self.viewbox}" width="48" '
                f'height="48">{self.body}</svg>')


GENERIC = IconRef("generic", GENERIC_ICON_VIEWBOX, GENERIC_ICON_SVG, generic=True)


def resvg_importable() -> bool:
    try:
        import resvg_py  # noqa: F401
        return True
    except Exception as e:
        logger.info("diagram: resvg_py not importable (%s)", type(e).__name__)
        return False


def fonts_present() -> bool:
    return all(os.path.isfile(f) for f in FONT_FILES)


class Assets:
    """One instance per specialist; readiness is computed once."""

    def __init__(self, assets_dir: Optional[str] = None, *, enabled: bool = True):
        env_dir = os.getenv("GAC_DIAGRAM_ASSETS_DIR")
        if assets_dir is not None:
            self.mode = "image" if env_dir else "local"
            self.dir = Path(assets_dir)
        elif env_dir:
            self.mode, self.dir = "image", Path(env_dir)
        else:
            self.mode, self.dir = "local", LOCAL_ASSETS_DIR
        self.enabled = enabled
        self._catalog: Dict[str, dict] = {}
        self._icons: Dict[str, IconRef] = {}
        self._readiness: Optional[Readiness] = None
        self.catalog_failed = False

    def _load(self) -> Readiness:
        icons, source, cat_sha, release = "absent", "", "", ""
        try:
            status = json.loads((self.dir / "STATUS.json").read_text(encoding="utf-8"))
            if isinstance(status, dict) and status.get("icons") == "present":
                cat_sha = str(status.get("catalog_sha256", ""))
                raw = (self.dir / "catalog.json").read_bytes()
                if not _HEX64.match(cat_sha) or hashlib.sha256(raw).hexdigest() != cat_sha:
                    logger.warning("diagram: icon catalog failed its checksum")
                    self.catalog_failed = True
                    icons, cat_sha = "invalid", ""
                else:
                    cat = json.loads(raw.decode("utf-8"))
                    self._catalog = cat if isinstance(cat, dict) else {}
                    icons = "present"
                    source = "test-fixture" if status.get("source") == "test-fixture" else \
                        "official-aws-architecture-icons"
                    rel = str(status.get("package_release", ""))
                    release = rel if re.match(r"^[0-9]{8}$", rel) else ""
        except FileNotFoundError:
            logger.info("diagram: no icon catalog at the assets dir")
        except Exception as e:
            logger.info("diagram: icon catalog unreadable (%s)", type(e).__name__)
            icons = "absent"
        raster = resvg_importable()
        if self.mode == "image" and raster and not fonts_present():
            logger.warning("diagram: pinned DejaVu font files are missing from this image")
            raster = False
        return Readiness(self.mode, icons, "available" if raster else "absent", self.enabled,
                         source, cat_sha, release)

    def readiness(self) -> Readiness:
        if self._readiness is None:
            self._readiness = self._load()
        return self._readiness

    def color(self, key: str) -> Optional[str]:
        entry = self._catalog.get(key)
        c = entry.get("color") if isinstance(entry, dict) else None
        return c if isinstance(c, str) and re.match(r"^#[0-9A-F]{6}$", c) else None

    def has(self, key: str) -> bool:
        return key in self._catalog

    def icon(self, key: str) -> IconRef:
        """The checksummed catalog icon, or the labelled generic icon with DQ-I101 (unknown key)
        or DQ-I103 (bytes/sha256 mismatch). Never silent."""
        if key in self._icons:
            return self._icons[key]
        self.readiness()
        entry = self._catalog.get(key) if _KEY.match(key or "") else None
        if not isinstance(entry, dict):
            ref = IconRef("generic", GENERIC_ICON_VIEWBOX, GENERIC_ICON_SVG, True, "DQ-I101")
        else:
            ref = self._read(key, entry)
        self._icons[key] = ref
        return ref

    def _read(self, key: str, entry: dict) -> IconRef:
        bad = IconRef("generic", GENERIC_ICON_VIEWBOX, GENERIC_ICON_SVG, True, "DQ-I103")
        try:
            data = (self.dir / "icons" / f"{key}.svg").read_bytes()
        except Exception:
            logger.warning("diagram: icon %s unreadable", key)
            return bad
        if len(data) != entry.get("bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            logger.warning("diagram: icon %s failed its checksum", key)
            return bad
        text = data.decode("utf-8", "replace")
        m = _SVG_OPEN.match(text)
        if not m or not text.endswith("</svg>"):
            return bad
        return IconRef(key, m.group(1), text[m.end():-len("</svg>")])

    def group_icon(self, gtype: str) -> Optional[IconRef]:
        for key in GROUP_ICON_KEYS.get(gtype, ()):
            if key in self._catalog:
                ref = self.icon(key)
                return None if ref.generic else ref
        return None
