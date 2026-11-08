"""Build the sanitized, checksummed AWS Architecture Icons catalog for the diagram specialist.

Used by the discarded `diagram-assets` stage of both runtime Dockerfiles and by
`./run.sh diagram-assets` (local preview). stdlib + defusedxml only; no network, no subprocess.

Input: a staging folder holding exactly one `*.zip` (the official AWS Architecture Icons Asset
Package as downloaded, any filename). Output folder:

    <out>/STATUS.json     icons present|absent, source, zip_sha256, catalog_sha256, count, ...
    <out>/catalog.json    {"<Key>": {kind, file, viewBox, color?, bytes, sha256}}
    <out>/icons/<Key>.svg sanitized icon bodies (canonical form, see _serialize)

No zip staged: STATUS.json says `absent`, a WARNING is printed and the exit code is 0 (the image
still builds; the specialist declines). Any unsafe or unexpected input exits 1 with `FATAL:`.
The icons are never committed to the kit (THIRD-PARTY-LICENSES.md).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BUILT_WITH = "build_icon_catalog/1"
MAX_MEMBERS = 20_000
MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024
MAX_RATIO = 100
ICON_MAX_BYTES = 16 * 1024
FLOOR = 300

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

_SERVICE = re.compile(r"^Architecture-Service-Icons_([^/]+)/Arch_[^/]+/48/Arch_([^/]+)_48\.svg$")
_RESOURCE = re.compile(r"^Resource-Icons_([^/]+)/(?:[^/]+/)*Res_([^/]+?)_48(_Light)?\.svg$")
_GROUP = re.compile(r"^Architecture-Group-Icons_([^/]+)/([^/]+)_32\.svg$")
_KEY = re.compile(r"^[A-Za-z0-9]{2,64}$")
_RELEASE = re.compile(r"^[0-9]{8}$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_NUM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

ALLOWED_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "polygon", "polyline", "line",
                "defs", "linearGradient", "radialGradient", "stop", "clipPath", "use"}
ALLOWED_ATTRS = {
    "d", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "fx", "fy", "points",
    "x1", "y1", "x2", "y2", "offset", "transform", "fill", "fill-rule", "fill-opacity",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
    "stroke-dasharray", "stroke-dashoffset", "stroke-opacity", "opacity", "clip-path",
    "clip-rule", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform",
    "spreadMethod", "clipPathUnits", "id", "href", "style", "visibility", "display",
}
STYLE_PROPS = {"fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap",
               "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "opacity",
               "stop-color", "stop-opacity", "clip-rule", "display", "visibility"}


class Fatal(Exception):
    """Unsafe or unexpected input: the build must fail."""


def _local(name: str) -> Tuple[str, str]:
    if name.startswith("{"):
        ns, _, local = name[1:].partition("}")
        return ns, local
    return "", name


def _safe_url_value(value: str) -> bool:
    low = value.lower()
    for m in re.finditer(r"url\(", low):
        rest = low[m.end():].lstrip(" '\"")
        if not rest.startswith("#"):
            return False
    return "@import" not in low and "expression(" not in low and "javascript:" not in low


def _clean_style(value: str) -> Optional[str]:
    keep = []
    for decl in value.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if prop in STYLE_PROPS and val and _safe_url_value(val) and "<" not in val:
            keep.append(f"{prop}:{val}")
    return ";".join(keep) or None


def _esc(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _clean_attrs(el) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw_name, value in el.attrib.items():
        ns, name = _local(raw_name)
        if ns not in ("", XLINK_NS) or (ns == XLINK_NS and name != "href"):
            continue
        if name not in ALLOWED_ATTRS or name.lower().startswith("on"):
            continue
        value = str(value)
        if name == "href":
            if not value.startswith("#") or not _NUM_ID.match(value[1:]):
                continue
        elif name == "id":
            if not _NUM_ID.match(value):
                continue
        elif name == "style":
            cleaned = _clean_style(value)
            if cleaned is None:
                continue
            value = cleaned
        elif not _safe_url_value(value) or "<" in value:
            continue
        out[name] = value
    return out


def _serialize(el, parts: List[str]) -> None:
    ns, tag = _local(el.tag)
    if ns not in ("", SVG_NS) or tag not in ALLOWED_TAGS or tag == "svg":
        return
    attrs = _clean_attrs(el)
    attr_text = "".join(f' {k}="{_esc(v)}"' for k, v in sorted(attrs.items()))
    children = list(el)
    if not children:
        parts.append(f"<{tag}{attr_text}/>")
        return
    parts.append(f"<{tag}{attr_text}>")
    for c in children:
        _serialize(c, parts)
    parts.append(f"</{tag}>")


def sanitize_svg(data: bytes) -> Tuple[str, str, Optional[str]]:
    """Return (canonical svg text, viewBox, first rect fill colour). Raises ValueError when the
    icon cannot be parsed safely (DTDs and entities are refused by defusedxml)."""
    from defusedxml import ElementTree as DET
    try:
        root = DET.fromstring(data, forbid_dtd=True)
    except Exception as e:  # defusedxml raises its own subclasses for DTD/entities
        raise ValueError(type(e).__name__) from None
    ns, tag = _local(root.tag)
    if tag != "svg" or ns not in ("", SVG_NS):
        raise ValueError("not an svg root")
    vb = root.attrib.get("viewBox") or ""
    if not re.match(r"^-?[0-9.]+ -?[0-9.]+ [0-9.]+ [0-9.]+$", vb):
        w = re.sub(r"px$", "", root.attrib.get("width", ""))
        h = re.sub(r"px$", "", root.attrib.get("height", ""))
        if not (re.match(r"^[0-9.]+$", w) and re.match(r"^[0-9.]+$", h)):
            raise ValueError("no viewBox")
        vb = f"0 0 {w} {h}"
    parts: List[str] = []
    for child in root:
        _serialize(child, parts)
    body = "".join(parts)
    color = None
    m = re.search(r'<rect[^>]*?fill="(#[0-9A-Fa-f]{6})"', body)
    if m and _COLOR.match(m.group(1)):
        color = m.group(1).upper()
    return f'<svg xmlns="{SVG_NS}" viewBox="{vb}">{body}</svg>', vb, color


def derive_key(stem: str) -> str:
    parts = [p for p in re.split(r"[-_]", stem) if p]
    return "".join(p[0].upper() + p[1:] for p in parts)


def _select(name: str) -> Optional[Tuple[str, str, str]]:
    """(kind, key, release) for a selected member, else None."""
    m = _SERVICE.match(name)
    if m:
        return "service", derive_key(m.group(2)), m.group(1)
    m = _RESOURCE.match(name)
    if m:
        return "resource", derive_key(m.group(2)), m.group(1)
    m = _GROUP.match(name)
    if m and not m.group(2).endswith("_Dark"):
        return "group", derive_key(m.group(2)), m.group(1)
    return None


def _write_json(path: Path, obj) -> bytes:
    data = (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


def build(staging: Path, out: Path, *, expected_sha256: str = "", zip_path: Optional[Path] = None,
          source: str = "official-aws-architecture-icons", log=print) -> dict:
    """Build the catalog. Returns the STATUS dict. Raises Fatal on unsafe/unexpected input."""
    out.mkdir(parents=True, exist_ok=True)
    if zip_path is None:
        zips = sorted(p for p in staging.glob("*.zip") if p.is_file())
        if not zips:
            status = {"icons": "absent", "built_with": BUILT_WITH}
            _write_json(out / "STATUS.json", status)
            log("WARNING: AWS Architecture Icons not staged; the diagram specialist will decline "
                "in this image (see docs/DEPLOY.md)")
            return status
        if len(zips) > 1:
            raise Fatal("more than one .zip in agentcore/diagram-icons/; keep exactly one (the AWS "
                        "Architecture Icons Asset Package)")
        zip_path = zips[0]
    raw = zip_path.read_bytes()
    zip_sha = hashlib.sha256(raw).hexdigest()
    expected = (expected_sha256 or "").strip().lower()
    if expected and expected != zip_sha:
        raise Fatal(f"icons zip sha256 mismatch (expected {expected[:12]}..., got {zip_sha[:12]}...)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise Fatal("the staged file is not a zip archive") from None
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise Fatal(f"zip has more than {MAX_MEMBERS} members")
    if sum(i.file_size for i in infos) > MAX_TOTAL_UNCOMPRESSED:
        raise Fatal("zip total uncompressed size is over 300 MB")
    selected: List[Tuple[str, str, str, zipfile.ZipInfo]] = []
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
            raise Fatal("unsafe member path in zip (absolute, '..' or backslash)")
        base = name.rsplit("/", 1)[-1]
        if info.is_dir() or name.startswith("__MACOSX/") or base.startswith("._"):
            continue
        if not name.lower().endswith(".svg"):
            continue
        sel = _select(name)
        if sel is None:
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            raise Fatal("a selected icon is over 256 KB")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise Fatal("a selected icon has a compression ratio over 100")
        selected.append((sel[0], sel[1], sel[2], info))
    order = {"service": 0, "resource": 1, "group": 2}
    selected.sort(key=lambda s: (order[s[0]], s[3].filename))
    icons_dir = out / "icons"
    icons_dir.mkdir(exist_ok=True)
    catalog: Dict[str, dict] = {}
    releases = set()
    skipped = {"parse": 0, "size": 0, "key": 0, "duplicate": 0}
    for kind, key, release, info in selected:
        if not _KEY.match(key):
            skipped["key"] += 1
            continue
        if key in catalog:
            skipped["duplicate"] += 1
            continue
        with zf.open(info) as fh:
            data = fh.read(MAX_MEMBER_BYTES + 1)
        if len(data) > MAX_MEMBER_BYTES:
            raise Fatal("a selected icon is over 256 KB")
        try:
            text, vb, color = sanitize_svg(data)
        except ValueError:
            skipped["parse"] += 1
            continue
        body = text.encode("utf-8")
        if len(body) > ICON_MAX_BYTES:
            skipped["size"] += 1
            continue
        (icons_dir / f"{key}.svg").write_bytes(body)
        entry = {"kind": kind, "file": f"icons/{key}.svg", "viewBox": vb, "bytes": len(body),
                 "sha256": hashlib.sha256(body).hexdigest()}
        if color:
            entry["color"] = color
        catalog[key] = entry
        rm = re.search(r"([0-9]{8})$", release)
        if rm:
            releases.add(rm.group(1))
    if len(catalog) < FLOOR:
        raise Fatal(f"unexpected zip layout: only {len(catalog)} icons accepted (floor {FLOOR}); "
                    "confirm it is the official AWS Architecture Icons Asset Package")
    cat_bytes = _write_json(out / "catalog.json", catalog)
    release = sorted(releases)[-1] if len(releases) >= 1 else ""
    status = {
        "icons": "present", "source": source, "zip_sha256": zip_sha, "count": len(catalog),
        "built_with": BUILT_WITH, "catalog_sha256": hashlib.sha256(cat_bytes).hexdigest(),
        "package_release": release if _RELEASE.match(release or "") else "", "skipped": skipped,
    }
    _write_json(out / "STATUS.json", status)
    log(f"icon catalog: {len(catalog)} icons accepted (skipped {skipped}); zip sha256 "
        f"{zip_sha[:12]}..., package release {status['package_release'] or 'unknown'}")
    return status


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--staging", default="agentcore/diagram-icons",
                   help="folder holding exactly one AWS Architecture Icons zip")
    p.add_argument("--out", required=True, help="output folder for the catalog")
    p.add_argument("--zip", default="", help="use this zip instead of scanning --staging")
    p.add_argument("--expected-sha256", default=os.getenv("GAC_DIAGRAM_ICONS_SHA256", ""),
                   help="optional SHA-256 the zip must match")
    a = p.parse_args(argv)
    try:
        build(Path(a.staging), Path(a.out), expected_sha256=a.expected_sha256,
              zip_path=Path(a.zip) if a.zip else None)
    except Fatal as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
