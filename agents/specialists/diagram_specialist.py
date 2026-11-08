"""The Diagram specialist: draws AWS architecture diagrams from knowledge-base diagram facts.

Describe-only. It never deploys, mutates, spawns a process or opens a socket (Tenets 1 and 3).
Deterministic by default (Tier 1, verified zone): a natural-language request runs
catalogue (every specialist's validated facts, read-only through `diagram_facts()`) -> plan
(a preset trigger, or the deterministic planner over the KB-owned vocabulary; it only SELECTS
catalogue ids) -> subgraph closure -> view planner (one preset or generic view, or up to four
preset and flow views) -> per view: compose (every element must cite a resolvable fact, else
the request fails closed; Tenet 4) -> layout candidates -> measured quality check -> drawio,
SVG, PNG and report artefacts -> one bounded wire budget -> a bounded, quoted manifest text.
The Orchestrator gates that text; artefacts leave only with a passing gate. A request the
knowledge cannot support gets an honest decline with prompts that do render (design D7.1).

The opt-in Bedrock planner (design D2, agents/diagram/planner_bedrock.py; off by default) may
only select catalogue ids; its output goes through the same plan validation, closure and
compose, and is exploration zone at the gate. Any failure falls back to the deterministic plan.

The AWS Architecture Icons are never in the repository: the image build bakes a sanitized,
checksummed catalog from a deployer-staged zip. Without it this specialist stays registered and
declines honestly (docs/DEPLOY.md, "Stage the AWS Architecture Icons").
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..base_agent import BaseAgent
from ..diagram import decline as D
from ..diagram import footer as F
from ..diagram import labels as L
from ..diagram import manifest as M
from ..diagram.assets import Assets
from ..diagram.bundle import ViewFiles, build_bundles, compact_manifest
from ..diagram.catalogue import Catalogue, build_catalogue
from ..diagram.closure import close
from ..diagram.compose import Collected, ComposeResult, UntracedElement, compose
from ..diagram.emit_drawio import emit_drawio
from ..diagram.emit_svg import emit_svg
from ..diagram.layout import best_layout
from ..diagram.limits import MAX_REQUEST_CHARS, MAX_SELECTION_NODES, Limits, resolve_limits
from ..diagram.plan import Plan, resolve_mode
from ..diagram.planner_det import Decline, plan_request
from ..diagram.quality import MESSAGES
from ..diagram.raster import rasterize
from ..diagram.report import build_report
from ..diagram.topics import merge_topics
from ..diagram.views import BoundsDecline, View, ViewPlan, assign_names, plan_views

logger = logging.getLogger(__name__)

TOPICS_SOURCE = "diagram:tier_a/diagram/topics"

DISABLED = ("[diagram] The diagram specialist is disabled in this deployment "
            "(diagram.enabled: false). No file was produced.")
ICONS_ABSENT_IMAGE = ("[diagram] I can't draw this here: the AWS Architecture Icons were not "
                      "staged when this image was built, so no file was produced. See "
                      "docs/DEPLOY.md, \"Stage the AWS Architecture Icons\", then rebuild the "
                      "image.")
ICONS_ABSENT_LOCAL = ("[diagram] I can't draw this here: the AWS Architecture Icons were not "
                      "built locally, so no file was produced. Run ./run.sh diagram-assets, see "
                      "docs/DEPLOY.md, \"Stage the AWS Architecture Icons\".")
CATALOG_FAILED = "The icon catalog in this image failed its checksum."
RENDERER_ABSENT = ("[diagram] I can't draw this here: the PNG renderer is missing from this "
                   "image, so no file was produced. Rebuild the image (docs/DEPLOY.md).")
TOO_LONG = ("[diagram] This request is longer than 2000 characters, so I did not draw it. "
            "Ask for one topic in a short sentence.")
BAD_TITLE = ("[diagram] The title was rejected by the diagram label policy, so no file was "
             "produced. Use up to 48 plain ASCII characters.")
BAD_NAME = ("[diagram] The file name must be lowercase letters, digits and hyphens (up to 64), "
            "so no file was produced.")
LAYOUT_ERROR = "[diagram] I could not lay out this topic (internal error), so no file was produced."
NO_FACTS = ("[diagram] There are no diagram facts for \"{title}\" in the knowledge base, so no "
            "file was produced. See docs/ARCHITECTURE.md, \"Describe your topology for "
            "diagrams\".")
FEW_NODES = ("[diagram] Not enough diagram facts for \"{title}\" (fewer than 2 nodes), so no "
             "file was produced. Run ./run.sh diagram --check-facts.")
BOUNDS = ("[diagram] This topic needs {detail}; split it into smaller topics. No file was "
          "produced.")
DEADLINE_NOTE = "The render deadline was reached; views not reached are listed as not delivered."

# Draw-intent routing rules (pinned, case-insensitive, word-bounded; design D10).
_RULE_A = re.compile(r"^\s*(?:(?:please|can you|could you|would you)\s+)?"
                     r"(?:draw|diagram|sketch|visuali[sz]e|illustrate)\b", re.I)
_NOUN = re.compile(r"\b(?:diagram|diagrams|drawio)\b|draw\.io|\.drawio\b", re.I)
_DRAWING = re.compile(r"\bdrawing\b", re.I)
_VERB = re.compile(r"\b(?:draw|create|make|generate|produce|render)\b|\bgive me\b|\bshow me\b", re.I)
_RULE_C = re.compile(r"^\s*show\b(?:\s+(?:me|us))?(?:\s+(?:our|the|how))?", re.I)
_FLOW = re.compile(r"\b(?:path|flow|topology|architecture|ingestion|network)\b", re.I)


@dataclass
class RenderResult:
    raw_text: str
    bundle: Optional[dict]
    sources: List[str]
    manifest: Optional[dict] = None
    files: Dict[str, bytes] = field(default_factory=dict)
    #: every delivered view's bundle; bundle (view 1) is bundles[0]
    bundles: List[dict] = field(default_factory=list)
    used_llm: bool = False
    confidence: Optional[float] = None
    #: the D5.3 footer lines the gate renders on a pass
    footer_lines: List[str] = field(default_factory=list)
    #: the D5.3 audit `extra` (ints and enums)
    audit_extra: Dict[str, object] = field(default_factory=dict)
    #: no file was produced (an honest decline)
    declined: bool = False


@dataclass
class _ViewOut:
    """One view after layout (memoized per request: replays reuse it)."""
    res: ComposeResult
    rendered: object = None

    @property
    def passed(self) -> bool:
        return self.rendered is not None and bool(self.rendered.quality.passed)


class DiagramSpecialist(BaseAgent):
    """Draws traceable diagrams from the KB. Read-only peers; one gated response."""

    knowledge_dirs = ["tier_a/diagram", "tier_b/diagram"]

    def __init__(self, *, assets_dir: Optional[str] = None,
                 clock: Callable[[], float] = time.monotonic,
                 planner_factory: Optional[Callable[..., object]] = None):
        super().__init__("diagram")
        self._peers: Callable[[], Dict[str, object]] = lambda: {}
        self._assets_dir = assets_dir
        self._assets: Optional[Assets] = None
        self.clock = clock
        #: test seam only (design D5.4): no environment variable can inject a planner
        self._planner_factory = planner_factory

    def get_system_prompt(self) -> str:
        return ("You are the Diagram specialist. You draw only from knowledge-base diagram facts, "
                "cite every element, and never deploy or change anything (Tenets 1, 3 and 4).")

    # -- wiring -------------------------------------------------------------------------------

    def bind_peers(self, fn: Callable[[], Dict[str, object]]) -> None:
        self._peers = fn

    @property
    def assets(self) -> Assets:
        if self._assets is None:
            self._assets = Assets(self._assets_dir, enabled=resolve_limits().enabled)
        return self._assets

    def readiness_line(self) -> str:
        r = self.assets.readiness()
        lim = resolve_limits()
        return (f"diagram: {'enabled' if lim.enabled else 'disabled'}, icons {r.icons}"
                f"{' (test-fixture)' if r.source == 'test-fixture' else ''}, PNG rasterizer "
                f"{r.rasterizer}, {r.mode} mode")

    # -- routing ------------------------------------------------------------------------------

    def matches_intent(self, request: str, other_score: Callable[[str], int]) -> bool:
        if _RULE_A.search(request):
            return True
        noun = bool(_NOUN.search(request)) or (bool(_DRAWING.search(request))
                                                and other_score(request) == 0)
        if noun and _VERB.search(request):
            return True
        m = _RULE_C.search(request)
        if m:
            rest = request[m.end():]
            words = rest.split()
            window = " ".join(words[:6])
            fm = _FLOW.search(window)
            if fm:
                stripped = request[:m.end()] + " " + (window[:fm.start()] + window[fm.end():]) \
                    + " " + " ".join(words[6:])
                if other_score(stripped) == 0:
                    return True
        return False

    # -- contract -----------------------------------------------------------------------------

    def handle_request(self, request: str, environment: str = "dev") -> str:
        return self.render_request(request, environment, deliver=False).raw_text

    def _decline(self, text: str, footer_lines: Optional[List[str]] = None) -> RenderResult:
        return RenderResult(text, None, [TOPICS_SOURCE], footer_lines=list(footer_lines or []),
                            declined=True)

    def topics(self):
        return merge_topics(self._knowledge)

    # -- traceability (design D5.1) -----------------------------------------------------------

    def _resolver(self) -> Callable[[str, str, str], bool]:
        agents: Dict[str, object] = {self.name: self}
        try:
            agents.update(self._peers() or {})
        except Exception as e:  # a peer registry failure leaves only own facts resolvable
            logger.warning("diagram: peers unavailable for traceability (%s)", type(e).__name__)

        def resolves(source: str, kind: str, fid: str) -> bool:
            spec, _, rest = source.partition(":")
            kb_key, _, sid = rest.partition("#")
            if sid != fid:
                return False
            agent = agents.get(spec)
            data = getattr(agent, "_knowledge", {}).get(kb_key) if agent is not None else None
            entries = data.get("diagram_facts") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                return False
            return any(isinstance(e, dict) and e.get("kind") == kind and str(e.get("id")) == fid
                       for e in entries)

        return resolves

    # -- one view -------------------------------------------------------------------------------

    def _layout_view(self, view: View, cat: Catalogue, lim: Limits, deadline: float,
                     memo: Dict[tuple, _ViewOut], caller_title: str = "",
                     resolver=None) -> _ViewOut:
        key = (view.kind, view.preset, tuple(view.facts), view.hints, caller_title, view.title)
        if key in memo:
            return memo[key]
        collected = Collected(list(cat.facts.values()), list(cat.flags))
        if view.kind == "preset":
            res = compose(view.preset, view.title, caller_title, collected, lim,
                          resolver=resolver)
        else:
            res = compose(view.id, view.title, caller_title, collected, lim, select=view.facts,
                          resolver=resolver)
        out = _ViewOut(res)
        if res.scene is not None:
            warnings = self._icon_warnings(res.scene)
            out.rendered = view.rendered or best_layout(
                res.scene, pass_score=lim.quality_pass_score,
                max_attempts=lim.max_layout_attempts, deadline=deadline, clock=self.clock,
                icon_warnings=warnings, hints_enabled=view.hints)
        memo[key] = out
        return out

    def _icon_warnings(self, scene) -> Dict[str, str]:
        try:
            if self.assets.readiness().icons != "present":
                return {}
            return {n.id: self.assets.icon(n.icon).warn for n in scene.nodes
                    if self.assets.icon(n.icon).warn}
        except Exception:
            return {}

    # -- replay (decline suggestions, check-facts; review M8) ----------------------------------

    def _replayer(self, cat: Catalogue, lim: Limits, memo: Dict[tuple, _ViewOut],
                  resolver) -> Callable[[str], Optional[D.Replay]]:
        """Replays share one budget (Tenet 12): every suggestion is tried inside a single
        render deadline; a suggestion not reached in time is not offered."""
        stop_at = self.clock() + lim.render_timeout_seconds

        def replay(prompt: str) -> Optional[D.Replay]:
            if self.clock() > stop_at:
                return None
            try:
                p = plan_request(prompt, cat)
                if isinstance(p, Decline):
                    return None
                c = close(p, cat)
                if c.bounds:
                    return None
                deadline = stop_at
                vp = plan_views(c, cat, lim, title=p.title, selected=p.selected,
                                layout_fn=self._try_fn(cat, lim, deadline, memo, resolver))
                if isinstance(vp, BoundsDecline):
                    return None
                ok = all(self._layout_view(v, cat, lim, deadline, memo,
                                           resolver=resolver).passed for v in vp.views)
                return D.Replay(p.planner, p.preset, list(p.unmatched), len(vp.views), ok)
            except Exception as e:  # a replay failure only drops that suggestion
                logger.info("diagram: suggestion replay failed (%s)", type(e).__name__)
                return None
        return replay

    def _try_fn(self, cat, lim, deadline, memo, resolver, caller_title: str = ""):
        def try_generic(view: View):
            out = self._layout_view(view, cat, lim, deadline, memo, caller_title, resolver)
            return out.passed, out.rendered
        return try_generic

    def _suggestions(self, cat: Catalogue, lim: Limits, memo, resolver,
                     cands: Optional[List[Tuple[str, str]]] = None) -> List[str]:
        return D.suggestions(cands if cands is not None else D.candidates(cat),
                             self._replayer(cat, lim, memo, resolver))

    # -- check-facts ------------------------------------------------------------------------

    def check_facts(self) -> tuple:
        """`./run.sh diagram --check-facts`: validate the catalogue (facts, aliases, tags,
        descriptions, vocabulary, presets), render every preset (quality >= the pass mark) and
        replay every preset's suggestion (M8). Returns (report text, 0 when nothing is flagged
        else 1)."""
        lim = resolve_limits()
        cat = build_catalogue(self, self._peers())
        resolver = self._resolver()
        memo: Dict[tuple, _ViewOut] = {}
        replay = self._replayer(cat, lim, memo, resolver)
        lines = [f"Diagram facts check: {len(cat.presets)} topic(s)."]
        v = cat.vocabulary
        lines.append(f"Vocabulary: {len(v.synonyms)} synonyms, {len(v.ignore)} ignore words, "
                     f"{len(v.scopes)} scope phrases, {len(v.flow_order)} flow tags.")
        facts = list(cat.facts.values())
        lines.append(f"Catalogue: {len(cat.nodes())} nodes, {len(cat.groups())} groups, "
                     f"{len(cat.edges())} edges; {sum(len(f.aliases) for f in facts)} aliases, "
                     f"{sum(len(f.tags) for f in facts)} tags, "
                     f"{sum(1 for f in facts if f.description)} descriptions.")
        extra: List[str] = []
        flagged = len(cat.flags)
        for t in cat.presets:
            pv = View(t.id, "preset", t.title, [], preset=t.id, hints=True)
            out = self._layout_view(pv, cat, lim, self.clock() + lim.render_timeout_seconds,
                                    memo, resolver=resolver)
            scene = out.res.scene
            n = len(scene.nodes) if scene else 0
            e = len(scene.edges) if scene else 0
            g = len(scene.groups) if scene else 0
            q = out.rendered.quality if out.rendered is not None else None
            score = (f"quality {q.score:.1f}, {'pass' if q.passed else 'below the pass mark'}"
                     if q is not None else "not rendered")
            lines.append(f'Topic "{t.title}" ({t.id}): {n} nodes (limit {lim.max_nodes}), '
                         f'{e} edges (limit {lim.max_edges}), {g} groups (limit '
                         f'{lim.max_groups}); {score}.')
            if out.res.decline:
                lines.append(f"- decline ({out.res.decline})")
                flagged += 1
            elif not out.passed:
                extra.append(f'- ignored "{t.kb_key}#{t.id}" (reason preset-render)')
                flagged += 1
            if t.suggestion and not D.keeps(t.id, replay(t.suggestion)):
                extra.append(f'- ignored "{t.kb_key}#{t.id}" (reason preset-suggestion)')
                flagged += 1
        own = getattr(self, "name", "diagram")
        if self._peers() and not any(not f.sources[0].startswith(own + ":")
                                     for f in facts if f.sources):
            extra.append("- ignored (reason peer-catalogue-empty)")
            flagged += 1
        lines += [f.line() for f in cat.flags] + extra
        lines.append("OK: nothing flagged." if flagged == 0 else f"{flagged} item(s) flagged.")
        return "\n".join(lines), (0 if flagged == 0 else 1)

    # -- the request ----------------------------------------------------------------------------

    def render_request(self, request: str, environment: str = "dev", *, deliver: bool,
                       title: str = "", name: str = "", wire_budget: bool = True,
                       planner: str = "") -> RenderResult:
        lim = resolve_limits()
        if not lim.enabled:
            return self._decline(DISABLED)
        ready = self.assets.readiness()
        if ready.icons != "present":
            base = ICONS_ABSENT_IMAGE if ready.mode == "image" else ICONS_ABSENT_LOCAL
            if ready.icons == "invalid":
                base += " " + CATALOG_FAILED
            return self._decline(base)
        if ready.mode == "image" and ready.rasterizer != "available":
            return self._decline(RENDERER_ABSENT)
        if len(request) > MAX_REQUEST_CHARS:
            return self._decline(TOO_LONG)
        if title and L.check_label(title) is not None:
            return self._decline(BAD_TITLE)
        if name and not L.check_name(name):
            return self._decline(BAD_NAME)
        try:
            return self._render(request, lim, ready, deliver=deliver, title=title, name=name,
                                wire_budget=wire_budget, planner=planner)
        except Exception as e:  # a layout bug must never leak a partial artefact
            logger.error("diagram: render failed (%s)", type(e).__name__)
            return self._decline(LAYOUT_ERROR)

    def _plan(self, request: str, cat: Catalogue, lim: Limits, planner: str, title: str):
        """(Plan | Decline, fallback code). The mode is resolved per request and a caller can
        only downgrade (design D2.1). A preset trigger is tried first in both modes, so preset
        phrasings never spend a model call. In `bedrock` mode the opt-in planner may only
        select catalogue ids; any failure falls back to the deterministic plan with a D14
        code. The bedrock module (and boto3) is imported only in that mode."""
        mode = resolve_mode("deterministic" if planner == "deterministic" else "", None,
                            lim.planner.mode)
        det = plan_request(request, cat, title)
        if mode != "bedrock" or (isinstance(det, Plan) and det.planner == "preset"):
            return det, ""
        from ..diagram import planner_bedrock as PB
        out = PB.plan_bedrock(request, cat, lim.planner, caller_title=title,
                              unmatched=list(det.unmatched),
                              client_factory=self._planner_factory)
        if out.plan is None:
            return det, out.code
        return out.plan, ""

    def _render(self, request: str, lim: Limits, ready, *, deliver: bool, title: str,
                name: str, wire_budget: bool, planner: str) -> RenderResult:
        cat = build_catalogue(self, self._peers())
        resolver = self._resolver()
        memo: Dict[tuple, _ViewOut] = {}
        planned, fallback = self._plan(request, cat, lim, planner, title)
        if isinstance(planned, Decline):
            sugg = self._suggestions(cat, lim, memo, resolver)
            top = D.degree_ranked(cat, 1)
            example = cat.facts[top[0]].label.lower() if top else "gateway"
            text = D.no_plan_text(reason=planned.reason, terms=planned.terms,
                                  unmatched=planned.unmatched, example_label=example, sugg=sugg)
            return self._decline(text, F.lines_for(planner="deterministic", fallback=fallback,
                                                   rendered=False))
        plan: Plan = planned
        decline_footer = F.lines_for(planner=plan.planner, fallback=fallback, cache=plan.cache,
                                     rendered=False)
        if plan.planner == "llm" and not plan.selected and not plan.preset:
            # An empty model selection is an honest decline (design D14), verified zone.
            sugg = self._suggestions(cat, lim, memo, resolver)
            top = D.degree_ranked(cat, 1)
            example = cat.facts[top[0]].label.lower() if top else "gateway"
            text = D.no_plan_text(reason="no-match" if plan.unmatched else "no-anchor",
                                  terms=plan.terms, unmatched=plan.unmatched,
                                  example_label=example, sugg=sugg)
            return self._decline(text, decline_footer)
        closed = close(plan, cat)
        if closed.bounds:
            sugg = self._suggestions(cat, lim, memo, resolver)
            return self._decline(D.selection_bounds_text(len(closed.nodes), MAX_SELECTION_NODES,
                                                         sugg), decline_footer)
        # The render deadline starts after planning (design D2.7).
        deadline = self.clock() + lim.render_timeout_seconds
        vp = plan_views(closed, cat, lim, title=plan.title, selected=plan.selected,
                        layout_fn=self._try_fn(cat, lim, deadline, memo, resolver,
                                               title if closed.mode != "preset" else ""))
        if isinstance(vp, BoundsDecline):
            sugg = self._suggestions(cat, lim, memo, resolver, vp.suggestions)
            return self._decline(D.views_bounds_text(vp.needed, vp.limit, sugg), decline_footer)
        overall = title or plan.title
        if plan.scope == "security" and not title and len(vp.views) > 1:
            # each view answers the question asked, not only the part it shows (D6.4)
            for v in vp.views:
                v.title = f"{plan.title}: {v.title}"
        assign_names(vp.views, caller_name=name, title=overall)
        return self._render_views(vp, plan, closed, cat, lim, ready, memo, resolver,
                                  deliver=deliver, title=title, overall=overall,
                                  wire_budget=wire_budget, deadline=deadline, fallback=fallback)

    def _render_views(self, vp: ViewPlan, plan: Plan, closed, cat: Catalogue, lim: Limits,
                      ready, memo, resolver, *, deliver: bool, title: str, overall: str,
                      wire_budget: bool, deadline: float, fallback: str) -> RenderResult:
        single = len(vp.views) == 1
        caller_title = title if single else ""
        assets = self.assets
        image_mode = ready.mode == "image"
        colors = {"public-subnet": assets.color("PublicSubnet"),
                  "account": assets.color("AWSAccount")}
        base_notes: List[str] = []
        if ready.rasterizer != "available":
            base_notes.append(M.LOCAL_PREVIEW)
        if ready.source == "test-fixture":
            base_notes.append(M.TEST_FIXTURE)
        plan_record = {
            "planner": plan.planner, "scope": plan.scope, "preset": plan.preset,
            "selected": list(plan.selected), "added_by": dict(closed.added_by),
            "excluded": dict(closed.excluded), "unmatched": list(plan.unmatched),
            "unknown_ids": plan.unknown_ids, "fallback": fallback,
            "cache": plan.cache, "usage": dict(plan.usage), "notes": list(plan.notes),
            "catalogue_sha256": cat.sha256,
            "status": {q: ({"status": s, "reason": r} if r else {"status": s})
                       for q, (s, r) in vp.status.items()},
            "views": [{"id": v.id, "name": v.name, "kind": v.kind, "title": v.title,
                       "context": len(v.context)} for v in vp.views],
        }
        rendered_views = []      # (view, scene, rendered, files, base, draft, omitted, extras)
        missing: Dict[str, str] = {}
        all_sources: List[str] = []
        timed_out = False
        for i, view in enumerate(vp.views):
            if i > 0 and self.clock() > deadline:
                missing[view.name] = "render_deadline"
                timed_out = True
                continue
            out = self._layout_view(view, cat, lim, deadline, memo, caller_title, resolver)
            res = out.res
            if res.scene is None:
                if res.decline == "bounds":
                    return self._decline(BOUNDS.format(detail=res.detail))
                tmpl = FEW_NODES if res.decline == "few-nodes" else NO_FACTS
                return self._decline(tmpl.format(title=view.title))
            if out.rendered is None:
                return self._decline(LAYOUT_ERROR)
            scene = res.scene
            scene.topic_id = view.preset or view.name
            lay, q = out.rendered.layout, out.rendered.quality
            if out.rendered.timed_out:
                timed_out = True
            draft = not q.passed
            icons = {n.id: assets.icon(n.icon) for n in scene.nodes}
            group_icons = {g.id: assets.group_icon(g.type) for g in scene.groups}
            disp_title = scene.title or scene.topic_title
            base = f"{view.name}-draft" if draft else view.name
            svg = emit_svg(lay, icons, group_icons, colors)
            drawio = emit_drawio(lay, base, disp_title, icons, group_icons, colors)
            files: Dict[str, bytes] = {"drawio": drawio.encode("utf-8"),
                                       "svg": svg.encode("utf-8")}
            omitted: List[Tuple[str, str]] = []
            if ready.rasterizer == "available":
                r = rasterize(svg, lay.width * lim.png_scale, image_mode=image_mode,
                              deadline=deadline, clock=self.clock)
                if r.png is not None:
                    files["png"] = r.png
                else:
                    omitted.append((f"{base}.png", r.reason))
            sources = list(scene.source_keys)
            if view.preset:
                p = cat.preset(view.preset)
                if p is not None:
                    sources.append(f"diagram:{p.kb_key}")
            if ready.icon_manifest_source:
                sources.append(ready.icon_manifest_source)
            sources = [s for s in sources if L.check_source(s)]
            for s in sources:
                if s not in all_sources:
                    all_sources.append(s)
            findings = [f.as_dict() for f in q.findings]
            flagged = [f.as_dict() for f in scene.flagged]
            generic = [(n.label, icons[n.id].warn) for n in scene.nodes if icons[n.id].warn]
            by_id = {(e.kind, e.id): e for e in scene.groups + scene.nodes + scene.edges}
            elements = []
            for kind, geos in (("group", lay.groups), ("node", lay.nodes), ("edge", lay.edges)):
                for geo in sorted(geos, key=lambda g: int(g.gid[1:])):
                    el = by_id[(kind, geo.key)]
                    elements.append({"id": geo.gid, "kind": kind, "label": el.label,
                                     "kb_refs": list(el.kb_refs), "refs": list(el.refs)})
            files["report.json"] = build_report(
                topic_id=scene.topic_id, title=disp_title, score=q.score, passed=q.passed,
                threshold=q.threshold, findings=findings, rendered=scene.rendered,
                cited=scene.cited, flagged=flagged, generic_icons=[g for g, _ in generic],
                sources=sources, elements=elements, plan=plan_record,
            ).encode("utf-8")

            def rerender_1x(svg=svg, lay=lay):
                if ready.rasterizer != "available" or lim.png_scale == 1:
                    return None
                return rasterize(svg, lay.width, image_mode=image_mode, deadline=deadline,
                                 clock=self.clock).png

            rendered_views.append((view, scene, q, files, base, draft, omitted, generic,
                                   findings, sources, rerender_1x))
        notes: List[str] = []
        if timed_out:
            notes.append(DEADLINE_NOTE if not single else
                         "The render deadline was reached; the best layout so far is returned.")
        notes += base_notes
        kb_lines = [D.not_in_kb_line(plan.unmatched),
                    D.drawn_from_line(cat.vocabulary.topology_note)]
        excluded_n = sum(1 for s, r in vp.status.values()
                         if s == "excluded" and r and r != "not-selected")
        delivered_ids = [v.id for v, *_ in rendered_views]
        footer_lines = F.lines_for(planner=plan.planner, fallback=fallback, cache=plan.cache,
                                   selected=vp.count("selected"), added=vp.count("added"),
                                   excluded=excluded_n, unknown_ids=plan.unknown_ids,
                                   view_ids=delivered_ids, context=vp.context_count)
        audit = {"planner": plan.planner, "included": vp.count("selected") + vp.count("added"),
                 "excluded": excluded_n, "closure_added": vp.count("added"),
                 "unknown_ids": plan.unknown_ids, "views": len(rendered_views),
                 "fallback": fallback}
        audit.update({k: v for k, v in plan.usage.items()
                      if k in ("input_tokens", "output_tokens")})
        rendered_n = sum(rv[1].rendered for rv in rendered_views)
        cited_n = sum(rv[1].cited for rv in rendered_views)
        traceability = (cited_n / rendered_n) if rendered_n else 0.0
        vfiles = [ViewFiles(name=v.name, draft=draft,
                            quality={"score": q.score, "pass": q.passed, "threshold": q.threshold,
                                     "findings": findings},
                            traceability={"rendered": scene.rendered, "cited": scene.cited,
                                          "flagged": len(scene.flagged)},
                            sources=sources, files=files, rerender_png_1x=rr, omitted=om)
                  for (v, scene, q, files, base, draft, om, _g, findings, sources, rr)
                  in rendered_views]

        def text_for(arts_per_view, omitted_per_view, dropped=(), too_large=False):
            if single:
                v, scene, q, files, base, draft, _om, generic, findings, _s, _rr = \
                    rendered_views[0]
                vnotes = (["Saved as a draft: the names end in -draft."] if draft else []) + notes
                return M.build_text(M.ManifestInput(
                    title=scene.title or scene.topic_title, topic_id=scene.topic_id,
                    score=q.score, passed=q.passed, threshold=q.threshold,
                    rendered=scene.rendered, cited=scene.cited,
                    flagged_total=len(scene.flagged),
                    label_lines=[M.line_for(e) for e in scene.groups + scene.nodes]
                    + [M.line_for(e) for e in scene.edges if e.label],
                    unlabelled_edges=sum(1 for e in scene.edges if not e.label),
                    flagged_lines=[f.line() for f in scene.flagged],
                    finding_lines=[M.finding_line(f.severity, f.code, f.element, f.message)
                                   for f in q.findings],
                    report_file=f"{base}.report.json", generic_icons=generic,
                    artefacts=arts_per_view[0] if arts_per_view else [],
                    omitted=omitted_per_view[0] if omitted_per_view else [],
                    deliver=deliver, draft=draft,
                    hard_lines=[M.finding_line(f.severity, f.code, f.element, f.message)
                                for f in q.hard],
                    notes=vnotes, too_large=too_large, kb_lines=kb_lines))
            sections = []
            names_live = [v.name for v, *_ in rendered_views]
            by_name = {v.name: rv for rv in rendered_views for v in [rv[0]]}
            generic_all: List[Tuple[str, str]] = []
            for view in vp.views:
                ctx = len(view.context)
                if view.name in missing or view.name not in by_name or view.name in dropped:
                    why = missing.get(view.name, "views_over_limit")
                    sections.append(M.ViewSection(view.title, view.id, 0.0, False, 0.0, 0, 0, 0,
                                                  ctx, 0, 0, missing=why))
                    continue
                v, scene, q, files, base, draft, _om, generic, _f, _s, _rr = by_name[view.name]
                idx = names_live.index(view.name)
                for g in generic:
                    if g not in generic_all:
                        generic_all.append(g)
                sections.append(M.ViewSection(
                    view.title, view.id, q.score, q.passed, q.threshold, len(scene.nodes),
                    len(scene.edges), len(scene.groups), ctx, scene.rendered, scene.cited,
                    draft=draft,
                    hard_lines=[M.finding_line(f.severity, f.code, f.element, f.message)
                                for f in q.hard],
                    artefacts=arts_per_view[idx] if idx < len(arts_per_view) else [],
                    omitted=omitted_per_view[idx] if idx < len(omitted_per_view) else []))
            scene0 = rendered_views[0][1]
            return M.build_multi_text(M.MultiInput(
                title=overall, sections=sections, flagged_total=len(scene0.flagged),
                flagged_lines=[f.line() for f in scene0.flagged],
                report_hint="each view's report.json", kb_lines=kb_lines,
                generic_icons=generic_all, notes=notes, deliver=deliver, too_large=too_large))

        estimate, _ok = text_for(
            [[(f"{b}.{k}", len(val), "0" * 12) for k, val in f.items()]
             for (_v, _s, _q, f, b, *_r) in rendered_views],
            [om for (_v, _s, _q, _f, _b, _d, om, *_r) in rendered_views])
        br = build_bundles(vfiles, limits=lim, text_estimate=estimate, wire_budget=wire_budget)
        text, ok = text_for(br.artefacts, br.omitted, br.dropped, br.too_large)
        common = dict(used_llm=plan.planner == "llm",
                      confidence=traceability if plan.planner == "llm" else None,
                      footer_lines=footer_lines, audit_extra=audit)
        if not ok or not br.bundles:
            return RenderResult(text, None, all_sources, declined=True, **common)
        live = [rv for rv in rendered_views if rv[0].name not in br.dropped]
        manifests = [compact_manifest(b, name=rv[0].name, draft=rv[5], score=rv[2].score,
                                      artefacts=arts, omitted=oms)
                     for b, rv, arts, oms in zip(br.bundles, live, br.artefacts, br.omitted)]
        man = dict(manifests[0])
        if len(manifests) > 1:
            man["views"] = manifests
        return RenderResult(text, br.bundles[0], all_sources, man, live[0][3],
                            bundles=list(br.bundles), **common)


def known_codes() -> List[str]:
    return sorted(MESSAGES)
