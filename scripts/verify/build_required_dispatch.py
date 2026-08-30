#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

(plan_path, ref_dir, repo_root, impl_root, impl_src, impl_public,
 ref_url, impl_url, session) = sys.argv[1:10]
plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
strict_warnings = (
    str(plan.get("strictWarnings", "")).lower() in {"1", "true", "yes", "on"}
    or os.environ.get("UI_CLONE_STRICT_WARNINGS", "").strip().lower()
    in {"1", "true", "yes", "on"}
)
STRICT_WARNING_IDS = {
    "tree-diff",
    "scroll-coverage",
    "keyframes-diff",
    "scroll-anim-temporal",
    "visual-fidelity-judge",
    "runtime-text-sequence",
}

# Known dispatch signatures. Add new scripts here as they are wired in.
# Each entry maps the SCRIPT FILENAME to an ARGS RECIPE string with
# placeholders {ref_dir}, {impl_root}, {impl_src}, {ref_url}, {impl_url},
# {session}.
SIGNATURES = {
    # ── static / quick tier (no browser) ──
    "ref-screenshot-asset-check.sh": "{ref_dir} {impl_root}",
    "capacity-check.sh": "{ref_dir}",
    "impl-url-guard.sh": "{ref_dir} {impl_url} {impl_root}",
    "entry-coherence-check.sh": "{ref_dir} {impl_root}",
    "scaffold-residue-check.sh": "{ref_dir} {impl_root}",
    "html-paste-check.sh": "{ref_dir} {impl_root}",
    "monolithic-impl-check.sh": "{ref_dir} {impl_root}",
    "motion-coverage-check.sh": "{ref_dir} {impl_root}",
    "scroll-engine-parity-check.sh": "{ref_dir} {impl_root}",
    "forced-state-class-check.sh": "{ref_dir} {impl_root}",
    "body-opacity-unlock-check.sh": "{ref_dir} {impl_root}",
    "live-parity-sweep.sh": "{ref_url} {impl_url} {session}-lp {ref_dir}",
    "lottie-scroll-scrub-check.sh": "{ref_dir} {impl_root} {ref_url} {impl_url} {session}-lottie",
    "swiper-runtime-check.sh": "{ref_dir} {impl_root}",
    "css-mirror-check.sh": "{ref_dir} {impl_root}",
    "scaffold-warn-check.sh": "{ref_dir} {impl_root}",
    "invalidation-check.sh": "{ref_dir}",
    "required-media-coverage-check.sh": "{ref_dir} {impl_root}",
    "remote-asset-ref-check.sh": "{ref_dir}",
    "capture-artifact-inventory-check.sh": "{ref_dir}",
    "alignment-parity-check.sh": "{ref_dir}",
    "junk-token-check.sh": "{ref_dir} {impl_src} {session}-junk {impl_url}",
    "alignment-sweep-check.sh": "{session}-align {impl_url} {ref_dir}",
    "masked-region-motion-proof-check.sh": "{session}-mrm {impl_url} {ref_dir}",
    "asset-transfer-check.sh": "{ref_dir} {impl_public}",
    "asset-utilization-check.sh": "{ref_dir} {impl_src}",
    "asset-placement-check.sh": "{ref_dir} {impl_root}",
    "image-fidelity-check.sh": "{ref_dir} {impl_src}",
    "proxy-mirror-check.sh": "{ref_dir} {impl_root}",
    "bundle-paste-check.sh": "{ref_dir} {impl_root}",
    "transition-spec-coverage.sh": "{ref_dir} {impl_src}",
    "signature-effects-coverage-check.sh": "{ref_dir} {impl_src}",
    "spec-implementation-coverage.sh": "{ref_dir} {impl_src}",
    "runtime-spec-coverage.sh": "{ref_dir} {impl_src}",
    "bundle-impl-coverage-check.sh": "{ref_dir} {impl_pkg}",
    # 2026-05-22: add {impl_url} third arg so the runtime-proof block in
    # lottie-runtime-check.sh fires (it opens impl_url, waits 1.5s, and
    # asserts at least one Lottie container painted svg/canvas). Without
    # impl_url the script falls back to the legacy static-only check
    # which can pass when imports exist but loadAnimation never runs.
    "lottie-runtime-check.sh": "{ref_dir} {impl_root} {impl_url}",
    # ── browser-needed / standard tier ──
    "tailwind-transform-conflict-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-twc {impl_url}",
    "hydration-check.sh": "{session}-hyd {impl_url} {ref_dir}",
    "runtime-image-validity-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-rim {impl_url}",
    "hidden-children-check.sh": "{session}-hidden {impl_url} {ref_dir}",
    "geometry-sanity-check.sh": "{session}-geom {impl_url} {ref_dir}",
    # reveal-trigger-check.sh writes reveal-trigger.json via REF_DIR env
    # (pass and fail paths both emit).
    "reveal-trigger-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-reveal {impl_url}",
    # transition-fires drives each transition-spec entry's trigger in a real
    # browser and asserts a MEASURED runtime delta. Positional args:
    # <session> <impl-url> <ref-dir>; writes <ref-dir>/transition-fires.json.
    "transition-fires-check.sh":
        "{session}-fires {impl_url} {ref_dir}",
    # 2026-05-22: header-state-runtime gate fires unconditionally — proves
    # the impl header is a runtime state machine (mutates className on
    # scroll) when the ref's header is stateful. Args: session ref-url
    # impl-url ref-dir [w] [h]. self-skips when ref header is static.
    "header-state-runtime-check.sh":
        "{session}-hsr {ref_url} {impl_url} {ref_dir}",
    # svg-provenance closes the IconMark.tsx hand-roll loophole.
    # svg-dom-parity only checks count
    # + section presence; this gate asserts impl SVG geometry traces
    # back to ref geometry. Args: session ref-url impl-url ref-dir.
    "svg-provenance-check.sh":
        "{session}-svgp {ref_url} {impl_url} {ref_dir}",
    # 2026-05-22: runtime-proof rollup is a file-IO aggregator —
    # only ref-dir needed. Must run AFTER all source artifacts are
    # produced; dispatcher already orders rows by add_check insertion
    # order (this row is inserted near the end of standard tier so
    # source artifacts exist by the time it dispatches).
    "runtime-proof-rollup.sh":
        "{ref_dir}",
    # 2026-05-22: transition-proof rollup — same file-IO contract as
    # runtime-proof; ref-dir only.
    "transition-proof-rollup.sh":
        "{ref_dir}",
    # 2026-05-22: ref-js-loader gate — static scan of impl source for
    # ref-host references, plus optional runtime probe when impl_url
    # is passed.
    "ref-js-loader-check.sh":
        "{ref_dir} {impl_root} {impl_url}",
    # runtime-env gate — catches Vite preamble traps, hydration
    # mismatches, port-routing collisions from orphan dev servers.
    # Observed failure modes: NODE_ENV=production trap and orphan-port
    # interception. Needs ref-dir + impl-root + impl-url.
    "runtime-env-check.sh":
        "{ref_dir} {impl_root} {impl_url}",
    # first-paint visibility gate — catches loader/body/root hidden states
    # such as copied `body{opacity:0}` when DOM exists but the page is blank.
    "blank-viewport-check.sh":
        "{session}-blank {impl_url} {ref_dir}",
    # 2026-05-22: video-play-proof — currentTime advancement check.
    "video-play-proof-check.sh":
        "{session}-vpp {impl_url} {ref_dir}",
    # 2026-05-22: impl-scope guard — diff git HEAD against baseline,
    # fail if iteration touched plugin tooling.
    "impl-scope-check.sh":
        "{ref_dir} {impl_root}",
    # 2026-05-22 grounding: color-token gate is pure file-scan;
    # ref-dir + impl-root only.
    "color-token-grounding-check.sh":
        "{ref_dir} {impl_root}",
    # 2026-05-22: duration/easing grounding — scan impl for guessed
    # transition timings; static, no browser.
    "duration-easing-grounding-check.sh":
        "{ref_dir} {impl_root}",
    # 2026-05-22: mobile viewport parity at 375x812.
    "preview-runtime-health-check.sh":
        "{session}-prh {ref_url} {impl_url} {ref_dir}",
    "mobile-viewport-parity-check.sh":
        "{session}-mvp {ref_url} {impl_url} {ref_dir}",
    "mobile-responsive-coverage-check.sh": "{ref_dir} {impl_src}",
    # 2026-05-22: stronger frame-delta proof (Lottie currentFrame +
    # canvas paint + WebGL drawbuffer).
    "runtime-frame-proof-check.sh":
        "{session}-rfp {impl_url} {ref_dir}",
    "scroll-end-completion-check.sh": "{session}-sec {impl_url} {ref_dir}",
    "scroll-state-machine-check.sh": "{session}-ssm {ref_url} {impl_url} {ref_dir}",
    "font-parity-check.sh": "{session}-fp {ref_url} {impl_url} {ref_dir}",
    "typography-parity-check.sh": "{session}-typo {ref_url} {impl_url} {ref_dir}",
    "content-cardinality-check.sh": "{session}-cc {impl_url} {ref_dir}",
    "breakpoint-collision-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-bound {impl_url}",
    # ── ref+impl browser pairs ──
    "runtime-dom-parity-check.sh":
        "{session}-rdp {ref_url} {impl_url} {ref_dir}",
    "runtime-text-sequence-check.sh":
        "{session}-rts {ref_url} {impl_url} {ref_dir}",
    "svg-dom-parity-check.sh":
        "{session}-svg {ref_url} {impl_url} {ref_dir}",
    "transition-compare.sh":
        "{ref_url} {impl_url} {session}-tc {ref_dir}",
    "hover-state-compare.sh":
        "{ref_url} {impl_url} {session}-hsc {ref_dir}",
    "hover-tree-diff.sh":
        "{session}-htd {ref_url} {impl_url} {ref_dir}",
    "video-motion-compare.sh":
        "{ref_url} {impl_url} {session}-vmc {ref_dir}",
    "click-state-compare.sh":
        "{ref_url} {impl_url} {session}-clk {ref_dir}",
    "scroll-coverage-check.sh":
        "{ref_dir} {ref_url} {impl_url} {session}-scov",
    "tree-diff.sh": "{session}-td {ref_url} {impl_url} {ref_dir}",
    "keyframes-diff.sh": "{session}-kf {ref_url} {impl_url} {ref_dir}/transitions",
    # ── static text/dom fidelity ──
    "text-fidelity-check.sh":
        "{ref_dir} {impl_root} --out {ref_dir}/text-fidelity-check.json",
    "dom-mirror-check.sh":
        "{ref_dir} {impl_root} --out {ref_dir}/dom-mirror-check.json",
    # 2026-05-22: hero-composite-check pairs with the dom-mirror advisory
    # downgrade — same {ref_dir} {impl_root} contract; default artifact path
    # is $REF_DIR/hero-composite.json (matches verification-plan row).
    "hero-composite-check.sh": "{ref_dir} {impl_root}",
    "scroll-anim-temporal-diff.sh": "MANUAL",
}

