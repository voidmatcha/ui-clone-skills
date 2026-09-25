"""Deterministic completion check for scoped clones.

A scoped clone (section-only, element-only, or a trigger-opened modal/drawer;
operational-rules.md "Scope adjustments by request shape") never runs the
page-level pipeline, so `pipeline verify`, `completion-report.sh --check`, and
`goal --check-done` cannot certify it. This module is its completion command:

    python -m ui_clone.scoped_check <ref-dir> [--impl-root <dir>] [--json]

It passes only when every item of the element-scope contract holds
(closeout.md "Scoped clones", element-capture.md,
comparison-fix.md "Element-Scope Verification"). Every verdict is recomputed
from script-produced evidence; nothing agent-written is trusted:

0. The installed producer files (`ui_clone.scoped_producers` PRODUCER_FILES)
   hash as the shipped release manifest `ui_clone/scoped_producers.sha256.json`
   says; every producer record in the evidence must carry those same hashes.
1. `element-target.json` is a valid element-evidence.sh probe record
   (schemaVersion 2: the selector matched exactly one element, its box is at
   least TARGET_MIN_SIZE px on each side, and it was visible) and the ref dir
   carries no page-level marker. The pass output names the selector, match
   count, and bbox so the user can confirm the target.
2. `frames/ref/` and `frames/impl/` hold the same non-empty set of frame
   images, each side captured by `scripts/extract/element-state-capture.sh`:
   every frame has a `capture-manifest.json` entry whose sha256 matches the
   file (recomputed), whose producer record names the recorder CLI driven by
   the shipped capture script (driver and module sha256), ref entries come
   from the reference origin, impl entries from a different origin (the local
   implementation) whose page loaded nothing from the reference host or its
   subdomains (`resourceOrigins`), no script/stylesheet the reference
   captures inventoried (`codeResources`: by kind, code extension, or
   response content type; same normalized URL), and no non-media load from
   a non-first-party origin that served reference code (an extensionless
   fetched chunk; media and font hotlinks are allowed). Clip entries still
   pass target sanity. A reference
   frame copied or linked into `frames/impl/`, a proxied/iframed reference
   page, an impl page running the reference bundles, an older-schema
   manifest, or an impl frame older than `element-target.json`, fails.
3. Resting-state clips (`idle.png`, `active.png`, `open.png`, ...) are
   pixel-identical (AE 0, recomputed). Motion sequences (`frame-NNNN`,
   `open-NNNN`, `close-NNNN`) are judged with the page-level video criteria
   of `scripts/verify/video-transition-compare.sh` (see `ui_clone.scoped_frames`):
   first-change alignment, arc timing within 18 frames, per-frame SSIM >= 0.90
   with one frame of jitter. Sequence verdicts are cached by content hash in
   `.scoped-check-cache.json` so the Stop hook does not recompute SSIM every turn.
4. `pixel-perfect-diff.json` was produced by the `python -m ui_clone.scoped_diff`
   CLI (schemaVersion 3, producer, producer record with `entry: "cli"`,
   property-list fingerprint, self checksum), every input and implementation
   source it fingerprints still hashes the same and every current clip is
   covered, it reports `result: "pass"` / `mismatches: 0`, and every
   resting-state clip has a passing row whose computed-style diff — target
   plus its element subtree matched by structural path
   (`computed_style_diff.diff_records`) — re-run here from the captured
   `<state>.computed.json` records, is empty.
5. Implementation provenance (`ui_clone.scoped_provenance`): the recorded
   page-level `proxy-mirror-check` / `bundle-paste-check` verdicts pass and
   their outputs still hash the same, and the fingerprinted implementation
   sources (component files, files named after the target, app entry files),
   re-scanned here, carry no reference-host loads, whole-document mirrors,
   raw HTML mounts, or upstream proxies.
6. Trigger-opened UI (open/close recordings, `open*`/`close*` frames, or a
   dialog role on the probed element) has opening AND closing evidence on
   both sides and a passing `open` state row.
7. The current sha256 of `element-target.json`, both `capture-manifest.json`
   files, and `pixel-perfect-diff.json` appears in `.scoped-evidence-ledger.json`
   under its producer (`ui_clone.scoped_ledger`): the PostToolUse Bash hook
   records the hash after a Bash command that is exactly one canonical
   producer invocation, so evidence written by a script the hooks never saw,
   by a producer chained with other commands, or without the hooks installed
   has no entry and fails (`evidence-unledgered` / `evidence-ledger-missing`).

Still trusted (documented limits): that the ledger itself was not edited by
a script the hooks never see (it is a hook-guarded plain file next to the
evidence), that the one visible element `element-target.json` names is the
one the user meant (the pass output surfaces it for confirmation), and that
source files outside the fingerprinted set do not proxy the site.

Exit codes follow docs/agent-cli.md: 0 = PASS, 1 = BLOCKED, 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from ui_clone import scoped_ledger, scoped_producers
from ui_clone.computed_style_diff import diff_records, properties_sha256
from ui_clone.element_capture import (
    MANIFEST_NAME,
    code_hotlink_failures,
    computed_name,
    origin_of,
    sha256_file,
    verify_side,
)
from ui_clone.hooks._common import (
    ELEMENT_TARGET_NAME,
    ELEMENT_TARGET_SCHEMA_VERSION,
    element_target_summary,
    is_valid_element_target,
    load_json_safe,
    target_sanity_problems,
)
from ui_clone.scoped_diff import (
    DIFF_NAME,
    DIFF_PRODUCER,
    DIFF_SCHEMA_VERSION,
    default_impl_root,
    load_record,
    provenance_problems,
    resting_states,
    source_hashes,
)
from ui_clone.scoped_frames import (
    CRITERIA_VERSION,
    auto_sources,
    compare_sequence,
    list_frames,
    pixel_ae,
    split_frames,
)
from ui_clone.scoped_provenance import (
    PAGE_LEVEL_CHECKS,
    driver_script_sha256,
    entry_sources,
    record_checksum,
    scan_sources,
)

CACHE_NAME = ".scoped-check-cache.json"
PAGE_LEVEL_MARKERS = (".ui-re-active", "extracted.json", "pipeline-state.json")
_MAX_LISTED = 5


def _listed(names: list[str]) -> str:
    shown = ", ".join(names[:_MAX_LISTED])
    extra = len(names) - _MAX_LISTED
    return f"{shown} (+{extra} more)" if extra > 0 else shown


class _Report:
    def __init__(self) -> None:
        self.failures: list[dict[str, str]] = []

    def fail(self, code: str, reason: str) -> None:
        self.failures.append({"code": code, "reason": reason})


def _check_target(ref_dir: Path, report: _Report) -> dict[str, Any] | None:
    markers = [name for name in PAGE_LEVEL_MARKERS if (ref_dir / name).exists()]
    if markers:
        report.fail(
            "page-level-run",
            f"ref dir carries page-level marker(s) {', '.join(markers)}; complete it "
            "with the page-level commands (pipeline verify, completion-report.sh "
            "--check, goal --check-done), not scoped_check",
        )
    target = ref_dir / ELEMENT_TARGET_NAME
    if not target.is_file():
        report.fail(
            "element-target-missing",
            f"{ELEMENT_TARGET_NAME} missing; record the target with "
            "scripts/extract/element-evidence.sh",
        )
        return None
    data = load_json_safe(target)
    annotation = data.get("annotation") if isinstance(data, dict) else None
    if not is_valid_element_target(target):
        problems = (
            target_sanity_problems(annotation)
            if isinstance(data, dict)
            and data.get("schemaVersion") == ELEMENT_TARGET_SCHEMA_VERSION
            and isinstance(annotation, dict)
            else []
        )
        if problems:
            report.fail(
                "element-target-sanity",
                f"{ELEMENT_TARGET_NAME} does not name a usable target: {'; '.join(problems)}; "
                "re-probe a selector that matches one visible element with element-evidence.sh",
            )
        else:
            report.fail(
                "element-target-invalid",
                f"{ELEMENT_TARGET_NAME} is not a successful element-evidence.sh probe "
                f"record (schemaVersion {ELEMENT_TARGET_SCHEMA_VERSION}, ok, http(s) url, "
                "element-probe selector + bbox + target sanity)",
            )
        return None
    assert isinstance(data, dict)
    if data.get("schemaVersion") != ELEMENT_TARGET_SCHEMA_VERSION:
        report.fail(
            "element-target-schema",
            f"{ELEMENT_TARGET_NAME} was recorded by an older element-evidence.sh "
            f"(schemaVersion {data.get('schemaVersion')!r}, no match count / visibility); "
            "re-probe the target with the shipped script, then re-capture frames/impl/",
        )
        return None
    return data


def _check_producers(report: _Report) -> dict[str, str | None]:
    """The installed producer files must match the shipped release manifest;
    returns the shipped hashes the evidence records are compared against."""
    problems = scoped_producers.problems()
    if problems:
        report.fail(
            "producers-modified",
            "installed scoped-evidence producers differ from the shipped release manifest "
            f"({scoped_producers.MANIFEST_NAME}); reinstall the plugin: {_listed(problems)}",
        )
    manifest = scoped_producers.load_manifest()
    return {
        rel: scoped_producers.shipped_sha256(rel, manifest)
        for rel in (
            scoped_producers.CAPTURE_MODULE,
            scoped_producers.DIFF_MODULE,
            scoped_producers.DRIVER_SCRIPT,
        )
    }


def _check_frames(
    ref_dir: Path, report: _Report
) -> tuple[dict[str, Path], dict[str, Path]]:
    ref_frames = list_frames(ref_dir / "frames" / "ref")
    impl_frames = list_frames(ref_dir / "frames" / "impl")
    if not ref_frames:
        report.fail("ref-frames-missing", "frames/ref/ has no frame images")
    if not impl_frames:
        report.fail(
            "impl-frames-missing",
            "frames/impl/ has no frame images; capture the implementation with "
            "element-state-capture.sh using the same clip, names, and states as frames/ref/",
        )
    if not ref_frames or not impl_frames:
        return ref_frames, impl_frames
    missing = sorted(set(ref_frames) - set(impl_frames))
    extra = sorted(set(impl_frames) - set(ref_frames))
    if missing:
        report.fail(
            "impl-frames-unmatched",
            f"{len(missing)} ref frame(s) have no impl counterpart: {_listed(missing)}",
        )
    if extra:
        report.fail(
            "impl-frames-unmatched",
            f"{len(extra)} impl frame(s) have no ref counterpart: {_listed(extra)}",
        )
    return ref_frames, impl_frames


def _hash_frames(frames: dict[str, Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, path in frames.items():
        try:
            hashes[name] = sha256_file(path)
        except OSError:
            continue
    return hashes


def _check_provenance(
    ref_dir: Path,
    target: dict[str, Any] | None,
    ref_frames: dict[str, Path],
    impl_frames: dict[str, Path],
    hashes: dict[str, dict[str, str]],
    shipped: dict[str, str | None],
    report: _Report,
) -> None:
    linked: list[str] = []
    for name, impl in impl_frames.items():
        if impl.is_symlink():
            linked.append(name)
            continue
        ref = ref_frames.get(name)
        try:
            if ref is not None and os.path.samefile(ref, impl):
                linked.append(name)
        except OSError:
            continue
    if linked:
        report.fail(
            "impl-frame-is-reference",
            f"{len(linked)} impl frame(s) link to reference files, not implementation "
            f"captures: {_listed(linked)}",
        )
    reference_origin = origin_of(target.get("url")) if isinstance(target, dict) else None
    if reference_origin is not None:
        driver_sha = shipped.get(scoped_producers.DRIVER_SCRIPT) or driver_script_sha256()
        for side, frames in (("ref", ref_frames), ("impl", impl_frames)):
            for code, reason in verify_side(
                ref_dir / "frames" / side,
                frames,
                reference_origin=reference_origin,
                side=side,
                hashes=hashes[side],
                driver_sha256=driver_sha,
                module_sha256=shipped.get(scoped_producers.CAPTURE_MODULE),
            ):
                report.fail(code, reason)
        for code, reason in code_hotlink_failures(ref_dir):
            report.fail(code, reason)
    try:
        target_mtime = (ref_dir / ELEMENT_TARGET_NAME).stat().st_mtime
    except OSError:
        return
    older = [
        name
        for name, impl in impl_frames.items()
        if not impl.is_symlink() and impl.stat().st_mtime < target_mtime
    ]
    if older:
        report.fail(
            "impl-frames-predate-target",
            f"{len(older)} impl frame(s) are older than {ELEMENT_TARGET_NAME} (the "
            f"target was re-probed after they were captured): {_listed(older)}; "
            "re-capture frames/impl/",
        )


# ── frame fidelity ────────────────────────────────────────────────────────


def _load_cache(ref_dir: Path) -> dict[str, Any]:
    data = load_json_safe(ref_dir / CACHE_NAME)
    if data is None or data.get("criteria") != CRITERIA_VERSION or not isinstance(data.get("sequences"), dict):
        return {"criteria": CRITERIA_VERSION, "sequences": {}}
    return data


def _save_cache(ref_dir: Path, cache: dict[str, Any]) -> None:
    try:
        tmp = ref_dir / (CACHE_NAME + ".tmp")
        tmp.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, ref_dir / CACHE_NAME)
    except OSError:
        pass


def _sequence_key(prefix: str, names: list[str], hashes: dict[str, dict[str, str]]) -> str:
    digest = hashlib.sha256(CRITERIA_VERSION.encode("utf-8"))
    digest.update(prefix.encode("utf-8"))
    for name in names:
        digest.update(f"\n{name}:{hashes['ref'].get(name, '?')}:{hashes['impl'].get(name, '?')}".encode())
    return digest.hexdigest()


def _check_frame_fidelity(
    ref_dir: Path,
    ref_frames: dict[str, Path],
    impl_frames: dict[str, Path],
    hashes: dict[str, dict[str, str]],
    report: _Report,
) -> tuple[int, dict[str, Any]]:
    """Static clips: AE 0. Motion sequences: page-level video criteria. Returns
    (frame pairs compared, per-sequence verdicts)."""
    shared = {name: ref_frames[name] for name in sorted(set(ref_frames) & set(impl_frames))}
    try:
        import numpy  # noqa: F401
        import skimage  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        report.fail(
            "ae-unavailable",
            "Pillow/numpy/scikit-image unavailable, so frames cannot be measured",
        )
        return 0, {}
    static, sequences = split_frames(shared)
    failing: list[str] = []
    unreadable: list[str] = []
    for name in static:
        try:
            ae = pixel_ae(ref_frames[name], impl_frames[name])
        except (OSError, ValueError):
            unreadable.append(name)
            continue
        if ae is None:
            failing.append(f"{name} (size differs)")
        elif ae > 0:
            failing.append(f"{name} (AE {ae})")
    if unreadable:
        report.fail(
            "frame-unreadable", f"{len(unreadable)} frame pair(s) unreadable: {_listed(unreadable)}"
        )
    if failing:
        report.fail(
            "frame-ae-nonzero",
            f"{len(failing)} of {len(static)} resting-state clip(s) differ (AE must be 0): "
            f"{_listed(failing)}",
        )
    compared = len(static)
    verdicts: dict[str, Any] = {}
    cache = _load_cache(ref_dir)
    cache_dirty = False
    for prefix, items in sequences.items():
        names = [name for _, name in items]
        compared += len(names)
        key = _sequence_key(prefix, names, hashes)
        verdict = cache["sequences"].get(key)
        if not isinstance(verdict, dict) or "pass" not in verdict:
            try:
                verdict = compare_sequence(
                    [ref_frames[n] for n in names], [impl_frames[n] for n in names]
                )
            except (OSError, ValueError) as exc:
                report.fail("frame-unreadable", f"{prefix}-* sequence unreadable: {exc}")
                continue
            cache["sequences"][key] = verdict
            cache_dirty = True
        verdicts[prefix] = verdict
        if not verdict["pass"]:
            report.fail(
                "motion-sequence-diverged",
                f"{prefix}-* ({len(names)} frames): " + "; ".join(verdict["reasons"]),
            )
    if cache_dirty:
        cache["sequences"] = {k: v for k, v in cache["sequences"].items() if k in {
            _sequence_key(p, [n for _, n in items], hashes) for p, items in sequences.items()
        }}
        _save_cache(ref_dir, cache)
    return compared, verdicts


# ── pixel-perfect-diff.json ───────────────────────────────────────────────


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_diff(
    ref_dir: Path,
    ref_frames: dict[str, Path],
    impl_root: Path | None,
    shipped: dict[str, str | None],
    report: _Report,
) -> tuple[dict[str, Any] | None, set[str]]:
    path = ref_dir / DIFF_NAME
    if not path.is_file():
        report.fail(
            "diff-missing",
            f"{DIFF_NAME} missing; produce it with `python -m ui_clone.scoped_diff {ref_dir}` "
            "after capturing every resting state on both sides",
        )
        return None, set()
    data = load_json_safe(path)
    if data is None:
        report.fail("diff-invalid", f"{DIFF_NAME} does not parse as a JSON object")
        return None, set()
    if data.get("schemaVersion") != DIFF_SCHEMA_VERSION or data.get("producer") != DIFF_PRODUCER:
        report.fail(
            "diff-provenance",
            f"{DIFF_NAME} was not produced by `python -m ui_clone.scoped_diff` "
            f"(schemaVersion {DIFF_SCHEMA_VERSION}, producer {DIFF_PRODUCER}); hand-written "
            "diffs are not evidence",
        )
        return data, set()
    if data.get("propertiesSha256") != properties_sha256():
        report.fail(
            "diff-provenance",
            f"{DIFF_NAME} was produced with a different computed-style property list; re-run scoped_diff",
        )
    record = data.get("producerRecord")
    if not isinstance(record, dict) or record.get("entry") != "cli":
        report.fail(
            "diff-provenance",
            f"{DIFF_NAME} was not produced through the `python -m ui_clone.scoped_diff` CLI "
            f"(producer record entry {record.get('entry') if isinstance(record, dict) else None!r}); "
            "an imported build() or a custom script is not evidence",
        )
    elif (
        shipped.get(scoped_producers.DIFF_MODULE) is not None
        and record.get("moduleSha256") != shipped[scoped_producers.DIFF_MODULE]
    ):
        report.fail(
            "diff-provenance",
            f"{DIFF_NAME} was produced by a scoped_diff module that differs from the shipped "
            f"one ({scoped_producers.MANIFEST_NAME}); re-run `python -m ui_clone.scoped_diff` "
            "with the installed plugin",
        )
    if data.get("recordSha256") != record_checksum(data):
        report.fail(
            "diff-provenance",
            f"{DIFF_NAME} was edited after scoped_diff wrote it (self checksum differs); re-run scoped_diff",
        )
    stale: list[str] = []
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        report.fail("diff-provenance", f"{DIFF_NAME} records no input fingerprints")
        inputs = {}
    for rel, digest in inputs.items():
        target = ref_dir / str(rel)
        try:
            if not target.is_file() or sha256_file(target) != digest:
                stale.append(str(rel))
        except OSError:
            stale.append(str(rel))
    states = resting_states(ref_frames)
    expected_inputs = [ELEMENT_TARGET_NAME] + [f"frames/{s}/{MANIFEST_NAME}" for s in ("ref", "impl")]
    expected_inputs.extend(PAGE_LEVEL_CHECKS.values())
    for state in states:
        for side in ("ref", "impl"):
            expected_inputs.extend((f"frames/{side}/{state}.png", f"frames/{side}/{computed_name(state)}"))
    uncovered = [rel for rel in expected_inputs if rel not in inputs]
    if uncovered:
        stale.append(f"not fingerprinted: {_listed(uncovered)}")
    sources = data.get("sources")
    if not isinstance(sources, dict):
        report.fail("diff-provenance", f"{DIFF_NAME} records no source fingerprints")
        sources = {}
    for raw, digest in sources.items():
        src = Path(str(raw))
        try:
            if not src.is_file() or sha256_file(src) != digest:
                stale.append(str(raw))
        except OSError:
            stale.append(str(raw))
    if impl_root is not None:
        current = [str(p) for p in auto_sources(impl_root, ref_dir.name) + entry_sources(impl_root)]
        new_sources = [p for p in current if p not in sources]
        if new_sources:
            stale.append(f"new source(s): {_listed(new_sources)}")
    declared = data.get("implFiles")
    if declared is not None and (
        not isinstance(declared, list) or not all(isinstance(p, str) for p in declared)
    ):
        report.fail("impl-files-invalid", "implFiles must be a list of paths")
    elif isinstance(declared, list):
        _, missing = source_hashes(ref_dir, impl_root, declared)
        if missing:
            report.fail("impl-files-missing", f"implFiles entries not found: {_listed(missing)}")
    if stale:
        report.fail(
            "diff-stale",
            f"{DIFF_NAME} inputs changed since it was produced ({len(stale)}): {_listed(stale)}; "
            "re-run `python -m ui_clone.scoped_diff` after the last capture/source change",
        )
    # Implementation provenance: the recorded page-level no-cheat verdicts
    # (their outputs are fingerprinted above) plus the source scan re-run here.
    provenance = provenance_problems(data.get("noCheat"))
    reference_origin = str(data.get("referenceOrigin") or "")
    existing = [Path(str(p)) for p in sources if Path(str(p)).is_file()]
    provenance.extend(
        f"{item['rule']}: {item['file']} `{item['match']}`"
        for item in scan_sources(existing, reference_origin)
    )
    if provenance:
        report.fail(
            "impl-provenance",
            "implementation is not proven to be generated source (proxy/mirror/reference "
            f"loads): {_listed(sorted(set(provenance)))}",
        )
    if data.get("result") != "pass":
        problems = data.get("problems") if isinstance(data.get("problems"), list) else []
        detail = f": {_listed([str(p) for p in problems])}" if problems else ""
        report.fail("diff-result", f"{DIFF_NAME} result={data.get('result')!r}, expected 'pass'{detail}")
    mismatches = data.get("mismatches")
    if not _is_int(mismatches):
        report.fail("diff-mismatches", f"{DIFF_NAME} has no integer mismatches field")
    elif mismatches != 0:
        report.fail("diff-mismatches", f"{DIFF_NAME} mismatches={mismatches}, expected 0")
    rows = data.get("elements")
    passing_states: set[str] = set()
    if not isinstance(rows, list) or not rows:
        report.fail("diff-elements", f"{DIFF_NAME} has no element rows")
        return data, passing_states
    failing: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            failing.append("non-object row")
            continue
        row_state = row.get("state")
        label = f"{row.get('selector')}@{row_state}"
        if not isinstance(row_state, str) or not row_state.strip():
            failing.append(f"{label} (row without a state)")
            continue
        if row.get("status") != "pass" or row.get("ae") != 0 or row.get("mismatches") != 0:
            failing.append(f"{label} status={row.get('status')!r} ae={row.get('ae')!r} mismatches={row.get('mismatches')!r}")
            continue
        recomputed = diff_records(
            load_record(ref_dir / "frames" / "ref" / computed_name(row_state)),
            load_record(ref_dir / "frames" / "impl" / computed_name(row_state)),
        )
        if recomputed:
            props = ", ".join(
                f"{item['path']}/{item['property']}" if item.get("path") else item["property"]
                for item in recomputed[:5]
            )
            failing.append(f"{label} recomputed {len(recomputed)} computed-style mismatch(es): {props}")
            continue
        passing_states.add(row_state)
    if failing:
        report.fail(
            "diff-element-fail",
            f"{len(failing)} of {len(rows)} element row(s) do not pass: {_listed(failing)}",
        )
    uncovered_states = sorted(s for s in states if s not in passing_states)
    if uncovered_states:
        report.fail(
            "diff-state-uncovered",
            f"resting-state clip(s) with no passing {DIFF_NAME} row: {_listed(uncovered_states)}",
        )
    return data, passing_states


# ── trigger-opened UI ─────────────────────────────────────────────────────


def _is_trigger_opened(
    ref_dir: Path, target: dict[str, Any] | None, ref_frames: dict[str, Path]
) -> bool:
    if (ref_dir / "open.webm").exists() or (ref_dir / "close.webm").exists():
        return True
    if any(Path(n).stem == "open" or Path(n).stem.startswith(("open-", "close-")) for n in ref_frames):
        return True
    annotation = target.get("annotation") if isinstance(target, dict) else None
    attrs = annotation.get("attributes") if isinstance(annotation, dict) else None
    role = attrs.get("role") if isinstance(attrs, dict) else None
    return isinstance(role, str) and role.strip().lower() in {"dialog", "alertdialog"}


def _check_trigger_opened(
    ref_dir: Path,
    ref_frames: dict[str, Path],
    impl_frames: dict[str, Path],
    passing_states: set[str],
    report: _Report,
) -> None:
    for recording in ("open.webm", "close.webm"):
        path = ref_dir / recording
        if not path.is_file() or path.stat().st_size == 0:
            report.fail(
                "trigger-recording-missing",
                f"trigger-opened UI needs the reference {recording} recording "
                "(element-capture.md 'Trigger-opened UI')",
            )
    for side, frames in (("ref", ref_frames), ("impl", impl_frames)):
        _, sequences = split_frames(frames)
        if not any(Path(n).stem == "open" for n in frames):
            report.fail("trigger-open-missing", f"frames/{side}/ has no open.png settled-open clip")
        if "open" not in sequences:
            report.fail(
                "trigger-open-missing",
                f"frames/{side}/ has no opening sequence (open-0001.png ...)",
            )
        if "close" not in sequences:
            report.fail(
                "trigger-close-missing",
                f"frames/{side}/ has no closing sequence (close-0001.png ...)",
            )
    if "open" not in passing_states:
        report.fail(
            "trigger-open-missing",
            f"{DIFF_NAME} has no passing row with state 'open'",
        )


# ── entry points ──────────────────────────────────────────────────────────


def check(ref_dir: Path, impl_root: Path | None = None) -> dict[str, Any]:
    """Run every scoped-completion check; never raises on bad evidence."""
    ref_dir = ref_dir.resolve()
    report = _Report()
    root = impl_root.resolve() if impl_root is not None else default_impl_root(ref_dir)
    shipped = _check_producers(report)
    target = _check_target(ref_dir, report)
    ref_frames, impl_frames = _check_frames(ref_dir, report)
    compared = 0
    sequences: dict[str, Any] = {}
    if ref_frames and impl_frames:
        hashes = {"ref": _hash_frames(ref_frames), "impl": _hash_frames(impl_frames)}
        _check_provenance(ref_dir, target, ref_frames, impl_frames, hashes, shipped, report)
        compared, sequences = _check_frame_fidelity(ref_dir, ref_frames, impl_frames, hashes, report)
    diff, passing_states = _check_diff(ref_dir, ref_frames, root, shipped, report)
    # Every evidence file's current hash must have been recorded by the
    # PostToolUse hook after a canonical producer command (scoped_ledger).
    for code, reason in scoped_ledger.problems(ref_dir):
        report.fail(code, reason)
    trigger_opened = _is_trigger_opened(ref_dir, target, ref_frames)
    if trigger_opened:
        _check_trigger_opened(ref_dir, ref_frames, impl_frames, passing_states, report)
    sources: dict[str, Any] = {}
    if isinstance(diff, dict) and isinstance(diff.get("sources"), dict):
        sources = diff["sources"]
    passed = not report.failures
    return {
        "status": "passed" if passed else "failed",
        "ref_dir": str(ref_dir),
        "impl_root": str(root) if root is not None else None,
        "target": element_target_summary(target),
        "trigger_opened": trigger_opened,
        "frames_compared": compared,
        "impl_sources": sorted(str(p) for p in sources),
        "sequences": {
            prefix: {k: v for k, v in verdict.items() if k != "failing"} for prefix, verdict in sequences.items()
        },
        "failures": report.failures,
        "next_action": (
            "Report the result as a scoped clone with this evidence (not a page-level verified "
            f"clone), naming the {format_target(element_target_summary(target))} so the user can "
            "confirm it is the intended element."
            if passed
            else "Fix each failure, re-capture frames/impl/ with element-state-capture.sh, re-run "
            f"python -m ui_clone.scoped_diff {ref_dir}, then re-run python -m ui_clone.scoped_check {ref_dir}"
        ),
    }


def block_reason(result: dict[str, Any], head: str) -> str:
    """Hook block text: `head`, one bullet per failure, then the command."""
    failures = result["failures"]
    lines = [f"{head} ({len(failures)} issue(s))."]
    lines.extend(f"  - {f['code']}: {f['reason']}" for f in failures[:8])
    if len(failures) > 8:
        lines.append(f"  - +{len(failures) - 8} more")
    lines.append(
        "\nA scoped clone is complete only when "
        f"`python -m ui_clone.scoped_check {result['ref_dir']}` exits 0 "
        "(closeout.md 'Scoped clones'). Fix the evidence above, then re-run it. "
        "Report the result as scoped, never as a page-level verified clone."
    )
    return "\n".join(lines)


def format_target(target: dict[str, Any] | None) -> str:
    """One line naming the resolved target so the user can confirm it is the
    intended element: selector, match count, and bbox."""
    if not isinstance(target, dict):
        return "target: (no valid element-target.json)"
    bbox = target.get("bbox")
    box = (
        f"{bbox.get('width')}x{bbox.get('height')} at ({bbox.get('x')}, {bbox.get('y')})"
        if isinstance(bbox, dict)
        else "?"
    )
    return f"target: {target.get('selector')!r} (matches: {target.get('match_count')}, bbox {box})"


def format_text(result: dict[str, Any]) -> str:
    head = "PASS" if result["status"] == "passed" else "BLOCKED"
    lines = [
        f"scoped_check {head}: {result['ref_dir']}"
        f" (frames compared: {result['frames_compared']},"
        f" trigger-opened: {'yes' if result['trigger_opened'] else 'no'})",
        "  " + format_target(result.get("target")),
    ]
    for prefix, verdict in result.get("sequences", {}).items():
        lines.append(
            f"  {prefix}-*: {'pass' if verdict['pass'] else 'FAIL'} (aligned {verdict['aligned']}, "
            f"arc ref {verdict['ref_arc']} / impl {verdict['impl_arc']}, min SSIM {verdict['min_ssim']})"
        )
    for failure in result["failures"]:
        lines.append(f"  - {failure['code']}: {failure['reason']}")
    lines.append(result["next_action"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.scoped_check",
        description="Completion check for a scoped (section/element/modal) clone.",
    )
    parser.add_argument("ref_dir", help="tmp/ref/<target> directory of the scoped run")
    parser.add_argument(
        "--impl-root",
        default=None,
        help="implementation root for source freshness (default: project root above tmp/ref)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    ref_dir = Path(args.ref_dir)
    if not ref_dir.is_dir():
        print(f"scoped_check: ref dir not found: {ref_dir}", file=sys.stderr)
        return 2
    impl_root = Path(args.impl_root) if args.impl_root else None
    if impl_root is not None and not impl_root.is_dir():
        print(f"scoped_check: impl root not found: {impl_root}", file=sys.stderr)
        return 2
    result = check(ref_dir, impl_root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_text(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
