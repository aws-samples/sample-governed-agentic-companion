"""The resvg rasterizer (design D2, D6.7; acceptance 9)."""

import hashlib
import struct
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("resvg_py")

from agents.diagram import raster as R  # noqa: E402

OK = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" width="100" height="50">'
      '<rect x="0" y="0" width="100" height="50" fill="#FFFFFF"/><path d="M10 10 L90 40" '
      'stroke="#232F3E" stroke-width="2"/></svg>')


@pytest.mark.parametrize("bad", [
    '<image href="file:///etc/passwd"/>',
    '<use href="http://169.254.169.254/latest"/>',
    '<style>@import url(x.css);</style>',
    '<rect fill="url(http://evil/x)"/>',
    '<foreignObject><p/></foreignObject>',
    '<script>x</script>',
    '<rect onload="x()"/>',
])
def test_refused_before_resvg(bad):
    calls = []
    svg = OK.replace("</svg>", bad + "</svg>")
    r = R.rasterize(svg, 200, image_mode=False, deadline=1e18,
                    render=lambda *a: calls.append(a) or b"")
    assert r.png is None and r.reason == "png_refused" and calls == []


def test_png_signature_width_and_determinism():
    a = R.rasterize(OK, 200, image_mode=False, deadline=1e18)
    b = R.rasterize(OK, 200, image_mode=False, deadline=1e18)
    assert a.png.startswith(R.PNG_SIG)
    assert struct.unpack(">I", a.png[16:20])[0] == 200
    assert hashlib.sha256(a.png).hexdigest() == hashlib.sha256(b.png).hexdigest()


def test_busy():
    assert R._BUSY.acquire(blocking=False)
    try:
        assert R.rasterize(OK, 200, image_mode=False, deadline=1e18).reason == "png_busy"
    finally:
        R._BUSY.release()


def test_timeout_after_call_with_fake_clock():
    ticks = iter([0.0, 100.0])
    r = R.rasterize(OK, 200, image_mode=False, deadline=50.0, clock=lambda: next(ticks))
    assert r.reason == "png_timeout"


def test_wrong_width_and_failure():
    assert R.rasterize(OK, 200, image_mode=False, deadline=1e18,
                       render=lambda *a: R.PNG_SIG + b"\0" * 4 + b"IHDR" + struct.pack(">I", 7)
                       + b"\0" * 8).reason == "png_failed"

    def boom(*a):
        raise RuntimeError("x")
    assert R.rasterize(OK, 200, image_mode=False, deadline=1e18, render=boom).reason == "png_failed"
    assert not R._BUSY.locked()


def test_image_mode_kwargs(monkeypatch):
    seen = {}
    import resvg_py

    def fake(**kw):
        seen.update(kw)
        return R.PNG_SIG + b"\0" * 4 + b"IHDR" + struct.pack(">I", 200) + b"\0" * 8
    monkeypatch.setattr(resvg_py, "svg_to_bytes", fake)
    r = R.rasterize(OK, 200, image_mode=True, deadline=1e18)
    assert r.png is not None
    assert seen["skip_system_fonts"] is True
    assert seen["font_files"] == ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    assert seen["resources_dir"] is None


def test_single_module_executor():
    assert R._EXECUTOR._max_workers == 1
    assert isinstance(R._BUSY, type(threading.Lock()))