ctx = {
    "ref_dir": ref_dir,
    "impl_root": impl_root,
    "impl_src": impl_src,
    "impl_public": impl_public,
    "impl_pkg": str(Path(impl_root) / "package.json"),
    "ref_url": ref_url,
    "impl_url": impl_url,
    "session": session,
}

dispatch_rows = []

for check in plan.get("requiredChecks", []):
    cid = check.get("id", "?")
    script_rel = check.get("script") or ""
    produces = check.get("produces") or ""
    if not script_rel or not produces:
        dispatch_rows.append(("SKIP", cid, "", "", "no-script-or-produces", "", ""))
        continue
    # Resolve script path against repo root.
    script_path = Path(repo_root) / script_rel
    if not script_path.is_file():
        # Try relative basename match (for scripts referenced by short
        # name only).
        alt = list(Path(repo_root).rglob(Path(script_rel).name))
        if alt:
            script_path = alt[0]
        else:
            dispatch_rows.append(("NOSCRIPT", cid, script_rel, "", "script not found", "", ""))
            continue
    # Plan rows may carry their own dispatch template (argsRecipe) — the
    # single-source alternative to the hand-synced SIGNATURES table. Rows
    # without one fall back to SIGNATURES for back-compat.
    sig = check.get("argsRecipe") or SIGNATURES.get(script_path.name)
    if not sig:
        dispatch_rows.append(("NOSIG", cid, str(script_path), "", "unknown signature", "", ""))
        continue
    args = sig.format(**ctx)
    severity = check.get("severity") or "block"
    if strict_warnings and severity == "warn" and cid in STRICT_WARNING_IDS:
        severity = "block"
    deps = " ".join(check.get("dependsOn", []) or [])
    dispatch_rows.append(("DISPATCH", cid, str(script_path), args, produces, severity, deps))

