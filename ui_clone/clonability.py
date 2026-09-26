"""Early clonability risk report (Step 5c-d).

`python -m ui_clone.clonability <ref-dir>` reads capture and extraction
artifacts that already exist (no browser) and writes
`<ref-dir>/clonability-report.json`: site traits that make parts of a faithful
clone approximate, slow to verify, or impossible. It exists so these traits are
surfaced to the user right after capture instead of being discovered at
closeout (an intro overlay covering the page during measurement, a WebGL scene
that cannot be pixel-matched, reveal motion measured before it fires).

Every risk carries evidence (artifact path + field/value), the checks/steps it
affects, and a concrete mitigation. Nothing is reported without artifact
evidence. Two severities:

* ``blocker`` - a faithful clone is impossible or disallowed (a bot
  challenge, or recorded unclonable reasons (e.g. auth-gated) in
  ``pipeline-state.json`` ``unclonable_reasons``). The agent stops and asks the
  user and never records the answer itself: the user sends
  ``ui-clone decide <risk-id> proceed|stop <note>`` as a prompt (recorded by the
  Claude UserPromptSubmit hook) or runs ``--decide`` in their own terminal (the
  pre-bash hook denies it from an agent Bash call). Only decisions carrying
  that user provenance count. A ``stop`` answer records the canonical
  ``unclonable_reasons`` entry through ``PipelineState.record_unclonable`` (no
  parallel mechanism). Risk ids derive from the evidence identity (font family +
  CDN, recorded reason), not list positions, and a decision is carried across
  re-runs only when both the id and that identity still match.
* ``caution`` - the clone proceeds; the mitigation is carried into generation
  (generation-planner reads this report) and verification
  (``verification.introSettleMs`` is read by fixed-wait live probes).

The ``pre-generate`` gate requires the report to exist, match the current
hashes of its inputs (including the externally recorded ``unclonable_reasons``),
and have a user decision for every blocker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_NAME = "clonability-report.json"
PRODUCER = "ui_clone.clonability"
SCHEMA_VERSION = 1

# Inputs whose bytes decide the report. pipeline-state.json as a whole is
# deliberately excluded: it changes on every gate pass and would make the report
# perpetually stale. Only its unclonable_reasons (minus the ones this report's
# own stop decisions recorded) are hashed, under STATE_BLOCKERS_INPUT, so a
# recovered or newly recorded blocker marks the report stale.
INPUTS: tuple[str, ...] = (
    "states/splash/contract.json",
    "states/splash/summary.json",
    "interactions-detected.json",
    "canvas-webgl-detection.json",
    "animation-runtime-dump.json",
    "transition-spec.json",
    "scroll-transitions.json",
    "runtime-media.json",
    "paid-features.json",
    "head.json",
    "structure.json",
    "bundles/*.js",
)
STATE_BLOCKERS_INPUT = "pipeline-state.json#unclonable_reasons"

# Only these decision sources count: the user's own prompt (Claude
# UserPromptSubmit hook) or the CLI run from the user's own terminal.
USER_DECISION_SOURCES = frozenset({"user-prompt", "user-terminal"})
# Environment variables agent hosts export into their tool shells. `--decide`
# refuses to run when any is present (even empty): a TTY can be faked with
# `script`, and the pre-bash text guard cannot see through shell indirection.
# Claude Code: CLAUDECODE=1 and CLAUDE_CODE_ENTRYPOINT. Codex: CODEX_THREAD_ID
# and CODEX_CI on tool shells, CODEX_SANDBOX(_NETWORK_DISABLED) when sandboxed.
# CODEX_HOME / ORCA_* are deliberately absent: users set them in their own shells.
AGENT_HOST_ENV_MARKERS: tuple[str, ...] = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_THREAD_ID",
    "CODEX_CI",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
)


def agent_host_marker(environ: Any = None) -> str | None:
    """Name of the first agent-host marker present in the environment."""
    env = os.environ if environ is None else environ
    return next((name for name in AGENT_HOST_ENV_MARKERS if name in env), None)

# `ui-clone decide [<component>/]<risk-id> proceed|stop <note>`, one per line.
# A `>`-quoted line (the user quoting the agent's instructions) is not a
# decision; an empty note or a template placeholder note is rejected.
DECIDE_PROMPT_RE = re.compile(
    r"^[ \t]*ui-clone[ \t]+decide[ \t]+(?:(?P<ref>[A-Za-z0-9._-]+)/)?(?P<risk>[A-Za-z0-9._-]+)"
    r"[ \t]+(?P<decision>proceed|stop)\b[ \t]*(?P<note>[^\n]*)$",
    re.MULTILINE,
)
# `<note>`, `<id>`, `<answer>`: template text copied instead of an answer.
_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9 _-]*>")

# Reference capture viewport used by runtime-media.sh / canvas detection.
_VIEWPORT_W = 1440
_VIEWPORT_H = 900
_VIEWPORT_AREA = _VIEWPORT_W * _VIEWPORT_H
# splash_contract.COVERING_ENTER: the smallest overlay the lifecycle probe
# treats as a mounted splash.
_INTRO_MIN_COVERAGE = 0.45
_INTRO_SETTLE_MARGIN_MS = 500
_HERO_VIDEO_MIN_AREA_RATIO = 0.30
_VIDEO_HEAVY_MIN_COUNT = 3
_CANVAS_2D_MIN_AREA_RATIO = 0.25
_SCROLL_MOTION_MIN_COUNT = 10

_SCROLL_TRIGGER_RE = re.compile(
    r"scroll|viewport|in-?view|intersection|whileinview|reveal", re.IGNORECASE
)
_BOT_CHALLENGE_TITLE_RE = re.compile(
    r"^\s*(just a moment|attention required|checking your browser|"
    r"verify(ing)? you are (a )?human|are you a robot|access denied|"
    r"ddos-guard|security check|captcha)",
    re.IGNORECASE,
)
_WEBGL_LIBRARY_MARKERS = ("WebGLRenderer",)
_EMBED_HOSTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "map",
        (
            "google.com/maps",
            "maps.google.",
            "mapbox.com",
            "openstreetmap.org",
        ),
    ),
    ("video", ("youtube.com", "youtube-nocookie.com", "player.vimeo.com", "vimeo.com")),
    (
        "social",
        ("twitter.com", "platform.x.com", "instagram.com", "facebook.com", "tiktok.com"),
    ),
)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _load(ref_dir: Path, rel: str) -> Any:
    path = ref_dir / rel
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _short(value: Any) -> str:
    return _digest(value)[:10]


def _state_blocker_reasons(ref_dir: Path) -> list[dict]:
    """pipeline-state.json unclonable_reasons not recorded by this report's own
    stop decisions (those are already represented by the decision)."""
    if not (ref_dir / "pipeline-state.json").is_file():
        return []
    from ui_clone.state import PipelineState

    return [
        r
        for r in PipelineState.load(ref_dir).unclonable_reasons
        if isinstance(r, dict) and _as_dict(r.get("detail")).get("source") != REPORT_NAME
    ]


def source_hashes(ref_dir: Path) -> dict[str, str | None]:
    """sha256 per input (None when absent); globs hash a sorted file list."""
    hashes: dict[str, str | None] = {}
    reasons = _state_blocker_reasons(ref_dir)
    hashes[STATE_BLOCKERS_INPUT] = _digest(reasons) if reasons else None
    for rel in INPUTS:
        if "*" in rel:
            parent, pattern = rel.rsplit("/", 1)
            entries = [
                (p.relative_to(ref_dir).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted((ref_dir / parent).glob(pattern))
                if p.is_file()
            ]
            hashes[rel] = (
                hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
                if entries
                else None
            )
            continue
        path = ref_dir / rel
        try:
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        except OSError:
            hashes[rel] = "<unreadable>"
    return hashes


def stale_inputs(ref_dir: Path, report: dict) -> list[str]:
    """Inputs whose current hash differs from the one recorded in the report."""
    recorded = (report.get("provenance") or {}).get("sourceHashes")
    if not isinstance(recorded, dict):
        return list(INPUTS)
    current = source_hashes(ref_dir)
    return [rel for rel, digest in current.items() if recorded.get(rel, "<missing>") != digest]


def _risk(
    risk_id: str,
    severity: str,
    title: str,
    evidence: list[str],
    impact: list[str],
    mitigation: str,
    **extra: Any,
) -> dict:
    row = {
        "id": risk_id,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "impact": impact,
        "mitigation": mitigation,
    }
    row.update(extra)
    return row


# ── Detectors ──────────────────────────────────────────────────────────────


def detect_intro(ref_dir: Path) -> tuple[list[dict], int | None]:
    contract = _load(ref_dir, "states/splash/contract.json")
    if isinstance(contract, dict) and contract.get("detected") is True:
        overlay = _as_dict(contract.get("overlay"))
        coverage = overlay.get("maxCoverage")
        if overlay.get("everVisible") and isinstance(coverage, int | float) and (
            coverage >= _INTRO_MIN_COVERAGE
        ):
            timing = _as_dict(contract.get("exitTiming"))
            duration = timing.get("durationMs")
            evidence = [
                "states/splash/contract.json detected=true",
                f"states/splash/contract.json overlay.selector={overlay.get('selector')!r} "
                f"maxCoverage={coverage}",
            ]
            if isinstance(duration, int | float) and not isinstance(duration, bool) and duration > 0:
                settle = int(math.ceil(duration)) + _INTRO_SETTLE_MARGIN_MS
                evidence.append(f"states/splash/contract.json exitTiming.durationMs={duration}")
                return [
                    _risk(
                        "intro-overlay",
                        "caution",
                        f"Intro overlay covers the page for ~{int(duration)}ms",
                        evidence,
                        [
                            "hover/click candidate census",
                            "runtime-dom-parity (canvas/media census)",
                            "hover-fallback probe",
                            "live-parity / section-compare first frames",
                            "splash-lifecycle",
                        ],
                        f"Measure after the intro ends (~{int(duration)}ms; settle "
                        f"{settle}ms): every live probe must wait for the overlay "
                        "to exit before sampling, and the impl must replay the same "
                        "intro (IntroAnimation from generation-plan introAnimation).",
                        introExitMs=int(duration),
                    )
                ], settle
            evidence.append(
                "states/splash/contract.json exitTiming.durationMs=null "
                f"(overlay.exitObserved={overlay.get('exitObserved')})"
            )
            return [
                _risk(
                    "intro-overlay",
                    "caution",
                    "Full-viewport overlay seen at load; exit not timed",
                    evidence,
                    ["every live probe that samples right after load", "splash-lifecycle"],
                    "Re-run Phase A splash capture (capture-states.sh) with a longer "
                    "window to time the exit; until then treat first-load measurements "
                    "as unreliable.",
                )
            ], None
    interactions = _load(ref_dir, "interactions-detected.json")
    if isinstance(interactions, dict) and interactions.get("hasPreloader") is True:
        return [
            _risk(
                "intro-overlay",
                "caution",
                "Preloader detected (duration unknown)",
                ["interactions-detected.json hasPreloader=true"],
                ["every live probe that samples right after load", "splash-lifecycle"],
                "Run Phase A splash capture (capture-states.sh) to time the preloader "
                "exit, and capture dom-state-diff.json (Step 2.6-pre).",
            )
        ], None
    return [], None


def _bundle_markers(ref_dir: Path, markers: tuple[str, ...], limit: int = 3) -> list[str]:
    hits: list[str] = []
    bundles = ref_dir / "bundles"
    if not bundles.is_dir():
        return hits
    for path in sorted(bundles.glob("*.js")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for marker in markers:
            if marker in text:
                hits.append(f"bundles/{path.name} contains {marker!r}")
                break
        if len(hits) >= limit:
            break
    return hits


def detect_canvas(ref_dir: Path) -> list[dict]:
    det = _load(ref_dir, "canvas-webgl-detection.json")
    if not isinstance(det, dict) or not det.get("hasCanvas"):
        return []
    canvases = [c for c in det.get("canvases") or [] if isinstance(c, dict)]
    largest = max(canvases, key=lambda c: c.get("area") or 0, default={})
    size = f"{largest.get('width')}x{largest.get('height')}" if largest else "unknown"
    area = largest.get("area") or 0
    webgl = bool(det.get("hasWebGL")) or str(det.get("primaryRenderType") or "").startswith("webgl")
    evidence = [
        f"canvas-webgl-detection.json primaryRenderType={det.get('primaryRenderType')!r} "
        f"hasWebGL={det.get('hasWebGL')} canvasCount={det.get('canvasCount')}",
        f"canvas-webgl-detection.json canvases[] largest={size}",
    ]
    if det.get("hasPhysics"):
        evidence.append(f"canvas-webgl-detection.json physicsEngine={det.get('physicsEngine')!r}")
    if webgl:
        evidence.extend(_bundle_markers(ref_dir, _WEBGL_LIBRARY_MARKERS))
        return [
            _risk(
                "webgl-canvas",
                "caution",
                f"WebGL canvas ({size}) — pixels cannot be matched",
                evidence,
                [
                    "section-compare AE/SSIM on canvas sections",
                    "runtime-dom-parity canvas census",
                    "video-motion-compare",
                ],
                "Treat the WebGL canvas as approximate: match placement, size, layering "
                "and entry timing, not pixels. Reuse the reference scene library "
                "(canvas-webgl-extraction.md); mask the canvas region in pixel "
                "comparisons (asset-substitution / STRUCTURAL_ONLY) and say so in closeout.",
            )
        ]
    if area >= _VIEWPORT_AREA * _CANVAS_2D_MIN_AREA_RATIO:
        return [
            _risk(
                "canvas-2d",
                "caution",
                f"Large 2D canvas ({size})",
                evidence,
                ["section-compare AE/SSIM on canvas sections", "runtime-dom-parity canvas census"],
                "Recover the drawing code from bundles (canvas-webgl-extraction.md); "
                "if it is procedural/random, compare placement and size, not pixels.",
            )
        ]
    return []


def detect_video(ref_dir: Path) -> list[dict]:
    media = _load(ref_dir, "runtime-media.json")
    if not isinstance(media, dict):
        return []
    videos = [v for v in media.get("videos") or [] if isinstance(v, dict)]
    risks: list[dict] = []
    for index, video in enumerate(videos):
        rect = _as_dict(video.get("rect"))
        try:
            y, w, h = float(rect.get("y", 0)), float(rect.get("w", 0)), float(rect.get("h", 0))
        except (TypeError, ValueError):
            continue
        in_first_view = video.get("phase") == "initial" and y < _VIEWPORT_H and y + h > 0
        if in_first_view and w * h >= _VIEWPORT_AREA * _HERO_VIDEO_MIN_AREA_RATIO:
            src = video.get("currentSrc") or video.get("src") or video.get("poster") or ""
            risks.append(
                _risk(
                    "video-hero",
                    "caution",
                    f"Hero video ({int(w)}x{int(h)}) in the first viewport",
                    [
                        f"runtime-media.json videos[{index}].rect={rect} phase=initial",
                        f"runtime-media.json videos[{index}].currentSrc={src!r}",
                    ],
                    ["section-compare / video-motion-compare on the hero", "required-media-coverage"],
                    "Ship the original video file (required-media.json → impl/public) "
                    "with the same autoplay/loop/muted/poster; compare the hero at a "
                    "pinned currentTime (live-parity pin mode) and judge motion with "
                    "video-play-proof, not frame pixels.",
                )
            )
            break
    if len(videos) >= _VIDEO_HEAVY_MIN_COUNT:
        risks.append(
            _risk(
                "video-heavy",
                "caution",
                f"{len(videos)} videos on the page",
                [f"runtime-media.json totals.video={len(videos)}"],
                ["required-media-coverage", "section-compare on video sections"],
                "Download every required video before Step 7 and pin playback time "
                "during comparisons; decoding timing makes frame-exact matching flaky.",
            )
        )
    return risks


def detect_scroll_motion(ref_dir: Path) -> list[dict]:
    evidence: list[str] = []
    total = 0
    dump = _load(ref_dir, "animation-runtime-dump.json")
    if isinstance(dump, dict) and isinstance(dump.get("scrollLinkedStyles"), list):
        count = len(dump["scrollLinkedStyles"])
        if count:
            total += count
            evidence.append(f"animation-runtime-dump.json scrollLinkedStyles[] count={count}")
    spec = _load(ref_dir, "transition-spec.json")
    if isinstance(spec, dict) and not spec.get("placeholder"):
        rows = [t for t in spec.get("transitions") or [] if isinstance(t, dict)]
        scroll_rows = [t for t in rows if _SCROLL_TRIGGER_RE.search(str(t.get("trigger") or ""))]
        if scroll_rows:
            total += len(scroll_rows)
            evidence.append(
                f"transition-spec.json transitions[] scroll/in-view triggers={len(scroll_rows)}"
            )
    if total < _SCROLL_MOTION_MIN_COUNT:
        return []
    return [
        _risk(
            "scroll-motion-heavy",
            "caution",
            f"Heavy scroll-linked / in-view motion ({total} signals)",
            evidence,
            [
                "section-compare / live-parity (elements measured before they reveal)",
                "reveal-trigger / transition-fires / scroll-state-machine",
            ],
            "Scroll each section into view and wait for its reveal to finish before "
            "measuring; implement every scroll/in-view row from transition-spec.json "
            "(not from screenshots) and expect the reveal/scroll-state rows of "
            "verification-plan.json to carry the motion verdict.",
            signalCount=total,
        )
    ]


def detect_paid_fonts(ref_dir: Path) -> list[dict]:
    paid = _load(ref_dir, "paid-features.json")
    if not isinstance(paid, dict):
        return []
    risks: list[dict] = []
    for index, font in enumerate(paid.get("paidFonts") or []):
        if not isinstance(font, dict):
            continue
        decision = font.get("decision")
        name = font.get("family") or font.get("cdn") or f"#{index}"
        # Stable id + identity: the font itself (family + CDN), never its list
        # position, so a re-extracted list cannot move a decision to another font.
        identity = f"paid-font|{font.get('family')}|{font.get('cdn')}"
        suffix = _short(identity)
        evidence = [
            f"paid-features.json paidFonts[{index}] family={font.get('family')!r} "
            f"cdn={font.get('cdn')!r} decision={decision!r}",
        ]
        if decision in (None, ""):
            # A caution, not a blocker: Step 5c-c has the agent set the
            # decision by rule, and the paid-features gate fails while it is
            # null. Blocker decisions come only from the user.
            risks.append(
                _risk(
                    f"paid-font-undecided-{suffix}",
                    "caution",
                    f"Paid font {name} has no license/substitute decision",
                    evidence,
                    ["paid-features gate", "font-parity", "every text-bearing section-compare"],
                    "Set paid-features.json decision per Step 5c-c before Step 7: use "
                    "(licensed), substitute (free metric-compatible font + "
                    "asset-substitution.json fonts[]), or skip a false positive.",
                    identity=identity,
                )
            )
        elif decision == "use":
            risks.append(
                _risk(
                    f"paid-font-license-{suffix}",
                    "caution",
                    f"Paid font {name} used as-is (license required)",
                    evidence,
                    ["font-parity"],
                    "Ship the licensed binaries under impl/public/fonts; font-parity "
                    "fails if the font silently falls back.",
                )
            )
        elif decision == "substitute":
            risks.append(
                _risk(
                    f"paid-font-substitute-{suffix}",
                    "caution",
                    f"Paid font {name} will be substituted",
                    evidence,
                    ["font-parity", "text-bearing section-compare"],
                    "Declare the substitute in asset-substitution.json fonts[]; expect "
                    "glyph-level text diffs and report them as intentional.",
                )
            )
    return risks


def detect_access(ref_dir: Path) -> list[dict]:
    head = _load(ref_dir, "head.json")
    title = head.get("title") if isinstance(head, dict) else None
    if isinstance(title, str) and _BOT_CHALLENGE_TITLE_RE.search(title):
        return [
            _risk(
                "bot-challenge",
                "blocker",
                "Captured page is a bot challenge, not the site",
                [f"head.json title={title!r}"],
                ["every capture and extraction artifact"],
                "Ask the user: re-capture through an allowed browser profile that "
                "passes the challenge, or stop.",
                unclonable={"gate": "extraction", "category": "bot-challenge"},
                identity=f"bot-challenge|{title.strip()}",
            )
        ]
    return []


def detect_state_blockers(ref_dir: Path) -> list[dict]:
    """Surface reasons already recorded in pipeline-state.json unclonable_reasons."""
    risks: list[dict] = []
    for index, reason in enumerate(_state_blocker_reasons(ref_dir)):
        category = str(reason.get("category") or "unclonable")
        identity = f"unclonable|{reason.get('gate')}|{category}|{reason.get('reason')}"
        risks.append(
            _risk(
                f"unclonable-{re.sub(r'[^A-Za-z0-9._-]+', '-', category)}-{_short(identity)}",
                "blocker",
                f"Recorded blocker: {category}",
                [
                    f"pipeline-state.json unclonable_reasons[{index}] gate="
                    f"{reason.get('gate')!r} reason={str(reason.get('reason'))[:160]!r}"
                ],
                ["the whole run (terminal state recorded)"],
                "; ".join(reason.get("fallback_suggestions") or [])
                or "Ask the user how to proceed; recovery uses `python -m ui_clone.state recover`.",
                fromState=True,
                identity=identity,
            )
        )
    return risks


def _walk_nodes(node: Any) -> Any:
    if isinstance(node, dict):
        yield node
        for child in node.get("children") or []:
            yield from _walk_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_nodes(child)


def detect_embeds(ref_dir: Path) -> list[dict]:
    structure = _load(ref_dir, "structure.json")
    if structure is None:
        return []
    found: dict[str, list[str]] = {}
    for node in _walk_nodes(structure):
        if str(node.get("tag") or "").lower() != "iframe":
            continue
        src = str(node.get("src") or node.get("data-src") or "")
        if not src:
            continue
        kind = "other"
        lowered = src.lower()
        for name, hosts in _EMBED_HOSTS:
            if any(host in lowered for host in hosts):
                kind = name
                break
        found.setdefault(kind, []).append(src)
    if not found:
        return []
    evidence = [
        f"structure.json iframe[{kind}] x{len(srcs)} e.g. src={srcs[0][:120]!r}"
        for kind, srcs in sorted(found.items())
    ]
    total = sum(len(v) for v in found.values())
    return [
        _risk(
            "third-party-embeds",
            "caution",
            f"{total} third-party iframe embed(s) ({', '.join(sorted(found))})",
            evidence,
            ["section-compare on embed sections", "live-parity image/asset census"],
            "Embed the same third-party URL (allow/allowfullscreen preserved) rather "
            "than re-drawing it; its content is rendered by the provider, so compare "
            "the frame box and mask its interior in pixel comparisons.",
        )
    ]


DETECTORS = (
    detect_state_blockers,
    detect_access,
    detect_paid_fonts,
    detect_canvas,
    detect_video,
    detect_scroll_motion,
    detect_embeds,
)


# ── Report assembly ────────────────────────────────────────────────────────


def is_user_decision(decision: Any) -> bool:
    """A decision counts only with user provenance (prompt hook or the user's
    own terminal); anything else, including pre-provenance records, is open."""
    if not isinstance(decision, dict) or decision.get("decision") not in ("proceed", "stop"):
        return False
    return _as_dict(decision.get("provenance")).get("source") in USER_DECISION_SOURCES


def user_decisions(report: dict) -> dict[str, dict]:
    return {
        rid: d for rid, d in _as_dict(report.get("decisions")).items() if is_user_decision(d)
    }


def open_blockers(report: dict) -> list[dict]:
    decisions = user_decisions(report)
    return [
        r
        for r in report.get("risks") or []
        if isinstance(r, dict) and r.get("severity") == "blocker" and r.get("id") not in decisions
    ]


def stopped_blockers(report: dict) -> list[str]:
    return [rid for rid, d in user_decisions(report).items() if d.get("decision") == "stop"]


def decision_instructions(ref_dir: Path | str, risk_ids: list[str]) -> str:
    """What the USER sends to record a decision (the agent never records it)."""
    ids = ", ".join(risk_ids) or "<risk-id>"
    first = risk_ids[0] if risk_ids else "<risk-id>"
    return (
        f"Stop and ask the user how to proceed on {ids}; do not record the answer "
        "yourself (agent --decide calls are denied). In Claude Code the user replies "
        f"with one line per blocker: `ui-clone decide {first} proceed <note>` or "
        f"`ui-clone decide {first} stop <note>`. Without prompt hooks (Codex) the user "
        f"runs in their own terminal: python -m {PRODUCER} {ref_dir} --decide {first} "
        '--decision proceed|stop --note "<answer>"'
    )


def recover_instructions(ref_dir: Path | str, risk_ids: list[str]) -> str:
    """State blockers are lifted by operator recovery, not a decision."""
    return (
        f"{', '.join(risk_ids)} come(s) from pipeline-state.json unclonable_reasons. "
        "Ask the user; if they resolved the constraint, they run "
        f"python -m ui_clone.state recover {ref_dir} --gate <gate> --force "
        f'--reason "<what was resolved>", then re-run python -m {PRODUCER} {ref_dir}. '
        "To stop, the user records `stop` as for any blocker."
    )


def _summary(risks: list[dict], decisions: dict) -> str:
    blockers = [r for r in risks if r["severity"] == "blocker"]
    cautions = [r for r in risks if r["severity"] == "caution"]
    if not risks:
        return "Clonability: no risks found in the captured evidence."
    parts = [f"Clonability: {len(blockers)} blocker(s), {len(cautions)} caution(s)."]
    for r in risks:
        state = ""
        if r["severity"] == "blocker":
            d = decisions.get(r["id"])
            state = f" [decision: {d['decision']}]" if isinstance(d, dict) else " [needs your decision]"
        parts.append(f"- {r['severity'].upper()} {r['title']}{state} -> {r['mitigation']}")
    return "\n".join(parts)


def build_report(ref_dir: Path, previous: dict | None = None) -> dict:
    ref_dir = Path(ref_dir)
    risks, intro_settle = detect_intro(ref_dir)
    for detector in DETECTORS:
        risks.extend(detector(ref_dir))
    for r in risks:
        r.setdefault("identity", r["id"])
    risks.sort(key=lambda r: (r["severity"] != "blocker", r["id"]))
    identities = {r["id"]: r["identity"] for r in risks}
    prev_decisions = _as_dict(_as_dict(previous).get("decisions"))
    # Carry a decision forward only when the same id still names the same
    # evidence (identity), so it can never move to a different risk.
    decisions = {
        rid: d
        for rid, d in prev_decisions.items()
        if isinstance(d, dict) and rid in identities and d.get("identity") == identities[rid]
    }
    hashes = source_hashes(ref_dir)
    present = [rel for rel, digest in hashes.items() if digest]
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now(),
        "risks": risks,
        "decisions": decisions,
        "verification": {"introSettleMs": intro_settle},
        "inputsPresent": present,
        "inputsMissing": [rel for rel in INPUTS if rel not in present],
        "provenance": {
            "source": PRODUCER,
            "hashAlgorithm": "sha256",
            "sourceHashes": hashes,
        },
    }
    report["status"] = (
        "stopped" if stopped_blockers(report) else "blocked" if open_blockers(report) else "ok"
    )
    report["summary"] = _summary(risks, user_decisions(report))
    return report


def load_report(ref_dir: Path) -> dict | None:
    data = _load(Path(ref_dir), REPORT_NAME)
    return data if isinstance(data, dict) else None


def _atomic_write(ref_dir: Path, report: dict) -> None:
    path = ref_dir / REPORT_NAME
    tmp = path.with_name(f".{REPORT_NAME}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_report(ref_dir: Path) -> dict:
    ref_dir = Path(ref_dir)
    report = build_report(ref_dir, load_report(ref_dir))
    _atomic_write(ref_dir, report)
    from ui_clone.state import PipelineState

    # Durable "this run had a report" record: the gate's legacy warn-only
    # mode never applies once it is set, even if the report is deleted.
    PipelineState(component=ref_dir.name).record_clonability_report(ref_dir)
    return report


def record_decision(
    ref_dir: Path, risk_id: str, decision: str, note: str, *, provenance: dict
) -> dict:
    """Record the USER's answer for a blocker. `provenance.source` must be a
    user source (USER_DECISION_SOURCES). `stop` also records the canonical
    unclonable reason in pipeline-state.json."""
    ref_dir = Path(ref_dir)
    if _as_dict(provenance).get("source") not in USER_DECISION_SOURCES:
        raise ValueError("a blocker decision must come from the user (prompt or their terminal)")
    if decision not in ("proceed", "stop"):
        raise ValueError(f"decision must be proceed or stop, not {decision!r}")
    report = load_report(ref_dir)
    if report is None:
        raise ValueError(f"{REPORT_NAME} missing; run python -m {PRODUCER} {ref_dir} first")
    stale = stale_inputs(ref_dir, report)
    if stale:
        raise ValueError(
            f"{REPORT_NAME} is stale vs {', '.join(stale[:4])}; re-run "
            f"python -m {PRODUCER} {ref_dir} first, then decide against the current risks"
        )
    risk = next((r for r in report.get("risks") or [] if r.get("id") == risk_id), None)
    if risk is None:
        raise ValueError(f"unknown risk id {risk_id!r}")
    if risk.get("severity") != "blocker":
        raise ValueError(f"{risk_id!r} is a caution; only blockers take a decision")
    if decision == "proceed" and risk.get("fromState"):
        raise ValueError(
            f"{risk_id!r} is already a recorded unclonable reason; lifting it is an operator "
            f"recovery: python -m ui_clone.state recover {ref_dir} --gate <gate> --force "
            '--reason "<user decision>", then re-run this producer'
        )
    if decision == "stop" and not risk.get("fromState"):
        from ui_clone.state import PipelineState

        target = risk.get("unclonable") or {}
        state = PipelineState.load(ref_dir)
        state.record_unclonable(
            gate=str(target.get("gate") or "pre-generate"),
            reason=f"clonability {risk_id}: {risk.get('title')} (user decision: stop; {note})",
            ref_dir=ref_dir,
            category=target.get("category") or "unclonable",
            detail={"source": REPORT_NAME, "riskId": risk_id, "evidence": risk.get("evidence")},
        )
    decisions = _as_dict(report.get("decisions"))
    decisions[risk_id] = {
        "decision": decision,
        "note": note,
        "decidedAt": _now(),
        "identity": risk.get("identity", risk_id),
        "provenance": {**provenance, "at": provenance.get("at") or _now()},
    }
    report["decisions"] = decisions
    report["status"] = (
        "stopped" if stopped_blockers(report) else "blocked" if open_blockers(report) else "ok"
    )
    report["summary"] = _summary(report.get("risks") or [], user_decisions(report))
    _atomic_write(ref_dir, report)
    return report


def _report_dirs(project_root: Path) -> list[Path]:
    dirs: list[Path] = []
    for base in (project_root / "tmp" / "ref", project_root / ".ui-clone" / "runs"):
        if base.is_dir():
            dirs.extend(sorted(p.parent for p in base.glob(f"*/{REPORT_NAME}") if p.is_file()))
    return dirs


def _risk_ids(ref_dir: Path) -> set[str]:
    risks = (load_report(ref_dir) or {}).get("risks") or []
    return {str(r.get("id")) for r in risks if isinstance(r, dict)}


def apply_prompt_decisions(project_root: Path, session_id: str, prompt: str) -> str | None:
    """Record every `ui-clone decide ...` line of a USER prompt (called only
    from the UserPromptSubmit hook). Returns a context message, or None when the
    prompt carries no decision line."""
    matches = list(DECIDE_PROMPT_RE.finditer(prompt or ""))
    if not matches:
        return None
    lines: list[str] = []
    # One prompt answering the same blocker both ways records neither.
    # Keyed on the resolved report dir when the line resolves to exactly one
    # (`hero/bot-challenge` and bare `bot-challenge` are the same blocker).
    report_dirs = _report_dirs(Path(project_root))
    risk_ids = {d: _risk_ids(d) for d in report_dirs}

    def _candidates(m: re.Match[str]) -> list[Path]:
        ref_name, risk_id = m.group("ref"), m.group("risk")
        return [d for d in report_dirs if (ref_name is None or d.name == ref_name) and risk_id in risk_ids[d]]

    def _key(m: re.Match[str]) -> tuple[str | None, str]:
        hits = _candidates(m)
        return (str(hits[0]) if len(hits) == 1 else m.group("ref"), m.group("risk"))

    answers: dict[tuple[str | None, str], set[str]] = {}
    for m in matches:
        answers.setdefault(_key(m), set()).add(m.group("decision"))
    seen: set[tuple[str | None, str]] = set()
    for m in matches:
        risk_id, decision = m.group("risk"), m.group("decision")
        key = _key(m)
        if key in seen:
            continue
        seen.add(key)
        if len(answers[key]) > 1:
            lines.append(
                f"ui-clone decide {risk_id}: NOT recorded: this prompt answers it both "
                "proceed and stop; resend one line with the single answer."
            )
            continue
        note = m.group("note").strip()
        if not note or _PLACEHOLDER_RE.search(note):
            lines.append(
                f"ui-clone decide {risk_id}: NOT recorded: the note is "
                f"{'empty' if not note else 'a template placeholder'}; resend as "
                f"`ui-clone decide {risk_id} {decision} <your reason in words>`."
            )
            continue
        candidates = _candidates(m)
        if len(candidates) != 1:
            if candidates:
                names = ", ".join(d.name for d in candidates)
                hint = (
                    f"{len(candidates)} reports carry it ({names}); resend as "
                    f"`ui-clone decide <component>/{risk_id} {decision} <note>`"
                )
            else:
                hint = f"no {REPORT_NAME} carries that risk id; check the id in the report"
            lines.append(f"ui-clone decide {risk_id}: NOT recorded: {hint}.")
            continue
        ref_dir = candidates[0]
        provenance = {
            "source": "user-prompt",
            "sessionId": session_id,
            "at": _now(),
            "line": m.group(0).strip()[:300],
        }
        try:
            report = record_decision(ref_dir, risk_id, decision, note, provenance=provenance)
        except ValueError as exc:
            lines.append(f"ui-clone decide {risk_id}: NOT recorded: {exc}")
            continue
        status = report.get("status")
        if status == "stopped":
            follow = "Do not generate: the run is recorded unclonable."
        elif status == "ok":
            follow = f"Continue with python -m ui_clone.goal {ref_dir}."
        else:
            follow = "Other blockers still await the user's decision."
        lines.append(
            f"Recorded the user's decision {decision!r} for {risk_id} in "
            f"{ref_dir}/{REPORT_NAME} (status: {status}). {follow}"
        )
    return "\n".join(lines)


def render_table(report: dict, ref_dir: Path | None = None) -> str:
    risks = report.get("risks") or []
    lines = [f"Clonability report — status: {report.get('status')}"]
    if not risks:
        lines.append("  (no risks found in the captured evidence)")
    else:
        width = max(len(str(r.get("id"))) for r in risks)
        lines.append(f"  {'SEVERITY':<8}  {'ID':<{width}}  TITLE")
        for r in risks:
            lines.append(f"  {str(r.get('severity')):<8}  {str(r.get('id')):<{width}}  {r.get('title')}")
    lines.append("")
    lines.append(str(report.get("summary") or ""))
    settle = (report.get("verification") or {}).get("introSettleMs")
    if settle:
        lines.append(f"verification.introSettleMs={settle}")
    lines.append("")
    pending = [str(r.get("id")) for r in open_blockers(report)]
    if pending:
        lines.append(
            "NEXT: relay the summary to the user and STOP. "
            + decision_instructions(ref_dir or "<ref-dir>", pending)
            + " (stop records the canonical unclonable reason)."
        )
    else:
        lines.append(
            "NEXT: relay the summary to the user and continue without waiting; carry each "
            "caution mitigation into the plan (generation-planner copies them to "
            "clonabilityMitigations[]). Re-run after Step 6b-bis: pre-generate requires "
            "this report current."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m {PRODUCER}",
        description="Write tmp/ref/<c>/clonability-report.json from existing artifacts (no browser).",
        # No prefix matching: an abbreviation (`--decid`) must not reach --decide.
        allow_abbrev=False,
    )
    parser.add_argument("ref_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="print the report JSON")
    parser.add_argument(
        "--decide",
        metavar="RISK_ID",
        help="record YOUR answer for a blocker (user terminal only; agents must ask the user)",
    )
    parser.add_argument("--decision", choices=("proceed", "stop"))
    parser.add_argument("--note", default="", help="the user's answer, verbatim")
    args = parser.parse_args(argv)
    if not args.ref_dir.is_dir():
        print(f"clonability: ref dir not found: {args.ref_dir}", file=sys.stderr)
        return 2
    if args.decide:
        if not args.decision or not args.note.strip():
            print("clonability: --decide needs --decision and a non-empty --note", file=sys.stderr)
            return 2
        # An agent host's tool shell carries its env markers; the user's own
        # terminal does not. (The pre-bash hook also denies --decide from an
        # agent Bash call, but shell indirection can hide text from it.)
        marker = agent_host_marker()
        if marker is not None:
            print(
                f"clonability: --decide refused: it runs under an agent host ({marker} is set). "
                "Only the user records blocker decisions: ask the user to send the "
                "`ui-clone decide <risk-id> proceed|stop <note>` prompt line, or to run this "
                "command in their own terminal outside the agent.",
                file=sys.stderr,
            )
            return 2
        # The agent's Bash tool has no terminal on stdin; the user's shell does.
        if not sys.stdin.isatty():
            print(
                "clonability: --decide records the USER's answer and runs only from the "
                "user's own terminal. Agents: stop and ask the user instead.",
                file=sys.stderr,
            )
            return 2
        try:
            report = record_decision(
                args.ref_dir,
                args.decide,
                args.decision,
                args.note.strip(),
                provenance={"source": "user-terminal", "at": _now()},
            )
        except ValueError as exc:
            print(f"clonability: {exc}", file=sys.stderr)
            return 2
    else:
        report = write_report(args.ref_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_table(report, args.ref_dir))
    # 0 = proceed; 1 = a blocker awaits the user's decision or was stopped.
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
