"""Shared local writer for diagram artefacts (stdlib only; design D13.4).

Used by the stdio bridge (`draw_diagram`), `./run.sh diagram` and `./run.sh diagram-fetch`. It
verifies every artefact (schema, names, media types, sizes, SHA-256) before writing anything,
writes only under a proven workspace root, refuses traversal, symlinks and overwrites unless
asked, writes atomically (temp file + fsync + os.replace, with an O_EXCL claim when not
overwriting). On any failure it removes the files this call wrote; with overwrite=true a file it
had already replaced is removed, not restored. It returns only the paths it
actually wrote. It never runs a command and never opens a socket.

Env (the developer's own machine; never an MCP tool argument):
    GAC_DIAGRAM_WORKSPACE_ROOT           the project folder to write under (absolute)
    GAC_DIAGRAM_LOCAL_DIR                output folder (default <root>/outputs/diagrams)
    GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE  "true" to allow an output folder outside the root
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("companion.frontdoor.diagram_writer")

SCHEMA = "gac.diagram/1"
REPORT_SCHEMA = "gac.diagram-report/1"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CAPS = {"drawio": 2 * 1024 * 1024, "svg": 1536 * 1024, "png": 1024 * 1024,
        "report.json": 256 * 1024}
MEDIA = {"drawio": ("application/vnd.jgraph.mxfile", "utf-8"), "svg": ("image/svg+xml", "utf-8"),
         "png": ("image/png", "base64"), "report.json": ("application/json", "utf-8")}
TOTAL_CAP = 4 * 1024 * 1024
UPSTREAM_CAP = TOTAL_CAP + 1024 * 1024
OVERSIZE = ("Not saved: the upstream response exceeded the size limit (5 MiB); nothing was "
            "parsed or written.")
MARKERS = (".git", ".kiro", ".claude")


class WriteRefused(Exception):
    """A rule refused the write; the message is safe to show (no file content)."""


def _ext_of(file: str, base: str) -> Optional[str]:
    for ext in MEDIA:
        if file == f"{base}.{ext}":
            return ext
    return None


def validate_payload(diagram: dict) -> Dict[str, bytes]:
    """Return {file: decoded bytes} or raise WriteRefused. All-or-nothing."""
    if not isinstance(diagram, dict) or diagram.get("schema") != SCHEMA:
        raise WriteRefused("Not saved: the response is not a gac.diagram/1 payload.")
    name = diagram.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise WriteRefused("Not saved: the diagram name is not a valid file name.")
    base = f"{name}-draft" if diagram.get("draft") is True else name
    arts = diagram.get("artifacts")
    if not isinstance(arts, list) or not 1 <= len(arts) <= 4:
        raise WriteRefused("Not saved: the payload has no valid artefact list.")
    out: Dict[str, bytes] = {}
    seen = set()
    total = 0
    for a in arts:
        if not isinstance(a, dict):
            raise WriteRefused("Not saved: an artefact entry is malformed.")
        file = a.get("file")
        ext = _ext_of(file, base) if isinstance(file, str) else None
        if ext is None or ext in seen:
            raise WriteRefused("Not saved: an artefact name does not match the diagram name.")
        seen.add(ext)
        media, enc = MEDIA[ext]
        if a.get("media_type") != media or a.get("encoding") != enc:
            raise WriteRefused(f"Not saved: {file} has an unexpected media type or encoding.")
        content = a.get("content")
        if not isinstance(content, str):
            raise WriteRefused(f"Not saved: {file} has no content.")
        try:
            data = (base64.b64decode(content, validate=True) if enc == "base64"
                    else content.encode("utf-8"))
        except (binascii.Error, ValueError):
            raise WriteRefused(f"Not saved: {file} is not valid base64.") from None
        if len(data) != a.get("bytes") or len(data) > CAPS[ext]:
            raise WriteRefused(f"Not saved: {file} size does not match or is over its limit.")
        if hashlib.sha256(data).hexdigest() != a.get("sha256"):
            raise WriteRefused(f"Not saved: {file} failed its sha256 check.")
        if ext == "report.json":
            try:
                if json.loads(data.decode("utf-8")).get("schema") != REPORT_SCHEMA:
                    raise ValueError
            except (ValueError, AttributeError):
                raise WriteRefused(f"Not saved: {file} is not a gac.diagram-report/1 document.") from None
        total += len(data)
        out[file] = data
    if total > TOTAL_CAP:
        raise WriteRefused("Not saved: the artefacts are over the 4 MiB total limit.")
    return out


def resolve_root(explicit: Optional[str] = None, cwd: Optional[str] = None) -> Path:
    raw = explicit or os.getenv("GAC_DIAGRAM_WORKSPACE_ROOT")
    if raw:
        root = Path(raw)
        if not root.is_absolute() or not root.is_dir():
            raise WriteRefused("Not saved: GAC_DIAGRAM_WORKSPACE_ROOT must be an existing "
                               "absolute folder.")
    else:
        here = Path(cwd or os.getcwd())
        if not any((here / m).exists() for m in MARKERS):
            raise WriteRefused("Not saved: set GAC_DIAGRAM_WORKSPACE_ROOT to your project folder "
                               f"(the bridge was started in {here}, which is not a project)")
        root = here
    real = Path(os.path.realpath(root))
    if real == Path(real.anchor) or real == Path(os.path.realpath(Path.home())):
        raise WriteRefused("Not saved: the workspace root cannot be the filesystem root or your "
                           "home folder.")
    return real


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _no_symlinks(path: Path, stop: Path) -> None:
    cur = path
    while True:
        if cur.is_symlink():
            raise WriteRefused("Not saved: a symlink is in the output path.")
        if cur == stop or cur.parent == cur:
            return
        cur = cur.parent


def _names(diagram) -> List[str]:
    arts = diagram.get("artifacts") if isinstance(diagram, dict) else None
    if not isinstance(arts, list):
        return []
    return [str(a.get("file")) for a in arts if isinstance(a, dict)]


def write_artifacts(diagram: dict, *, out_dir: str = "", overwrite: bool = False,
                    workspace_root: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Validate and write. Returns {"written": [abs paths], "refused": [...], "reason": str,
    "notes": [...]}. Never raises for a rule refusal."""
    try:
        files = validate_payload(diagram)
        root = resolve_root(workspace_root, cwd)
        default_dir = root / "outputs" / "diagrams"
        raw_out = out_dir or os.getenv("GAC_DIAGRAM_LOCAL_DIR") or ""
        out = Path(raw_out) if raw_out else default_dir
        if not out.is_absolute():
            out = root / out
        out = Path(os.path.abspath(out))
        allow_outside = (os.getenv("GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE") or "").lower() == "true"
        if not _inside(Path(os.path.realpath(out)), root) and not allow_outside:
            raise WriteRefused("Not saved: the output folder is outside the workspace root (set "
                               "GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE=true in your environment to "
                               "allow it).")
        target_dir = out / diagram["name"]
        if _inside(target_dir, root):
            _no_symlinks(target_dir, root)
        else:
            _no_symlinks(target_dir, Path(target_dir.anchor))
        targets = {f: target_dir / f for f in files}
        for f, t in targets.items():
            if t.is_symlink():
                raise WriteRefused("Not saved: a symlink is in the output path.")
            if t.exists() and not overwrite:
                where = _shown(t, root).rsplit("/", 1)[0]
                raise WriteRefused(f"Not saved: {f} already exists in {where}; pass "
                                   "overwrite=true to replace")
    except WriteRefused as e:
        logger.warning("diagram write refused: %s", str(e)[:120])
        return {"written": [], "refused": _names(diagram), "reason": str(e), "notes": []}
    notes: List[str] = []
    created_dirs: List[Path] = []
    made_default = not default_dir.exists() and _inside(target_dir, default_dir)
    written: List[Path] = []
    try:
        for d in (out, target_dir):
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created_dirs.append(d)
        if made_default and default_dir.exists():
            gi = default_dir / ".gitignore"
            if not gi.exists():
                gi.write_text("*\n", encoding="utf-8")
                notes.append(f"Created {_shown(gi, root)} (customer diagrams are not committed "
                             "by accident).")
        for f, data in files.items():
            t = targets[f]
            fd, tmp = tempfile.mkstemp(prefix=".gac-", dir=str(target_dir))
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(tmp, 0o644)
                if not overwrite:
                    claim = os.open(str(t), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                    os.close(claim)
                    written.append(t)
                os.replace(tmp, str(t))
                if overwrite:
                    written.append(t)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
    except (OSError, WriteRefused) as e:
        for t in written:
            try:
                t.unlink()
            except OSError:
                pass
        reason = str(e) if isinstance(e, WriteRefused) else (
            f"Not saved: a write failed ({type(e).__name__}); files from this call were removed.")
        if isinstance(e, FileExistsError):
            reason = "Not saved: a file appeared while writing; nothing from this call was kept."
        logger.error("diagram write failed (%s)", type(e).__name__)
        return {"written": [], "refused": list(files), "reason": reason, "notes": []}
    return {"written": [str(p) for p in written], "shown": [_shown(p, root) for p in written],
            "refused": [], "reason": "", "notes": notes}


def _shown(p: Path, root: Path) -> str:
    """The path as the front door prints it: relative to the workspace root, so the text never
    carries an absolute workstation path (or a local user name); outside the root (only when the
    deployer allows it) just the topic folder and the file name."""
    try:
        return str(p.relative_to(root))
    except ValueError:
        return f"{p.parent.name}/{p.name}"


def front_door_lines(result: dict) -> str:
    """The fixed front-door text appended after the gated engine text."""
    if result.get("written"):
        shown = result.get("shown") or result["written"]
        lines = ["Saved locally:"] + [f"- {p} (sha256 verified)" for p in shown]
        return "\n".join(lines + list(result.get("notes", [])))
    return result.get("reason") or "Not saved: no artefacts were returned."
