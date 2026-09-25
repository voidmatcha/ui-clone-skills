"""Produce `pixel-perfect-diff.json` for a scoped clone (comparison-fix.md Phase D,
element scope).

    python -m ui_clone.scoped_diff <ref-dir> [--impl-root <dir>] [--impl-files <path> ...] [--json]

No browser is needed: it consumes the evidence `scripts/extract/element-state-capture.sh`
already recorded for every resting-state clip on both sides — `frames/<side>/<state>.png`
and `frames/<side>/<state>.computed.json` (the computed styles of the target
selector AND its element subtree for the `ui_clone.computed_style_diff`
property list, the list `computed-diff.sh` uses; subtree nodes are matched by
structural path, see `computed_style_diff.diff_records`) — and writes one row
per state with the recomputed clip AE and the computed-style mismatches
(computed-diff.sh rules, each with the node `path`, "" for the target). A row
passes only with AE 0 and 0 mismatches.

It also records implementation provenance (`noCheat`,
`ui_clone.scoped_provenance`): the page-level `proxy-mirror-check.sh` and
`bundle-paste-check.sh` run against the implementation root, and the
fingerprinted sources (plus the app entry files) are scanned for reference
loads, whole-document mirrors, raw HTML mounts, and upstream proxies. Any
finding fails the result.

The artifact carries provenance that `python -m ui_clone.scoped_check`
re-validates instead of trusting: `producer`, `schemaVersion`,
`producerRecord` (CLI entry, argv, module sha256), the property list
fingerprint, sha256 of every input (target record, both capture manifests,
every clip and computed record, the no-cheat outputs), of the implementation
sources (`implFiles`, files named after the target under the implementation
root, entry files), and a self checksum. Editing any input, or the file
itself, invalidates it; hand-writing it is denied by the hooks like
`element-target.json`, and so is importing this module from a shell one-liner.

Exit codes: 0 = result pass, 1 = result fail (artifact still written so the
rows can be read), 2 = usage / inputs missing (nothing written).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ui_clone import scoped_producers
from ui_clone.computed_style_diff import COMPUTED_STYLE_PROPS, diff_records, properties_sha256
from ui_clone.element_capture import (
    MANIFEST_NAME,
    code_hotlink_failures,
    computed_name,
    load_manifest,
    manifest_schema_problem,
    origin_of,
    sha256_file,
    verify_side,
)
from ui_clone.hooks._common import ELEMENT_TARGET_NAME, is_valid_element_target, load_json_safe
from ui_clone.scoped_frames import RESTING_STATES, auto_sources, list_frames, pixel_ae, split_frames
from ui_clone.scoped_provenance import (
    PAGE_LEVEL_CHECKS,
    driver_script_sha256,
    entry_sources,
    producer_record,
    record_checksum,
    run_page_level_checks,
    scan_sources,
)

DIFF_NAME = "pixel-perfect-diff.json"
DIFF_PRODUCER = "ui_clone.scoped_diff"
# 3: target subtree rows (`path`), implementation provenance (`noCheat`),
# producer record and self checksum.
DIFF_SCHEMA_VERSION = 3


class DiffInputsError(RuntimeError):
    """Inputs missing or invalid; nothing can be produced."""


def default_impl_root(ref_dir: Path) -> Path | None:
    if ref_dir.parent.name == "ref" and ref_dir.parent.parent.name == "tmp":
        return ref_dir.parent.parent.parent
    return None


def resting_states(ref_frames: dict[str, Path]) -> list[str]:
    static, _ = split_frames(ref_frames)
    return sorted(Path(n).stem for n in static if Path(n).stem in RESTING_STATES)


def load_record(path: Path) -> dict[str, Any] | None:
    """A `<state>.computed.json` record (target styles + subtree), None when unusable."""
    data = load_json_safe(path)
    if data is None or not isinstance(data.get("computedStyle"), dict):
        return None
    return data


def source_hashes(
    ref_dir: Path, impl_root: Path | None, declared: list[str]
) -> tuple[dict[str, str], list[str]]:
    """`{path: sha256}` for declared `implFiles`, auto sources, and the
    implementation entry files (scoped_provenance ENTRY_FILES); missing
    declared paths."""
    hashes: dict[str, str] = {}
    missing: list[str] = []
    for raw in declared:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (impl_root or ref_dir) / candidate
        if candidate.is_file():
            hashes[str(candidate)] = sha256_file(candidate)
        else:
            missing.append(raw)
    if impl_root is not None:
        for path in auto_sources(impl_root, ref_dir.name) + entry_sources(impl_root):
            hashes.setdefault(str(path), sha256_file(path))
    return hashes, missing


def _resolve_root(ref_dir: Path, impl_root: Path | None) -> Path | None:
    return impl_root.resolve() if impl_root is not None else default_impl_root(ref_dir)


def provenance_problems(no_cheat: object) -> list[str]:
    """Why a `noCheat` block does not clear the implementation, else []."""
    if not isinstance(no_cheat, dict):
        return ["no implementation provenance recorded"]
    problems: list[str] = []
    checks = no_cheat.get("pageLevel")
    if not isinstance(checks, dict):
        problems.append("page-level no-cheat checks not recorded")
    else:
        for name in PAGE_LEVEL_CHECKS:
            entry = checks.get(name)
            status = entry.get("status") if isinstance(entry, dict) else None
            if status != "pass":
                reason = entry.get("reason") if isinstance(entry, dict) else None
                problems.append(f"{name}: {status or 'not run'}" + (f" ({reason})" if reason else ""))
    scan = no_cheat.get("sourceScan")
    findings = scan.get("findings") if isinstance(scan, dict) else None
    if not isinstance(findings, list):
        problems.append("implementation source scan not recorded")
    else:
        for item in findings[:5]:
            if isinstance(item, dict):
                problems.append(f"{item.get('rule')}: {item.get('file')} `{item.get('match')}`")
    return problems


def build(
    ref_dir: Path,
    impl_root: Path | None = None,
    impl_files: list[str] | None = None,
    *,
    entry: str = "api",
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Compute the diff artifact (not written). Raises DiffInputsError when inputs are unusable.

    `entry`/`argv` become the producer record; only `main()` passes `cli`,
    which `scoped_check` requires.
    """
    ref_dir = ref_dir.resolve()
    root = _resolve_root(ref_dir, impl_root)
    target_path = ref_dir / ELEMENT_TARGET_NAME
    if not is_valid_element_target(target_path):
        raise DiffInputsError(f"{ELEMENT_TARGET_NAME} missing or invalid; record it with element-evidence.sh")
    target = load_json_safe(target_path) or {}
    selector = str(target["annotation"]["selector"])
    reference_origin = origin_of(target.get("url")) or ""
    ref_frames = list_frames(ref_dir / "frames" / "ref")
    impl_frames = list_frames(ref_dir / "frames" / "impl")
    states = resting_states(ref_frames)
    if not states:
        raise DiffInputsError("frames/ref/ has no resting-state clip (idle/active/before/mid/after/open)")
    problems: list[str] = []
    shipped = scoped_producers.load_manifest()
    driver_sha = scoped_producers.shipped_sha256(scoped_producers.DRIVER_SCRIPT, shipped) or driver_script_sha256()
    module_sha = scoped_producers.shipped_sha256(scoped_producers.CAPTURE_MODULE, shipped)
    for side, frames in (("ref", ref_frames), ("impl", impl_frames)):
        frames_dir = ref_dir / "frames" / side
        if load_manifest(frames_dir) is None:
            problems.append(
                manifest_schema_problem(frames_dir)
                or f"frames/{side}/{MANIFEST_NAME} missing; capture with element-state-capture.sh"
            )
            continue
        static = {n: p for n, p in frames.items() if Path(n).stem in states}
        problems.extend(
            reason
            for _, reason in verify_side(
                frames_dir,
                static,
                reference_origin=reference_origin,
                side=side,
                driver_sha256=driver_sha,
                module_sha256=module_sha,
            )
        )
    problems.extend(reason for _, reason in code_hotlink_failures(ref_dir))
    inputs: dict[str, str] = {ELEMENT_TARGET_NAME: sha256_file(target_path)}
    for side in ("ref", "impl"):
        manifest = ref_dir / "frames" / side / MANIFEST_NAME
        if manifest.is_file():
            inputs[f"frames/{side}/{MANIFEST_NAME}"] = sha256_file(manifest)
    rows: list[dict[str, Any]] = []
    total_mismatches = 0
    for state in states:
        row: dict[str, Any] = {"selector": selector, "state": state}
        missing_inputs: list[str] = []
        for side in ("ref", "impl"):
            for name in (f"{state}.png", computed_name(state)):
                path = ref_dir / "frames" / side / name
                if path.is_file():
                    inputs[f"frames/{side}/{name}"] = sha256_file(path)
                else:
                    missing_inputs.append(f"frames/{side}/{name}")
        if missing_inputs:
            row.update({"status": "fail", "error": f"missing {', '.join(missing_inputs)}"})
            rows.append(row)
            problems.append(f"{state}: missing {', '.join(missing_inputs)}")
            continue
        ae = pixel_ae(ref_dir / "frames" / "ref" / f"{state}.png", ref_dir / "frames" / "impl" / f"{state}.png")
        ref_record = load_record(ref_dir / "frames" / "ref" / computed_name(state))
        impl_record = load_record(ref_dir / "frames" / "impl" / computed_name(state))
        diff = diff_records(ref_record, impl_record)
        total_mismatches += len(diff)
        row.update(
            {
                "ae": ae if ae is not None else "size-mismatch",
                "mismatches": len(diff),
                "subtreeNodes": len(ref_record.get("subtree", [])) if isinstance(ref_record, dict) else 0,
                "diff": diff,
                "status": "pass" if ae == 0 and not diff else "fail",
            }
        )
        rows.append(row)
    declared = list(impl_files or [])
    sources, missing_sources = source_hashes(ref_dir, root, declared)
    if missing_sources:
        problems.append(f"implFiles not found: {', '.join(missing_sources)}")
    # Implementation provenance: page-level no-cheat scripts on the impl root,
    # then the fingerprinted sources scanned for reference-runtime signals.
    page_level = run_page_level_checks(ref_dir, root)
    for name, check in page_level.items():
        output = ref_dir / str(check.get("output"))
        if output.is_file():
            inputs[str(check["output"])] = sha256_file(output)
    scanned = [Path(p) for p in sources]
    no_cheat = {
        "pageLevel": page_level,
        "sourceScan": {"files": len(scanned), "findings": scan_sources(scanned, reference_origin)},
    }
    problems.extend(f"implementation provenance: {p}" for p in provenance_problems(no_cheat))
    result = "pass" if not problems and all(r.get("status") == "pass" for r in rows) else "fail"
    return {
        "schemaVersion": DIFF_SCHEMA_VERSION,
        "producer": DIFF_PRODUCER,
        "producerRecord": producer_record(__name__, entry, argv if argv is not None else []),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "component": ref_dir.name,
        "selector": selector,
        "referenceOrigin": reference_origin,
        "properties": list(COMPUTED_STYLE_PROPS),
        "propertiesSha256": properties_sha256(),
        "result": result,
        "mismatches": total_mismatches,
        "problems": problems,
        "implFiles": declared,
        "sources": sources,
        "inputs": inputs,
        "noCheat": no_cheat,
        "elements": rows,
    }


