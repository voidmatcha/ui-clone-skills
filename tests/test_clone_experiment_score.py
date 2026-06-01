from __future__ import annotations

import json
from pathlib import Path

from ui_clone.clone_experiment_score import score_clone_attempt


def test_score_clone_attempt_reads_asset_section_and_proof_artifacts(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (ref / "sections").mkdir(parents=True)
    impl.mkdir()
    (ref / "asset-placement.json").write_text(
        json.dumps({"status": "fail", "missingPlacements": [{"src": "a.png"}, {"src": "b.png"}]}),
        encoding="utf-8",
    )
    (ref / "sections" / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| hero | 0 | 0 | ok | ✅ |\n"
        "| product | 100 | 3000 | major | ❌ |\n"
        "| footer | 0 | 0 | missing | ⚠️ MISSING impl |\n",
        encoding="utf-8",
    )
    (ref / "runtime-proof.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "transition-proof.json").write_text(json.dumps({"status": "fail"}), encoding="utf-8")

    score = score_clone_attempt(ref, impl, attempt=2)

    assert score["done"] is False
    assert score["assetMissing"] == 2
    assert score["sectionPass"] == 1
    assert score["sectionFail"] == 2
    assert score["runtimeProof"] == "pass"
    assert score["transitionProof"] == "fail"
    assert score["completionStatus"] == "wip"
    assert score["attempt"] == 2
    assert isinstance(score["score"], int)


def test_score_clone_attempt_contaminated_completion_maps_to_low_score(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    (ref / "asset-placement.json").write_text(
        json.dumps({"status": "pass", "missingPlacements": []}),
        encoding="utf-8",
    )
    (ref / "runtime-proof.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "transition-proof.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")

    clean = score_clone_attempt(ref, impl, completion_status="done")
    contaminated = score_clone_attempt(ref, impl, completion_status="contaminated")

    assert clean["done"] is True
    assert contaminated["done"] is False
    assert contaminated["completionStatus"] == "contaminated"
    assert contaminated["score"] < clean["score"]
    assert contaminated["score"] < 0


def test_score_clone_attempt_missing_or_malformed_artifacts_is_stable_incomplete(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    (ref / "asset-placement.json").write_text("{not-json", encoding="utf-8")

    score = score_clone_attempt(ref, impl)

    assert score == {
        "done": False,
        "score": 0,
        "assetMissing": 0,
        "sectionPass": 0,
        "sectionFail": 0,
        "runtimeProof": "missing",
        "transitionProof": "missing",
        "completionStatus": "wip",
    }
