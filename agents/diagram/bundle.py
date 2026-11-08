"""The bounded `gac.diagram/1` payload and its degrade path (design D12).

wire_bytes reproduces what the MCP runtime emits for the result (one copy, because draw_diagram
is registered with structured_output=False): the inner JSON string, escaped again by the JSON-RPC
layer, plus 64 KiB for the envelope. The degrade path is measured, not estimated:
1) PNG over its cap or wire over budget -> re-rasterize at 1x; 2) still over -> omit the PNG
(png_over_limit); 3) still over -> no artefacts.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .limits import Limits, PAYLOAD_FINDINGS_CAP

SCHEMA = "gac.diagram/1"
WIRE_COPIES = 1
ENVELOPE_BYTES = 65_536
FOOTER_ALLOWANCE = 4_096

MEDIA = {
    "drawio": ("application/vnd.jgraph.mxfile", "utf-8"),
    "svg": ("image/svg+xml", "utf-8"),
    "png": ("image/png", "base64"),
    "report.json": ("application/json", "utf-8"),
}
CAP_KEY = {"drawio": "drawio", "svg": "svg", "png": "png", "report.json": "report"}
ORDER = ("drawio", "svg", "png", "report.json")


def wire_bytes(text: str, bundle: Optional[dict]) -> int:
    inner = json.dumps({"response": text, "diagram": bundle}, separators=(",", ":"))
    return WIRE_COPIES * len(json.dumps(inner)) + ENVELOPE_BYTES


def artefact(file: str, ext: str, data: bytes) -> dict:
    media, enc = MEDIA[ext]
    content = base64.b64encode(data).decode("ascii") if enc == "base64" else data.decode("utf-8")
    return {"file": file, "media_type": media, "encoding": enc, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "content": content}


@dataclass
class BundleResult:
    bundle: Optional[dict]
    artefacts: List[Tuple[str, int, str]] = field(default_factory=list)   # file, bytes, sha12
    omitted: List[Tuple[str, str]] = field(default_factory=list)
    too_large: bool = False


def build_bundle(*, name: str, draft: bool, quality: dict, traceability: dict, sources: List[str],
                 files: Dict[str, bytes], limits: Limits, text_estimate: str,
                 rerender_png_1x: Optional[Callable[[], Optional[bytes]]] = None,
                 omitted: Optional[List[Tuple[str, str]]] = None,
                 wire_budget: bool = True) -> BundleResult:
    """`files` maps extension -> decoded bytes. `omitted` carries earlier omissions (for example
    a PNG the rasterizer could not produce)."""
    base = f"{name}-draft" if draft else name
    omitted = list(omitted or [])
    present: Dict[str, bytes] = {}
    for ext in ORDER:
        if ext not in files:
            continue
        data = files[ext]
        if len(data) > limits.max_artifact_bytes[CAP_KEY[ext]]:
            if ext == "png" and rerender_png_1x is not None:
                again = rerender_png_1x()
                if again is not None and len(again) <= limits.max_artifact_bytes["png"]:
                    present[ext] = again
                    continue
            omitted.append((f"{base}.{ext}", f"{CAP_KEY[ext]}_over_limit"))
            continue
        present[ext] = data
    q = dict(quality)
    findings = list(q.get("findings", []))
    if len(findings) > PAYLOAD_FINDINGS_CAP:
        q["findings"] = findings[:PAYLOAD_FINDINGS_CAP]
        q["truncated"] = True

    def make() -> dict:
        arts = [artefact(f"{base}.{ext}", ext, present[ext]) for ext in ORDER if ext in present]
        return {"schema": SCHEMA, "name": name, "draft": draft, "quality": q,
                "traceability": traceability, "sources": sources, "artifacts": arts,
                "omitted": [{"file": f, "reason": r} for f, r in omitted],
                "limits": {"max_payload_bytes": limits.max_payload_bytes, "wire_bytes": 0,
                           "max_artifact_bytes": dict(limits.max_artifact_bytes)}}

    bundle = make()
    over = lambda b: wire_budget and wire_bytes(text_estimate + " " * FOOTER_ALLOWANCE, b) > limits.max_payload_bytes  # noqa: E731
    if over(bundle) and "png" in present and rerender_png_1x is not None:
        again = rerender_png_1x()
        if again is not None:
            present["png"] = again
            bundle = make()
    if over(bundle) and "png" in present:
        del present["png"]
        omitted.append((f"{base}.png", "png_over_limit"))
        bundle = make()
    too_large = False
    if over(bundle):
        for ext in list(present):
            omitted.append((f"{base}.{ext}", "payload_over_limit"))
        present.clear()
        too_large = True
        bundle = make()
    bundle["limits"]["wire_bytes"] = wire_bytes(text_estimate, bundle)
    arts = [(a["file"], a["bytes"], a["sha256"][:12]) for a in bundle["artifacts"]]
    if not bundle["artifacts"]:
        return BundleResult(None, [], omitted, too_large)
    return BundleResult(bundle, arts, omitted, too_large)


@dataclass
class ViewFiles:
    """One view's input to build_bundles (the same fields build_bundle takes)."""
    name: str
    draft: bool
    quality: dict
    traceability: dict
    sources: List[str]
    files: Dict[str, bytes]
    rerender_png_1x: Optional[Callable[[], Optional[bytes]]] = None
    omitted: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class BundlesResult:
    bundles: List[dict]
    artefacts: List[List[Tuple[str, int, str]]]
    omitted: List[List[Tuple[str, str]]]
    #: names of trailing views dropped by the wire budget (views_over_limit)
    dropped: List[str] = field(default_factory=list)
    too_large: bool = False


