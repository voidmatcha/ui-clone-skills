#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODE, SESSION, REF_URL, IMPL_URL, REF_DIR_ARG, MEAS_FILE, REPO_ROOT = sys.argv[1:8]
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ui_clone.evidence_validation import (  # noqa: E402
    bounded_score,
    load_strict_json_text,
    visual_fidelity_semantic_error,
)

REF_DIR = Path(REF_DIR_ARG)
ARTIFACT = REF_DIR / "visual-fidelity-judge.json"
MOTION_DIR = REF_DIR / "visual-fidelity-motion"

UTC = timezone.utc  # noqa: UP017 - macOS /usr/bin/python3 is still 3.9.

AXES = ("layout", "text", "color", "animation")
STATIC_THRESHOLD = 7.0        # per-section pass floor
AXIS_THRESHOLD = 7            # per-motion-axis pass floor
N_DEPTHS = 6                  # motion scroll samples
PAIR_TOLERANCE = 150         # px; |refY-implY| above this = divergent scroll, unpaired
SEV_PENALTY = {"critical": 4.0, "major": 2.0, "minor": 0.5}
# Post-scroll settle is DERIVED per-ref from transition durations — see
# derive_settle_ms() / the J-2 constants below (no hardcoded settle constant).


def _timeout_seconds() -> int:
    try:
        return max(30, int(os.environ.get("MOTION_JUDGE_TIMEOUT_SEC", "300")))
    except ValueError:
        return 300


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_artifact(payload: dict) -> None:
    payload.setdefault("schemaVersion", 1)
    payload.setdefault("generatedAt", now_iso())
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(payload, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    try:
        return load_strict_json_text(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: UP038


def decode(raw: str) -> Any:
    """agent-browser eval output may be double/triple JSON-encoded."""
    value = (raw or "").strip()
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = load_strict_json_text(value)
            except (json.JSONDecodeError, ValueError):
                break
        else:
            break
    return value


def first_json_object(text: str) -> Any:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        return load_strict_json_text(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


# ── derived settle window (J-2) ──────────────────────────────────────────────
SETTLE_FLOOR_MS = 1200        # judge-specific floor (see derive_settle_ms)


def derive_settle_ms(ref_dir: Path) -> int:
    """Post-scroll settle window in ms, DERIVED not hardcoded (1.5s was nvti-tuned).

    REUSES the sibling H9 derivation ui_clone.section_capture.derive_settle_seconds
    (rest-reeval margin 0.4s + longest transition duration, cap 4.0s) so the two
    never drift — a second copy of the same rule WILL diverge (team-lead J-2 note).
    We convert seconds→ms and raise the FLOOR to 1200ms: a VLM judging a *settled*
    frame needs a more conservative minimum than section-capture's 0.5s (a
    mid-transition judge frame produces phantom motion findings). The margin and
    the 4.0s cap come straight from the sibling. Absent/unparseable/duration-free
    spec → the sibling returns its 0.5s floor which we lift to 1200ms; a 1.06s
    transition → 400+1060 = 1460ms; a huge duration → the sibling's 4000ms cap."""
    from ui_clone.section_capture import derive_settle_seconds
    seconds = derive_settle_seconds(ref_dir / "transition-spec.json")
    return max(SETTLE_FLOOR_MS, int(round(seconds * 1000)))


# ── static pass (cached VLM per section crop) ────────────────────────────────
def score_from_findings(findings: Any) -> float:
    pen = 0.0
    for f in findings or []:
        if isinstance(f, dict):
            pen += SEV_PENALTY.get(str(f.get("severity", "")).lower(), 1.0)
    return max(0.0, min(10.0, 10.0 - pen))


def issues_from_findings(findings: Any) -> list[str]:
    out = []
    for f in findings or []:
        if isinstance(f, dict) and f.get("description"):
            out.append(str(f["description"]))
    return out


def resolve_impl_root() -> Path | None:
    """Locate the impl source root so crop staleness can be judged against the
    newest impl change. Prefer the IMPL_ROOT the dispatcher exports; else run
    the shared find-impl-root.sh resolver against this ref-dir."""
    env = os.environ.get("IMPL_ROOT")
    if env and Path(env).is_dir():
        return Path(env)
    resolver = Path(REPO_ROOT) / "scripts" / "extract" / "find-impl-root.sh"
    if resolver.is_file():
        try:
            proc = subprocess.run(["bash", str(resolver), str(REF_DIR)],
                                  capture_output=True, text=True, timeout=30)
        except Exception:
            return None
        if proc.returncode == 0:
            for line in (proc.stdout or "").splitlines():
                cand = line.strip()
                if cand and Path(cand).is_dir():
                    return Path(cand)
    return None


def impl_newest_mtime(impl_root: Path | None) -> float | None:
    """Newest mtime across the impl SOURCE tree (this check's declared impl
    inputs), or None if it cannot be determined. A crop older than this is
    stale — it depicts an impl that has since changed."""
    if impl_root is None:
        return None
    try:
        from ui_clone.check_inputs import _declared_side_files, get_check_inputs

        spec = get_check_inputs("visual-fidelity-judge")
        if spec is None or not spec.impl:
            return None
        files = _declared_side_files(impl_root, spec.impl, "implementation")
        return max(path.stat().st_mtime for _relative, path in files)
    except Exception:
        return None


def crop_sets() -> list:
    """Candidate {ref,impl} crop directories, newest-mtime-tagged. section-compare
    historically wrote sections/{ref,impl}/ but now also emits per-viewport
    sections/viewports/<WxH>/{ref,impl}/ — probe both and let the caller prefer
    the freshest set. Returns [(name, ref_dir, impl_dir, newest_mtime)]."""
    # codex C1: discover EVERY per-viewport crop set, not just 1440x900. A run
    # captured at another viewport (VIEWPORTS=1280x800, mobile-only) wrote its
    # fresh crops under sections/viewports/<WxH>/ and the hardcoded 1440x900 entry
    # never saw them — the judge then fell back to the (possibly stale) top-level
    # sections/ crops or ran motion-only, scoring the advisory verdict on the wrong
    # crops. Glob the viewports dir so max-mtime below picks the truly freshest set.
    candidates = [
        ("sections", REF_DIR / "sections" / "ref", REF_DIR / "sections" / "impl"),
    ]
    vp_root = REF_DIR / "sections" / "viewports"
    if vp_root.is_dir():
        for vp in sorted(vp_root.iterdir()):
            if vp.is_dir():
                candidates.append(
                    (f"sections/viewports/{vp.name}", vp / "ref", vp / "impl")
                )
    out = []
    for name, refd, impld in candidates:
        if refd.is_dir() and impld.is_dir():
            pngs = list(refd.glob("*.png"))
            if pngs:
                newest = max((p.stat().st_mtime for p in pngs), default=0.0)
                out.append((name, refd, impld, newest))
    return out


def static_plan() -> tuple:
    """Choose the freshest crop set and split its pairs into judged vs
    stale-skipped (crop older than the newest impl change). Returns
    (crop_set_name, judged[(label,ref_png,impl_png)], skipped[{label,reason}],
    impl_root_resolved: bool, note)."""
    sets = crop_sets()
    if not sets:
        return None, [], [], False, None
    name, ref_crops, impl_crops, _ = max(sets, key=lambda s: s[3])
    impl_root = resolve_impl_root()
    impl_mtime = impl_newest_mtime(impl_root)
    judged = []
    skipped = []
    for ref_png in sorted(ref_crops.glob("*.png")):
        impl_png = impl_crops / ref_png.name
        if not impl_png.is_file():
            continue
        label = ref_png.stem
        if impl_mtime is not None and impl_mtime > 0:
            crop_mtime = min(ref_png.stat().st_mtime, impl_png.stat().st_mtime)
            if crop_mtime < impl_mtime:
                # A crop older than the newest impl change describes an impl that
                # no longer exists (left by a previous loop's section-compare).
                skipped.append({"label": label, "reason": "stale-crop"})
                continue
        judged.append((label, ref_png, impl_png))
    note = None
    if not judged and skipped:
        note = ("all section crops are stale (older than newest impl change) — "
                "static pass ran motion-only")
    elif impl_root is None:
        note = "impl-root unresolved — crop staleness not checked"
    return name, judged, skipped, (impl_root is not None), note


def run_static() -> tuple[list, list, str | None, str | None, str | None]:
    """Judge the freshest, non-stale crop pairs via the cached dispatcher.
    Returns (static_sections, static_skipped, crop_set, note, error). A
    dispatcher failure (claude CLI missing / timeout / invalid JSON) is infra —
    returned as an error so the run fails closed. Stale crops are excluded (never
    judged against a dead tree) and recorded in static_skipped."""
    crop_set, judged, skipped, _resolved, note = static_plan()
    if crop_set is None:
        return [], [], None, None, None  # no crops captured yet — motion still runs
    from ui_clone.visual_judge_dispatcher import (
        VisualJudgeError,
        dispatch_visual_judge,
    )
    sections = []
    for label, ref_png, impl_png in judged:
        try:
            findings = dispatch_visual_judge(REF_DIR, label, ref_png, impl_png)
        except VisualJudgeError as exc:
            return [], skipped, crop_set, note, f"visual-judge dispatch failed for '{label}': {exc}"
        flist = findings.get("findings") if isinstance(findings, dict) else None
        score = score_from_findings(flist)
        sections.append({
            "label": label,
            "score": round(score, 2),
            "verdict": "pass" if score >= STATIC_THRESHOLD else "fail",
            "issues": issues_from_findings(flist),
        })
    return sections, skipped, crop_set, note, None


# ── motion pass (live scroll sweep + one VLM call) ───────────────────────────
def ab(session: str, *args: str, timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["agent-browser", "--session", session, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout or ""
    except Exception:
        return False, ""


def ab_eval(session: str, js: str) -> tuple[Any, bool]:
    ok, raw = ab(session, "eval", js)
    if not ok or not raw.strip():
        return None, True
    return decode(raw), False


def detect_scroller_js() -> str:
    return (
        "(() => { var dh=document.documentElement.scrollHeight;"
        " var dc=document.documentElement.clientHeight;"
        " if(dh>dc+100) return JSON.stringify({sel:'__document__',sh:dh});"
        " var best=null;"
        " document.querySelectorAll('*').forEach(function(el){"
        "   var cs=getComputedStyle(el);"
        "   if((cs.overflowY==='auto'||cs.overflowY==='scroll'||cs.overflowY==='hidden')"
        "      && el.scrollHeight>el.clientHeight+100){"
        "     if(!best||el.scrollHeight>best.sh){best={el:el,sh:el.scrollHeight};}}});"
        " if(!best) return JSON.stringify({sel:'__document__',sh:dh});"
        " var sel=best.el.tagName.toLowerCase();"
        " if(best.el.id) sel+='#'+best.el.id;"
        " return JSON.stringify({sel:sel,sh:best.sh});"
        "})()"
    )


def scroll_js(sel: str, y: int) -> str:
    if sel == "__document__":
        return ("(() => { window.scrollTo(0," + str(y) + ");"
                " window.dispatchEvent(new Event('scroll')); return " + str(y) + "; })()")
    s = json.dumps(sel)
    return (
        "(() => { var w=document.querySelector(" + s + ");"
        " if(!w){ window.scrollTo(0," + str(y) + ");"
        " window.dispatchEvent(new Event('scroll')); return " + str(y) + "; }"
        " w.scrollTop=" + str(y) + "; w.dispatchEvent(new Event('scroll'));"
        " return w.scrollTop; })()"
    )


def scroller_of(session: str) -> tuple[str, int]:
    res, pf = ab_eval(session, detect_scroller_js())
    if pf or not isinstance(res, dict):
        return "__document__", 0
    sel_value = res.get("sel")
    sel = sel_value if isinstance(sel_value, str) else "__document__"
    sh = res.get("sh")
    sh_value = int(sh) if isinstance(sh, (int, float)) and not isinstance(sh, bool) else 0  # noqa: UP038
    return sel, sh_value


def readback_js(sel: str) -> str:
    """Read the ACTUAL current scroll position AFTER a settle. The scroll_js
    setters return their requested constant; a snap-back / smooth-scroll engine
    (Lenis, GSAP ScrollSmoother, CSS scroll-snap) lands somewhere else during
    the settle, so the true position must be read separately."""
    if sel == "__document__":
        return ("(() => { return JSON.stringify("
                "{y: Math.round(window.scrollY || window.pageYOffset || 0)}); })()")
    s = json.dumps(sel)
    return (
        "(() => { var w=document.querySelector(" + s + ");"
        " return JSON.stringify({y: Math.round(w ? w.scrollTop"
        " : (window.scrollY || window.pageYOffset || 0))}); })()"
    )


def read_pos(session: str, sel: str) -> int | None:
    """Actual settled scroll position, or None on a probe failure."""
    res, pf = ab_eval(session, readback_js(sel))
    if pf or not isinstance(res, dict) or not is_num(res.get("y")):
        return None
    return int(res["y"])


def run_motion() -> tuple[dict, str | None]:
    """Drive both live pages, sample N matched scroll depths (reading back the
    ref's ACTUAL settled position and re-targeting the impl to it), screenshot
    each PAIRED depth, and score the paired sequences with one claude --print
    motion-judge call. Returns (motion_dict, error). The dict always carries
    samples/unpairedSamples when the browser ran; a None error means success
    (motion_dict has 'axes'). Any infra/all-unpaired failure is an error
    string, never a silent pass."""
    if not shutil.which("agent-browser"):
        return {}, "agent-browser not found in PATH"
    if not shutil.which("claude"):
        return {}, "claude CLI not found in PATH"

    prompt_path = PROMPT_PATH
    if not prompt_path.is_file():
        return {}, f"motion-judge prompt template missing: {prompt_path}"
    run_with_timeout = Path(REPO_ROOT) / "scripts" / "lib" / "run_with_timeout.py"
    if not run_with_timeout.is_file():
        return {}, f"run_with_timeout.py missing: {run_with_timeout}"

    ref_s, impl_s = f"{SESSION}-vj-ref", f"{SESSION}-vj-impl"
    ref_shots = MOTION_DIR / "ref"
    impl_shots = MOTION_DIR / "impl"
    shutil.rmtree(MOTION_DIR, ignore_errors=True)
    ref_shots.mkdir(parents=True, exist_ok=True)
    impl_shots.mkdir(parents=True, exist_ok=True)

    try:
        ok_ref, _ = ab(ref_s, "open", REF_URL)
        ok_impl, _ = ab(impl_s, "open", IMPL_URL)
        if not (ok_ref and ok_impl):
            failed = [n for n, ok in (("ref", ok_ref), ("impl", ok_impl)) if not ok]
            return {}, f"agent-browser open failed for session(s): {', '.join(failed)}"
        ab(ref_s, "set", "viewport", "1440", "900")
        ab(impl_s, "set", "viewport", "1440", "900")
        ab(ref_s, "wait", "6000")
        ab(impl_s, "wait", "6000")

        ref_sel, ref_h = scroller_of(ref_s)
        impl_sel, _ = scroller_of(impl_s)
        if ref_h <= 0:
            return {}, "could not measure ref document height"

        # Matched absolute offsets spread over the ref docHeight (skip 0/bottom
        # duplicates): interior points at k/(N+1) of the ref scroll range.
        step = ref_h / (N_DEPTHS + 1)
        depths = [max(1, round(step * k)) for k in range(1, N_DEPTHS + 1)]

        # Settle window is DERIVED from the ref's transition durations (J-2), not
        # a hardcoded constant — a long entrance transition needs a longer wait
        # before the frame is stable, or the VLM sees a mid-transition frame and
        # reports phantom motion. Applied after EVERY scroll (ref + impl re-target).
        settle_ms = derive_settle_ms(REF_DIR)
        settle_s = settle_ms / 1000.0

        # Per depth: scroll the ref to the requested offset, settle, and READ
        # BACK where it actually landed (a snap-back engine moves it during the
        # settle). Then RE-TARGET the impl to the ref's ACTUAL landing position
        # so both frames depict the same intended document location — the human
        # eyeball protocol implicitly compares "where the ref ended up", not the
        # requested constant. If, after re-targeting, the impl's settled position
        # still diverges beyond PAIR_TOLERANCE, that is genuinely divergent
        # scroll behavior (e.g. impl lacks the ref's snap): the sample is marked
        # unpaired, EXCLUDED from the VLM frames (so it cannot fabricate phantom
        # motion findings from mismatched frames) but RECORDED in the artifact —
        # divergent scroll behavior is itself evidence.
        frames = []
        samples = []
        unpaired = []
        for i, y in enumerate(depths, start=1):
            ab_eval(ref_s, scroll_js(ref_sel, y))
            time.sleep(settle_s)
            ref_y = read_pos(ref_s, ref_sel)
            if ref_y is None:
                return {}, f"could not read back ref scroll position at depth {i} (requested y={y})"
            ab_eval(impl_s, scroll_js(impl_sel, ref_y))
            time.sleep(settle_s)
            impl_y = read_pos(impl_s, impl_sel)
            if impl_y is None:
                return {}, f"could not read back impl scroll position at depth {i} (target y={ref_y})"

            gap = abs(ref_y - impl_y)
            if gap > PAIR_TOLERANCE:
                sample = {"depth": i, "requestedY": y, "refY": ref_y,
                          "implY": impl_y, "settleMs": settle_ms, "paired": False,
                          "reason": f"|refY-implY|={gap}px > {PAIR_TOLERANCE}px "
                                    "(impl scroll landed elsewhere — divergent scroll behavior)"}
                unpaired.append(sample)
                samples.append(sample)
                continue

            ref_png = ref_shots / f"d{i:02d}.png"
            impl_png = impl_shots / f"d{i:02d}.png"
            ab(ref_s, "screenshot", str(ref_png))
            ab(impl_s, "screenshot", str(impl_png))
            if not (ref_png.is_file() and ref_png.stat().st_size > 0):
                return {}, f"ref screenshot missing at depth {i} (y={ref_y})"
            if not (impl_png.is_file() and impl_png.stat().st_size > 0):
                return {}, f"impl screenshot missing at depth {i} (y={impl_y})"
            frames.append((i, ref_y, ref_png, impl_png))
            samples.append({"depth": i, "requestedY": y, "refY": ref_y,
                            "implY": impl_y, "settleMs": settle_ms, "paired": True})
    finally:
        ab(ref_s, "close")
        ab(impl_s, "close")

    # Every depth diverged — there is nothing paired to judge. A motion run that
    # produced no comparable frames is not motion evidence: fail closed as an
    # error (never a pass), but surface the divergence list as the reason.
    if not frames:
        return ({"settleMs": settle_ms, "samples": samples, "unpairedSamples": unpaired},
                f"all {len(depths)} motion samples diverged beyond {PAIR_TOLERANCE}px "
                "(impl scroll behavior does not track the ref) — no paired frames to judge")

    # Build ONE prompt listing the PAIRED 2xN screenshots in scroll order. Each
    # pair is labelled with where the ref actually landed (the position both
    # frames now depict).
    lines = [prompt_path.read_text(encoding="utf-8"), "", "---", ""]
    lines.append(f"There are {len(frames)} paired depths, listed top-to-bottom. "
                 "Read every REF and IMPL path below with the Read tool, then "
                 "emit ONLY the JSON object from the schema. No prose.")
    lines.append("")
    for i, ref_y, ref_png, impl_png in frames:
        lines.append(f"Depth {i} (settled scrollY={ref_y}):")
        lines.append(f"  REF:  {ref_png.resolve()}")
        lines.append(f"  IMPL: {impl_png.resolve()}")
    prompt = "\n".join(lines)

    timeout = _timeout_seconds()
    try:
        proc = subprocess.run(
            ["python3", str(run_with_timeout), str(timeout),
             "claude", "--print", "--permission-mode", "auto", prompt],
            capture_output=True, text=True, timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired:
        return {}, f"motion-judge claude call exceeded {timeout + 30}s"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return {}, f"motion-judge claude call failed (rc={proc.returncode}): {detail}"

    parsed = first_json_object(proc.stdout)
    if not isinstance(parsed, dict):
        return {}, "motion-judge response was not valid JSON"
    axes_in = parsed.get("axes")
    if not isinstance(axes_in, dict):
        return {}, "motion-judge response missing 'axes' object"
    axes = {}
    for a in AXES:
        v = axes_in.get(a)
        if not is_num(v):
            return {}, f"motion-judge axis '{a}' missing or non-numeric"
        axes[a] = v
    notes = parsed.get("verdictNotes")
    differs = parsed.get("differsAt")
    return {
        "axes": axes,
        "differsAt": differs if isinstance(differs, list) else [],
        "notes": notes if isinstance(notes, list) else [],
        "settleMs": settle_ms,
        "samples": samples,
        "unpairedSamples": unpaired,
    }, None


# ── verdict assembly (shared by collect + judge-artifact) ────────────────────
def measurements_error(meas: Any) -> str | None:
    """Validate a measurements dict; return an error reason or None. A probe/
    setup failure marker or any missing/non-numeric axis or static score fails
    closed as an infra error (never a silent pass)."""
    if not isinstance(meas, dict):
        return "measurements are not a JSON object"
    if meas.get("probeFailed") or meas.get("setupError"):
        return str(meas.get("setupError") or "probe failed")
    motion = meas.get("motion")
    if not isinstance(motion, dict):
        return "motion measurements missing"
    axes = motion.get("axes")
    if not isinstance(axes, dict):
        unpaired = motion.get("unpairedSamples")
        if isinstance(unpaired, list) and unpaired:
            # All samples diverged (no paired frames were judged) — surface the
            # divergence rather than a generic missing-axes message.
            return (f"all motion samples diverged — no paired frames to judge "
                    f"({len(unpaired)} unpaired)")
        return "motion axes missing"
    for a in AXES:
        if bounded_score(axes.get(a)) is None:
            return f"motion axis '{a}' must be finite and within [0, 10]"
    static = meas.get("staticSections")
    if not isinstance(static, list):
        return "staticSections missing"
    for s in static:
        if not isinstance(s, dict) or bounded_score(s.get("score")) is None:
            return "a static section score must be finite and within [0, 10]"
    return None


def normalize_static(raw: list) -> list:
    out = []
    for s in raw:
        score = bounded_score(s["score"])
        if score is None:
            raise ValueError("static score must be finite and within [0, 10]")
        out.append({
            "label": s.get("label", "section"),
            "score": round(score, 2),
            "verdict": s.get("verdict") or ("pass" if score >= STATIC_THRESHOLD else "fail"),
            "issues": s.get("issues") if isinstance(s.get("issues"), list) else [],
        })
    return out


def assemble(static_sections: list, motion: dict, source: str,
             static_skipped: list | None = None, crop_set: str | None = None,
             notes: list | None = None) -> tuple[dict, int]:
    axes: dict[str, float] = {}
    for axis in AXES:
        score = bounded_score(motion["axes"][axis])
        if score is None:
            raise ValueError(
                f"motion axis {axis!r} must be finite and within [0, 10]"
            )
        axes[axis] = score
    status = "pass"
    for s in static_sections:
        if s["score"] < STATIC_THRESHOLD:
            status = "fail"
    for a in AXES:
        if axes[a] < AXIS_THRESHOLD:
            status = "fail"
    worst_axis = min(AXES, key=lambda a: axes[a])
    worst_section = (min(static_sections, key=lambda s: s["score"])["label"]
                     if static_sections else None)
    signals = [axes[a] for a in AXES] + [s["score"] for s in static_sections]
    # J-3: the headline is the MEAN so one wall section can't zero it while the
    # rest pass; the min is preserved alongside (status stays fail-closed on any
    # axis<7 or section<7). worstAxis/worstSection point at the weakest signals.
    mean_score = round(sum(signals) / len(signals), 2) if signals else 10.0
    min_score = round(min(signals), 2) if signals else 10.0
    unpaired_value = motion.get("unpairedSamples")
    unpaired: list[Any] = unpaired_value if isinstance(unpaired_value, list) else []
    payload = {
        "schemaVersion": 1,
        "status": status,
        "source": source,
        "staticSections": static_sections,
        "staticSkipped": static_skipped or [],
        "staticCropSet": crop_set,
        "motion": {"axes": axes,
                   "differsAt": motion.get("differsAt", []),
                   "notes": motion.get("notes", []),
                   "settleMs": motion.get("settleMs"),
                   "samples": motion.get("samples", []) if isinstance(motion.get("samples"), list) else [],
                   "unpairedSamples": unpaired},
        "overall": {"score": mean_score, "min": min_score,
                    "worstAxis": worst_axis, "worstSection": worst_section},
        "notes": notes or [],
    }
    semantic_error = visual_fidelity_semantic_error(payload)
    if semantic_error is not None:
        raise ValueError(f"invalid visual fidelity payload: {semantic_error}")
    write_artifact(payload)
    print(f"visual-fidelity-judge: status={status} mean={mean_score} min={min_score} "
          f"worstAxis={worst_axis} worstSection={worst_section} "
          f"sections={len(static_sections)} skipped={len(static_skipped or [])} "
          f"unpaired={len(unpaired)} source={source}")
    return payload, (0 if status == "pass" else 1)


# ── main ─────────────────────────────────────────────────────────────────────
# Resolve the prompt from the plugin root passed in as REPO_ROOT.
PROMPT_PATH = Path(REPO_ROOT) / "skills" / "visual-debug" / "prompts" / "motion-judge.md"


def main() -> int:
    if MODE == "print-settle":
        # Testability probe (J-2): print the derived post-scroll settle window (ms).
        print(derive_settle_ms(REF_DIR))
        return 0

    if MODE == "print-static-plan":
        # Testability probe (J-1): print the static crop plan (chosen set + judged
        # vs stale-skipped) as JSON. Filesystem-only — no dispatch.
        crop_set, judged, skipped, impl_resolved, note = static_plan()
        print(json.dumps({
            "cropSet": crop_set,
            "judged": [label for label, _r, _i in judged],
            "skipped": skipped,
            "allStale": (not judged and bool(skipped)),
            "implRootResolved": impl_resolved,
            "note": note,
        }))
        return 0

    if MODE == "judge":
        meas = load_json(Path(MEAS_FILE))
        reason = measurements_error(meas)
        if reason:
            # Preserve any divergence list so an all-unpaired run still records
            # WHY it errored (divergent scroll behavior is evidence, not noise).
            motion_pt = meas.get("motion") if isinstance(meas, dict) else None
            write_artifact({"status": "error", "source": "judge",
                            "staticSections": [],
                            "motion": motion_pt if isinstance(motion_pt, dict) else {},
                            "note": reason})
            print(f"visual-fidelity-judge: error — {reason}", file=sys.stderr)
            return 2
        static_sections = normalize_static(meas["staticSections"])
        _, rc = assemble(
            static_sections, meas["motion"], meas.get("source", "judge"),
            static_skipped=meas.get("staticSkipped") if isinstance(meas.get("staticSkipped"), list) else None,
            crop_set=meas.get("staticCropSet") if isinstance(meas.get("staticCropSet"), str) else None,
            notes=meas.get("notes") if isinstance(meas.get("notes"), list) else None,
        )
        return rc

    # collect mode — live static + motion.
    static_sections, static_skipped, crop_set, static_note, static_err = run_static()
    notes = [static_note] if static_note else []
    if static_err:
        write_artifact({"status": "error", "source": "live",
                        "staticSections": [], "staticSkipped": static_skipped,
                        "staticCropSet": crop_set, "motion": {},
                        "notes": notes, "note": static_err})
        print(f"visual-fidelity-judge: error — {static_err}", file=sys.stderr)
        return 2
    motion, motion_err = run_motion()
    if motion_err or "axes" not in motion:
        # Preserve the partial motion dict (samples/unpairedSamples) so an
        # all-diverged or infra-failed run still records what it observed.
        write_artifact({"status": "error", "source": "live",
                        "staticSections": static_sections,
                        "staticSkipped": static_skipped, "staticCropSet": crop_set,
                        "motion": motion if isinstance(motion, dict) else {},
                        "notes": notes,
                        "note": motion_err or "motion pass produced no result"})
        print(f"visual-fidelity-judge: error — {motion_err}", file=sys.stderr)
        return 2
    _, rc = assemble(static_sections, motion, "live",
                     static_skipped=static_skipped, crop_set=crop_set, notes=notes)
    return rc


try:
    sys.exit(main())
except SystemExit:
    raise
except Exception as exc:  # emit-or-fail: never leave without an artifact
    write_artifact({"status": "error", "staticSections": [], "motion": {},
                    "note": f"unexpected failure: {exc}"})
    print(f"visual-fidelity-judge: unexpected failure — {exc}", file=sys.stderr)
    sys.exit(2)
