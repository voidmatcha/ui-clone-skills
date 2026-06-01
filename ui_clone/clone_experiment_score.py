from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _artifact_status(ref_dir: Path, name: str) -> str:
    data = _read_json(ref_dir / name)
    if not isinstance(data, dict):
        return "missing"
    status = data.get("status")
    return str(status) if status else "missing"


def _asset_missing_count(ref_dir: Path) -> tuple[int, bool]:
    data = _read_json(ref_dir / "asset-placement.json")
    if not isinstance(data, dict):
        return 0, False
    missing = data.get("missingPlacements")
    return len(missing) if isinstance(missing, list) else 0, True


def _section_counts(ref_dir: Path) -> tuple[int, int]:
    result = ref_dir / "sections" / "result.txt"
    if not result.is_file():
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
    _ = impl_dir
    asset_missing, has_asset_signal = _asset_missing_count(ref_dir)
    section_pass, section_fail = _section_counts(ref_dir)
    runtime_proof = _artifact_status(ref_dir, "runtime-proof.json")
    transition_proof = _artifact_status(ref_dir, "transition-proof.json")
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
        if done:
            numeric_score += 100_000
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
    if attempt is not None:
        score["attempt"] = attempt
    return score
