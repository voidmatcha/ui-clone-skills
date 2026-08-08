from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ui_clone.check_inputs import (
    compute_check_input_hash,
    get_check_inputs,
    newest_input_mtime,
    sidecar_path,
)
from ui_clone.evidence_validation import (
    load_strict_json_text,
    transition_proof_semantic_error,
    visual_fidelity_semantic_error,
)
from ui_clone.gates.verification_plan import (
    _runtime_text_provenance_error,
    _runtime_text_semantic_error,
)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return load_strict_json_text(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _newest_impl_mtime(impl_dir: Path) -> float | None:
    # Reuse the registered whole-implementation profile so an empty directory,
    # a tree containing only pruned build output, or unreadable traversal is
    # unavailable rather than the legacy false-fresh 0.0 sentinel.
    return newest_input_mtime(impl_dir, None, "impl-scope")


def _artifact_is_fresh(
    ref_dir: Path,
    impl_dir: Path,
    artifact: Path,
    check_id: str,
) -> bool:
    if not artifact.is_file():
        return False
    spec = get_check_inputs(check_id)
    fingerprint = sidecar_path(ref_dir, check_id)
    try:
        impl_newest = _newest_impl_mtime(impl_dir)
        if impl_newest is None:
            return False
        if check_id == "transition-proof" and not fingerprint.is_file():
            return False
        if spec is not None and (fingerprint.exists() or fingerprint.is_symlink()):
            stored_hash = fingerprint.read_text(encoding="utf-8").strip()
            current_hash = compute_check_input_hash(impl_dir, ref_dir, check_id)
            return bool(stored_hash) and current_hash == stored_hash
        newest = (
            newest_input_mtime(impl_dir, ref_dir, check_id)
            if spec is not None
            else impl_newest
        )
        return newest is not None and artifact.stat().st_mtime >= newest
    except OSError:
        return False


def _artifact_status(ref_dir: Path, impl_dir: Path, name: str, check_id: str) -> str:
    path = ref_dir / name
    if not _artifact_is_fresh(ref_dir, impl_dir, path, check_id):
        return "missing"
    data = _read_json(path)
    if not isinstance(data, dict):
        return "missing"
    status = data.get("status")
    return str(status) if status else "missing"


def _asset_missing_count(ref_dir: Path, impl_dir: Path) -> tuple[int, bool]:
    path = ref_dir / "asset-placement.json"
    if not _artifact_is_fresh(ref_dir, impl_dir, path, "asset-placement"):
        return 0, False
    data = _read_json(path)
    if not isinstance(data, dict):
        return 0, False
    missing = data.get("missingPlacements")
    return len(missing) if isinstance(missing, list) else 0, True


def _section_counts(ref_dir: Path, impl_dir: Path) -> tuple[int, int]:
    result = ref_dir / "sections" / "result.txt"
    if not _artifact_is_fresh(ref_dir, impl_dir, result, "section-compare"):
        return 0, 0
    try:
        lines = result.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, 0
    section_pass = 0
    section_fail = 0
    for raw in lines:
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        first = cells[0].lower() if cells else ""
        if first == "section" or (first and set(first) <= {"-"}):
            continue
        if "MISSING impl" in line or "❌" in line:
            section_fail += 1
        elif "✅" in line or "STRUCTURAL_ONLY" in line:
            section_pass += 1
    return section_pass, section_fail


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _section_fidelity(ref_dir: Path, impl_dir: Path) -> tuple[float, float, int] | None:
    path = ref_dir / "sections" / "result.json"
    if not _artifact_is_fresh(ref_dir, impl_dir, path, "section-compare"):
        return None
    data = _read_json(path)
    if (
        not isinstance(data, dict)
        or data.get("schemaVersion") != 1
        or not isinstance(data.get("sections"), list)
        or not isinstance(data.get("summary"), dict)
    ):
        return None

    sections = data["sections"]
    summary = data["summary"]
    counts = [summary.get(name) for name in ("pass", "fail", "skip", "structuralOnly")]
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts
    ) or sum(count for count in counts if isinstance(count, int)) != len(sections):
        return None
    structural_only = summary["structuralOnly"]
    assert isinstance(structural_only, int)

    values: list[float] = []
    for section in sections:
        if not isinstance(section, dict):
            return None
        raw = section.get("aePerMpx")
        value = _number(raw)
        if value is None or value < 0:
            return None
        values.append(value)
    if not values:
        return None
    return sum(values) / len(values), max(values), structural_only


def _visual_fidelity(ref_dir: Path, impl_dir: Path) -> tuple[str, float, float, float] | None:
    path = ref_dir / "visual-fidelity-judge.json"
    if not _artifact_is_fresh(ref_dir, impl_dir, path, "visual-fidelity-judge"):
        return None
    data = _read_json(path)
    if visual_fidelity_semantic_error(data) is not None:
        return None

    assert isinstance(data, dict)
    overall = data["overall"]
    motion = data["motion"]
    assert isinstance(overall, dict)
    assert isinstance(motion, dict)
    axes = motion["axes"]
    assert isinstance(axes, dict)
    score = _number(overall.get("score"))
    minimum = _number(overall.get("min"))
    animation = _number(axes.get("animation"))
    assert score is not None and minimum is not None and animation is not None
    return data["status"], score, minimum, animation