# Composite proof rollups are file-IO aggregators; they must run after
# their source checks even when verification-plan.json inserts them earlier
# than late-tier browser checks. Otherwise a single dispatcher call can leave
# runtime-proof.json / transition-proof.json stale-failed while later rows
# have already produced the missing source artifacts.
ROLLUP_BASENAMES = {"runtime-proof-rollup.sh", "transition-proof-rollup.sh"}
normal_rows = []
rollup_rows = []
for row in dispatch_rows:
    row_script_path = row[2]
    if row_script_path and Path(row_script_path).name in ROLLUP_BASENAMES:
        rollup_rows.append(row)
    else:
        normal_rows.append(row)


def _ensure_section_compare_precedes_alignment_checks(rows: list[tuple[str, str, str, str, str, str, str]]) -> list[tuple[str, str, str, str, str, str, str]]:
    """Keep section-compare before consumers whose checks rely on section matches.

    `alignment-parity` and `alignment-sweep` consume `sections/matches.json`
    artifacts generated (or refreshed) by section-compare. If their rows are
    emitted earlier, the B1 staleness hash can short-circuit them and reuse a
    stale verdict. Keep these checks after section-compare within the same
    dispatch pass.
    """
    section_idx = None
    alignment_consumer_idxs = []
    for i, row in enumerate(rows):
        cid = row[1]
        if cid == "section-compare":
            section_idx = i
        if cid in {"alignment-parity", "alignment-sweep"}:
            alignment_consumer_idxs.append(i)

    if section_idx is None or not alignment_consumer_idxs:
        return rows

    earliest_consumer = min(alignment_consumer_idxs)
    if earliest_consumer < section_idx:
        row = rows.pop(section_idx)
        rows.insert(earliest_consumer, row)
    return rows