def write(ref_dir: Path, data: dict[str, Any]) -> Path:
    """Write the artifact with its self checksum (`recordSha256`)."""
    data["recordSha256"] = record_checksum(data)
    path = ref_dir / DIFF_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def format_text(data: dict[str, Any], path: Path) -> str:
    lines = [f"scoped_diff {data['result'].upper()}: {path} (mismatches: {data['mismatches']})"]
    for row in data["elements"]:
        if row.get("status") == "pass":
            lines.append(f"  - {row['state']}: pass (AE 0, 0 mismatches, {row.get('subtreeNodes', 0)} subtree nodes)")
            continue
        detail = row.get("error") or f"AE {row.get('ae')}, {row.get('mismatches')} mismatch(es)"
        lines.append(f"  - {row['state']}: FAIL ({detail})")
        for item in row.get("diff", [])[:8]:
            where = f"{item['path']} " if item.get("path") else ""
            lines.append(f"      {where}{item['property']}: ref `{item['ref']}` vs impl `{item['impl']}`")
    for problem in data["problems"]:
        lines.append(f"  - {problem}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.scoped_diff",
        description="Produce pixel-perfect-diff.json for a scoped clone from script-captured evidence.",
    )
    parser.add_argument("ref_dir", help="tmp/ref/<target> directory of the scoped run")
    parser.add_argument("--impl-root", default=None, help="implementation root (default: project root above tmp/ref)")
    parser.add_argument("--impl-files", nargs="*", default=[], help="implementation sources to fingerprint")
    parser.add_argument("--json", action="store_true", help="print the artifact as JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    ref_dir = Path(args.ref_dir)
    if not ref_dir.is_dir():
        print(f"scoped_diff: ref dir not found: {ref_dir}", file=sys.stderr)
        return 2
    impl_root = Path(args.impl_root) if args.impl_root else None
    if impl_root is not None and not impl_root.is_dir():
        print(f"scoped_diff: impl root not found: {impl_root}", file=sys.stderr)
        return 2
    try:
        data = build(
            ref_dir,
            impl_root,
            list(args.impl_files),
            entry="cli",
            argv=list(sys.argv[1:] if argv is None else argv),
        )
    except DiffInputsError as exc:
        print(f"scoped_diff: {exc}", file=sys.stderr)
        return 2
    path = write(ref_dir.resolve(), data)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(format_text(data, path))
    return 0 if data["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
