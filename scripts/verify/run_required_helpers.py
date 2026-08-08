#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _canonical_runtime_text_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not scheme or host is None:
        return None
    host = host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment))


def _canonical_runtime_text_origin(value: object) -> str | None:
    normalized = _canonical_runtime_text_url(value)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def mtime_ns(path: str) -> int:
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return 0


def runtime_text_urls_match(artifact_name: str, ref_url: str, impl_url: str) -> bool:
    try:
        artifact = json.loads(Path(artifact_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(artifact, dict):
        return False

    expected = {
        "ref": _canonical_runtime_text_url(ref_url),
        "impl": _canonical_runtime_text_url(impl_url),
    }
    if None in expected.values() or expected["ref"] == expected["impl"]:
        return False

    receipt = artifact.get("captureReceipt")
    if not isinstance(receipt, dict):
        return False
    for side, top_requested, top_actual in (
        ("ref", "refUrl", "actualRefUrl"),
        ("impl", "implUrl", "actualImplUrl"),
    ):
        side_receipt = receipt.get(side)
        if not isinstance(side_receipt, dict):
            return False
        observed = (
            artifact.get(top_requested),
            artifact.get(top_actual),
            side_receipt.get("requestedUrl"),
            side_receipt.get("openUrl"),
            side_receipt.get("actualUrl"),
            side_receipt.get("analysisUrl"),
        )
        if any(_canonical_runtime_text_url(value) != expected[side] for value in observed):
            return False
        if _canonical_runtime_text_origin(side_receipt.get("analysisOrigin")) != (
            _canonical_runtime_text_origin(expected[side])
        ):
            return False
    return True


def runtime_text_write_provenance(
    artifact_name: str,
    provenance_name: str,
    ref_url_value: str,
    impl_url_value: str,
    before_mtime_ns_value: str,
) -> bool:
    from ui_clone.gates.verification_plan import (
        _canonical_runtime_url,
        _runtime_text_semantic_error,
    )

    artifact_path = Path(artifact_name)
    provenance_path = Path(provenance_name)
    try:
        raw = artifact_path.read_bytes()
        artifact = json.loads(raw)
        artifact_mtime_ns = artifact_path.stat().st_mtime_ns
        before_mtime_ns = int(before_mtime_ns_value)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    ref_url = _canonical_runtime_url(ref_url_value)
    impl_url = _canonical_runtime_url(impl_url_value)
    if (
        not isinstance(artifact, dict)
        or str(artifact.get("status") or "").lower() != "pass"
        or artifact_mtime_ns == before_mtime_ns
        or ref_url is None
        or impl_url is None
        or ref_url == impl_url
        or _canonical_runtime_url(artifact.get("refUrl")) != ref_url
        or _canonical_runtime_url(artifact.get("implUrl")) != impl_url
        or _runtime_text_semantic_error(artifact) is not None
    ):
        return False
    payload = {
        "schemaVersion": 1,
        "owner": "run-required-checks",
        "artifact": artifact_path.name,
        "refUrl": ref_url,
        "implUrl": impl_url,
        "artifactSha256": hashlib.sha256(raw).hexdigest(),
        "artifactMtimeNs": artifact_mtime_ns,
    }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=provenance_path.parent,
            prefix=f".{provenance_path.name}.",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, provenance_path)
    except OSError:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        return False
    return True


def _hover_state_result(
    ref_dir_name: str,
    artifact_name: str,
) -> tuple[bool, str] | None:
    from ui_clone.evidence_validation import hover_state_partial_result

    try:
        text = Path(artifact_name).read_text(encoding="utf-8")
    except OSError:
        return None
    return hover_state_partial_result(Path(ref_dir_name), text)


def hover_state_valid(ref_dir_name: str, artifact_name: str) -> bool:
    result = _hover_state_result(ref_dir_name, artifact_name)
    if result is None:
        return False
    valid, note = result
    if not valid:
        return False
    print(note)
    return True


def hover_state_partial_valid(ref_dir_name: str, artifact_name: str) -> bool:
    result = _hover_state_result(ref_dir_name, artifact_name)
    if result is None:
        return False
    valid, note = result
    if not valid or "PARTIAL" not in note:
        return False
    print(note)
    return True


def required_check_reusable(
    ref_dir_name: str,
    check_id: str,
    artifact_name: str,
) -> bool:
    from ui_clone.gate import Gate

    ref_dir = Path(ref_dir_name).expanduser().resolve()
    artifact = Path(artifact_name).expanduser().resolve()
    try:
        plan = json.loads(
            (ref_dir / "verification-plan.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    rows = plan.get("requiredChecks") if isinstance(plan, dict) else None
    matches = [
        row
        for row in rows or []
        if isinstance(row, dict) and row.get("id") == check_id
    ]
    if len(matches) != 1:
        return False
    produces = matches[0].get("produces")
    if not isinstance(produces, str) or not produces:
        return False
    if (ref_dir / produces).resolve() != artifact:
        return False
    results = [
        result
        for result in Gate(ref_dir)._check_verification_plan()
        if result.label == f"required: {check_id}"
    ]
    if len(results) != 1 or results[0].status not in {"pass", "warn"}:
        return False
    print(f"{results[0].status}\t{results[0].message}")
    return True


def section_compare_reusable(ref_dir_name: str, artifact_name: str) -> bool:
    from ui_clone.gate import Gate
    from ui_clone.gates.post_implement import _check_sections_result_health

    ref_dir = Path(ref_dir_name).expanduser().resolve()
    artifact = Path(artifact_name).expanduser().resolve()
    if artifact != (ref_dir / "sections" / "result.txt").resolve():
        return False
    gate = Gate(ref_dir)
    health = _check_sections_result_health(gate)
    if health is not None:
        return False
    section_results = gate.gate_section_compare()
    if not section_results or any(result.status == "fail" for result in section_results):
        return False
    print("pass\tsection-compare canonical gates accept result.txt")
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temp_name = handle.name
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise


def persist_impl_binding(ref_dir_name: str, impl_root_name: str) -> bool:
    from ui_clone.state import PipelineState

    ref_dir = Path(ref_dir_name).expanduser().resolve()
    impl_root = Path(impl_root_name).expanduser().resolve()
    if not ref_dir.is_dir():
        print(f"ref dir not found: {ref_dir}", file=sys.stderr)
        return False
    if not impl_root.is_dir():
        print(f"impl root not found: {impl_root}", file=sys.stderr)
        return False
    try:
        state_path = ref_dir / "pipeline-state.json"
        unresolved_quarantines = sorted(ref_dir.glob("pipeline-state.json.corrupt.*"))
        if not state_path.exists() and unresolved_quarantines:
            names = ", ".join(path.name for path in unresolved_quarantines)
            print(
                "pipeline-state.json is absent but unresolved corrupt "
                f"quarantine file(s) remain: {names}",
                file=sys.stderr,
            )
            return False
        state = PipelineState.load(ref_dir)
        if state.load_failed:
            print(
                f"pipeline-state.json at {ref_dir / 'pipeline-state.json'} could not be loaded",
                file=sys.stderr,
            )
            return False
        state.impl_root = str(impl_root)
        state.save(ref_dir)
        _atomic_write_text(ref_dir / ".impl-root", f"{impl_root}\n")
        _atomic_write_text(impl_root / ".ref-dir", f"{ref_dir}\n")
    except OSError as exc:
        print(f"cannot persist impl binding: {exc}", file=sys.stderr)
        return False
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 2
    command = argv[1]
    args = argv[2:]
    if command == "mtime-ns" and len(args) == 1:
        print(mtime_ns(args[0]))
        return 0
    if command == "runtime-text-urls-match" and len(args) == 3:
        return 0 if runtime_text_urls_match(*args) else 1
    if command == "runtime-text-write-provenance" and len(args) == 5:
        return 0 if runtime_text_write_provenance(*args) else 1
    if command == "hover-state-valid" and len(args) == 2:
        return 0 if hover_state_valid(*args) else 1
    if command == "hover-state-partial-valid" and len(args) == 2:
        return 0 if hover_state_partial_valid(*args) else 1
    if command == "required-check-reusable" and len(args) == 3:
        return 0 if required_check_reusable(*args) else 1
    if command == "section-compare-reusable" and len(args) == 2:
        return 0 if section_compare_reusable(*args) else 1
    if command == "persist-impl-binding" and len(args) == 2:
        return 0 if persist_impl_binding(*args) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