# post-implement also requires canonical section evidence
# (sections/result.txt), but verification-plan.json historically lists only
# requiredChecks and omits the section-compare closeout row. If full reference
# screenshots exist, synthesize that row here so one dispatcher call actually
# materializes every artifact the post-implement gate asks agents to produce.
has_section_row = any(row[4] == "sections/result.txt" for row in dispatch_rows)
has_ref_screenshots = any((Path(ref_dir) / "static" / "ref").glob("*.png"))
section_script = Path(repo_root) / "skills/visual-debug/scripts/section-compare.sh"
if not has_section_row and has_ref_screenshots and section_script.is_file():
    import os as _os
    tier = (_os.environ.get("UI_CLONE_VERIFY_TIER") or "comprehensive").strip().lower()
    frozen_script = Path(repo_root) / "skills/visual-debug/scripts/section-compare-frozen.sh"
    section_positional = f"{ref_url} {impl_url} {session}-section {ref_dir}"
    # Multi-viewport enforcement: responsive sites (detected-breakpoints evidence)
    # must run the section fan-out at every plan-declared viewport — the gate
    # refuses a single-viewport result.txt for them. Computed once; both the frozen
    # wrapper and the fast single-pass path carry it.
    vps = []
    if (Path(ref_dir) / "detected-breakpoints.json").is_file():
        for vp in plan.get("viewports") or []:
            try:
                vps.append(f"{int(vp['w'])}x{int(vp['h'])}")
            except (KeyError, TypeError, ValueError):
                continue
    viewport_env = [f"VIEWPORTS={','.join(vps)}"] if len(vps) > 1 else []
    if tier == "comprehensive" and frozen_script.is_file():
        # Capture-variance determinism (specific regression): the comprehensive tier runs the
        # 3-pass frozen-ref + impl-path-calib wrapper so the impl is captured at the
        # SAME forced scroll frame as the frozen ref. Same-frame strict AE is KEPT
        # (the section_dynamic AE ceiling, not discarded), killing the run-to-run
        # scrub variance that failed faithful sections — WITHOUT opening a defect-
        # hiding hole. The wrapper is viewport-aware (per-viewport impl-path calib),
        # so VIEWPORTS rides through and multi-viewport enforcement is preserved.
        # Its own row timeout scales with viewport count (3 passes x N viewports)
        # so it does not share the 180s single-check budget (review F3).
        # L-MEA-9 (loop-ebpb-0): the formula omitted the x3 pass factor its
        # own comment names — the frozen wrapper fans EVERY pass out over all
        # N viewports (3xN inner runs), so 5 viewports needed ~9000s but got
        # 3000s and the row was process-group-killed mid-run, leaving
        # result.txt header-only (post-implement could not aggregate). Base
        # raised 600->800 to absorb the H9 derived settle (up to 4s/shot).
        frozen_timeout = (
            _os.environ.get("SECTION_FROZEN_TIMEOUT_SEC")
            or str(800 * 3 * max(1, len(vps)))
        ).strip()
        env_parts = [f"ROW_TIMEOUT_SEC={frozen_timeout}", *viewport_env]
        section_args = f"ENV:{' '.join(env_parts)} -- {section_positional}"
        section_script_path = str(frozen_script)
    else:
        # quick/standard tier (or wrapper missing): fast single-pass section-compare.
        section_args = (
            f"ENV:{' '.join(viewport_env)} -- {section_positional}"
            if viewport_env
            else section_positional
        )
        section_script_path = str(section_script)
    normal_rows.append(
        (
            "DISPATCH",
            "section-compare",
            section_script_path,
            section_args,
            "sections/result.txt",
            "block",
            "impl-url-guard",
        )
    )

normal_rows = _ensure_section_compare_precedes_alignment_checks(normal_rows)

for row in normal_rows + rollup_rows:
    print("\t".join(row), flush=True)