def _runtime_text_fidelity(ref_dir: Path, impl_dir: Path) -> tuple[str, float, int, int] | None:
    path = ref_dir / "runtime-text-sequence.json"
    if not _artifact_is_fresh(ref_dir, impl_dir, path, "runtime-text-sequence"):
        return None
    data = _read_json(path)
    if (
        not isinstance(data, dict)
        or data.get("schemaVersion") != 1
        or data.get("status") not in {"pass", "fail"}
        or not isinstance(data.get("comparison"), dict)
        or not isinstance(data.get("violations"), list)
    ):
        return None

    def valid_capture(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        block_count = value.get("blockCount")
        blocks = value.get("blocks")
        return (
            isinstance(block_count, int)
            and not isinstance(block_count, bool)
            and isinstance(blocks, list)
            and block_count == len(blocks)
            and all(isinstance(item, str) for item in blocks)
        )

    if not valid_capture(data.get("ref")) or not valid_capture(data.get("impl")):
        return None

    comparison = data["comparison"]
    similarity = _number(comparison.get("orderedSimilarity"))
    lcs_length = comparison.get("lcsLength")
    missing_ratio = _number(comparison.get("missingRatio"))
    missing = comparison.get("missingCount")
    extra = comparison.get("extraCount")
    if (
        similarity is None
        or not 0 <= similarity <= 1
        or isinstance(lcs_length, bool)
        or not isinstance(lcs_length, int)
        or lcs_length < 0
        or missing_ratio is None
        or not 0 <= missing_ratio <= 1
        or isinstance(missing, bool)
        or not isinstance(missing, int)
        or missing < 0
        or isinstance(extra, bool)
        or not isinstance(extra, int)
        or extra < 0
    ):
        return None
    if _runtime_text_semantic_error(data) is not None:
        return None
    if _runtime_text_provenance_error(ref_dir, path, data) is not None:
        return None
    return data["status"], similarity, missing, extra


def _transition_proof_status(ref_dir: Path, impl_dir: Path) -> str:
    path = ref_dir / "transition-proof.json"
    if not _artifact_is_fresh(ref_dir, impl_dir, path, "transition-proof"):
        return "missing"
    data = _read_json(path)
    if transition_proof_semantic_error(ref_dir, data) is not None:
        return "missing"
    assert isinstance(data, dict)
    return str(data["status"])


def _proof_points(status: str) -> int:
    if status == "pass":
        return 500
    if status == "skip":
        return 100
    if status == "fail":
        return -200
    return 0


def score_clone_attempt(
    ref_dir: Path,
    impl_dir: Path,
    *,
    attempt: int | None = None,
    completion_status: str | None = None,
) -> dict[str, Any]:
    implementation_available = _newest_impl_mtime(impl_dir) is not None
    asset_missing, has_asset_signal = _asset_missing_count(ref_dir, impl_dir)
    section_pass, section_fail = _section_counts(ref_dir, impl_dir)
    runtime_proof = _artifact_status(ref_dir, impl_dir, "runtime-proof.json", "runtime-proof")
    transition_proof = _transition_proof_status(ref_dir, impl_dir)
    section_fidelity = _section_fidelity(ref_dir, impl_dir)
    visual_fidelity = _visual_fidelity(ref_dir, impl_dir)
    runtime_text = _runtime_text_fidelity(ref_dir, impl_dir)
    status = completion_status or "wip"
    done = status == "done"

    if status == "contaminated":
        numeric_score = -100_000
    else:
        numeric_score = 0
        if has_asset_signal:
            numeric_score += max(0, 1000 - asset_missing * 25)
        numeric_score += section_pass * 100
        numeric_score -= section_fail * 150
        numeric_score += _proof_points(runtime_proof)
        numeric_score += _proof_points(transition_proof)
        if section_fidelity is not None:
            ae_mean, ae_worst, structural_only = section_fidelity
            numeric_score += round(750 * max(0.0, 1.0 - ae_mean / 50_000))
            numeric_score += round(750 * max(0.0, 1.0 - ae_worst / 50_000))
            numeric_score -= structural_only * 250
        if visual_fidelity is not None:
            _, overall, minimum, animation = visual_fidelity
            numeric_score += round((overall + minimum + animation) * 100)
        if runtime_text is not None:
            text_status, similarity, missing, extra = runtime_text
            numeric_score += round(similarity * 1500)
            numeric_score -= missing * 100
            numeric_score -= extra * 50
            if text_status == "fail":
                numeric_score -= 250
            elif text_status == "error":
                numeric_score -= 500
        if done and implementation_available:
            numeric_score += 1
        numeric_score = max(0, numeric_score)

    score: dict[str, Any] = {
        "done": done,
        "score": int(numeric_score),
        "assetMissing": asset_missing,
        "sectionPass": section_pass,
        "sectionFail": section_fail,
        "runtimeProof": runtime_proof,
        "transitionProof": transition_proof,
        "completionStatus": status,
    }
    if section_fidelity is not None:
        ae_mean, ae_worst, structural_only = section_fidelity
        score["sectionAePerMpxMean"] = round(ae_mean, 2)
        score["sectionAePerMpxWorst"] = round(ae_worst, 2)
        score["sectionStructuralOnly"] = structural_only
    if visual_fidelity is not None:
        judge_status, overall, minimum, animation = visual_fidelity
        score["visualFidelityStatus"] = judge_status
        score["visualFidelityOverall"] = overall
        score["visualFidelityMin"] = minimum
        score["visualFidelityAnimation"] = animation
    if runtime_text is not None:
        text_status, similarity, missing, extra = runtime_text
        score["runtimeTextStatus"] = text_status
        score["runtimeTextOrderedSimilarity"] = similarity
        score["runtimeTextMissing"] = missing
        score["runtimeTextExtra"] = extra
    if attempt is not None:
        score["attempt"] = attempt
    return score