def wire_bytes_multi(text: str, bundles: List[dict]) -> int:
    """The MCP result for several views: `diagram` is view 1, `diagram_views` the rest
    (design D7.3); one copy, escaped again by the JSON-RPC layer, plus the envelope."""
    doc = {"response": text, "diagram": bundles[0] if bundles else None}
    if len(bundles) > 1:
        doc["diagram_views"] = bundles[1:]
    inner = json.dumps(doc, separators=(",", ":"))
    return WIRE_COPIES * len(json.dumps(inner)) + ENVELOPE_BYTES


def build_bundles(views: List[ViewFiles], *, limits: Limits, text_estimate: str,
                  wire_budget: bool = True) -> BundlesResult:
    """One wire budget across every view (design D7.3). A single view is exactly
    build_bundle(). Several views degrade in this order: every PNG at 1x; drop every PNG
    (png_over_limit); drop trailing views, never view 1 (views_over_limit); nothing (too
    large)."""
    if len(views) == 1:
        v = views[0]
        r = build_bundle(name=v.name, draft=v.draft, quality=v.quality,
                         traceability=v.traceability, sources=v.sources, files=v.files,
                         limits=limits, text_estimate=text_estimate,
                         rerender_png_1x=v.rerender_png_1x, omitted=v.omitted,
                         wire_budget=wire_budget)
        return BundlesResult([r.bundle] if r.bundle else [], [r.artefacts], [r.omitted],
                             [], r.too_large)
    # per-artefact caps first, view by view (no wire budget yet)
    states = []
    for v in views:
        r = build_bundle(name=v.name, draft=v.draft, quality=v.quality,
                         traceability=v.traceability, sources=v.sources, files=v.files,
                         limits=limits, text_estimate=text_estimate,
                         rerender_png_1x=v.rerender_png_1x, omitted=v.omitted,
                         wire_budget=False)
        base = f"{v.name}-draft" if v.draft else v.name
        present = {ext: v.files[ext] for ext in ORDER if ext in v.files
                   and not any(f == f"{base}.{ext}" for f, _ in r.omitted)}
        if "png" in present and r.bundle is not None:
            png = next((a for a in r.bundle["artifacts"] if a["file"] == f"{base}.png"), None)
            if png is not None:
                present["png"] = base64.b64decode(png["content"])
        states.append({"view": v, "base": base, "present": present, "omitted": list(r.omitted),
                       "quality": (r.bundle or {}).get("quality", v.quality)})

    def make(s) -> dict:
        v, base, present = s["view"], s["base"], s["present"]
        arts = [artefact(f"{base}.{ext}", ext, present[ext]) for ext in ORDER if ext in present]
        return {"schema": SCHEMA, "name": v.name, "draft": v.draft, "quality": s["quality"],
                "traceability": v.traceability, "sources": v.sources, "artifacts": arts,
                "omitted": [{"file": f, "reason": r} for f, r in s["omitted"]],
                "limits": {"max_payload_bytes": limits.max_payload_bytes, "wire_bytes": 0,
                           "max_artifact_bytes": dict(limits.max_artifact_bytes)}}

    live = list(states)
    pad = text_estimate + " " * FOOTER_ALLOWANCE

    def over() -> bool:
        return wire_budget and wire_bytes_multi(pad, [make(s) for s in live]) > \
            limits.max_payload_bytes

    if over():
        for s in live:
            rr = s["view"].rerender_png_1x
            if "png" in s["present"] and rr is not None:
                again = rr()
                if again is not None:
                    s["present"]["png"] = again
    if over():
        for s in live:
            if "png" in s["present"]:
                del s["present"]["png"]
                s["omitted"].append((f"{s['base']}.png", "png_over_limit"))
    dropped: List[str] = []
    while over() and len(live) > 1:
        gone = live.pop()
        dropped.append(gone["view"].name)
    too_large = False
    if over():
        s = live[0]
        for ext in list(s["present"]):
            s["omitted"].append((f"{s['base']}.{ext}", "payload_over_limit"))
        s["present"].clear()
        too_large = True
    bundles = [make(s) for s in live]
    total = wire_bytes_multi(text_estimate, bundles)
    for b in bundles:
        b["limits"]["wire_bytes"] = total
    arts = [[(a["file"], a["bytes"], a["sha256"][:12]) for a in b["artifacts"]] for b in bundles]
    oms = [list(s["omitted"]) for s in live]
    if too_large or not bundles or not bundles[0]["artifacts"]:
        return BundlesResult([], arts, oms, dropped, True)
    return BundlesResult(bundles, arts, oms, dropped, False)


def compact_manifest(bundle_or_none: Optional[dict], *, name: str, draft: bool, score: float,
                     artefacts: List[Tuple[str, int, str]], omitted: List[Tuple[str, str]]) -> dict:
    """The small `diagram_manifest` object (no content, <= 1 KiB)."""
    report = next((b for f, b, _ in artefacts if f.endswith(".report.json")), 0)
    return {"name": name, "draft": draft, "score": score,
            "artifacts": [{"file": f, "bytes": b, "sha256": h} for f, b, h in artefacts],
            "report_bytes": report,
            "omitted": [{"file": f, "reason": r} for f, r in omitted][:4]}
