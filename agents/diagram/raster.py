"""SVG to PNG with resvg-py, in-process (design D2, D6.7). No subprocess, no network.

One module-level single-worker executor and a non-blocking busy lock: a render never queues
behind another (`png_busy`). Before every call the SVG must be self-contained (no <image>,
<foreignObject>, <script>, @import, external url( or href); anything else is refused
(`png_refused`, an emitter bug). A running raster cannot be killed: the elapsed time is checked
after the call, and the input bounds keep it short.
"""

from __future__ import annotations

import logging
import re
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable, Optional

from .assets import FONT_DIR

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gac-raster")
_BUSY = threading.Lock()
PNG_SIG = b"\x89PNG\r\n\x1a\n"

_FORBIDDEN = [re.compile(p, re.I) for p in (r"<image\b", r"<foreignObject\b", r"<script\b",
                                             r"@import", r"\bon[a-z]+\s*=")]
_URL = re.compile(r"url\(\s*['\"]?([^)'\"\s]*)", re.I)
_HREF = re.compile(r"\b(?:xlink:)?href\s*=\s*['\"]([^'\"]*)['\"]", re.I)


def self_contained(svg: str) -> bool:
    if any(p.search(svg) for p in _FORBIDDEN):
        return False
    if any(not m.group(1).startswith("#") for m in _URL.finditer(svg)):
        return False
    return all(m.group(1).startswith("#") for m in _HREF.finditer(svg))


@dataclass
class RasterResult:
    png: Optional[bytes]
    reason: str = ""      # "" | png_refused | png_busy | png_timeout | png_failed | png_unavailable


def _render(svg: str, width: int, image_mode: bool) -> bytes:
    import resvg_py
    if image_mode:
        out = resvg_py.svg_to_bytes(
            svg_string=svg, width=width, skip_system_fonts=True,
            font_files=[f"{FONT_DIR}/DejaVuSans.ttf", f"{FONT_DIR}/DejaVuSans-Bold.ttf"],
            font_family="DejaVu Sans", sans_serif_family="DejaVu Sans",
            resources_dir=None, background=None,
        )
    else:
        out = resvg_py.svg_to_bytes(svg_string=svg, width=width, resources_dir=None,
                                    background=None)
    return bytes(out)


def rasterize(svg: str, width: int, *, image_mode: bool, deadline: float,
              clock: Callable[[], float] = time.monotonic,
              render: Callable[[str, int, bool], bytes] = _render) -> RasterResult:
    if not self_contained(svg):
        logger.error("diagram: SVG is not self-contained; PNG refused (emitter bug)")
        return RasterResult(None, "png_refused")
    if not _BUSY.acquire(blocking=False):
        logger.warning("diagram: rasterizer busy; PNG omitted")
        return RasterResult(None, "png_busy")
    remaining = deadline - clock()
    if remaining <= 0:
        _BUSY.release()
        return RasterResult(None, "png_timeout")

    def job() -> bytes:
        try:
            return render(svg, width, image_mode)
        finally:
            _BUSY.release()

    try:
        fut = _EXECUTOR.submit(job)
    except Exception:
        _BUSY.release()
        return RasterResult(None, "png_failed")
    try:
        png = fut.result(timeout=min(remaining, 60.0))
    except FutureTimeout:
        logger.warning("diagram: raster exceeded the render deadline")
        return RasterResult(None, "png_timeout")
    except Exception as e:
        logger.warning("diagram: raster failed (%s)", type(e).__name__)
        return RasterResult(None, "png_failed")
    if clock() > deadline:
        return RasterResult(None, "png_timeout")
    if not png.startswith(PNG_SIG) or len(png) < 24:
        return RasterResult(None, "png_failed")
    (ihdr_w,) = struct.unpack(">I", png[16:20])
    if ihdr_w != width:
        return RasterResult(None, "png_failed")
    return RasterResult(png)
