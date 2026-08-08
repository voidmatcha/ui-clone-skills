from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from ui_clone.check_inputs import compute_check_input_hash, sidecar_path
from ui_clone.clone_experiment_score import score_clone_attempt


def _write_impl(impl: Path) -> Path:
    source = impl / "src" / "app.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const version = 1;\n", encoding="utf-8")
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    return source


def _write_visual_ref_input(ref: Path) -> None:
    (ref / "transition-spec.json").write_text(
        '{"transitions":[]}\n',
        encoding="utf-8",
    )


def _write_runtime_text_ref_input(ref: Path) -> None:
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")


def _runtime_text_record(
    text: str,
    slot: str,
    tag: str = "P",
) -> dict[str, object]:
    return {"slot": slot, "text": text, "tag": tag, "initialViewport": False}


def _runtime_text_artifact(
    ref_records: list[dict[str, object]],
    impl_records: list[dict[str, object]],
    *,
    ref_samples: list[list[dict[str, object]]],
    impl_samples: list[list[dict[str, object]]],
    phase_variance: dict[str, object],
    lcs_length: int,
) -> dict[str, object]:
    ref_url = "https://reference.example/runtime-text"
    impl_url = "http://127.0.0.1:4173/runtime-text"

    def receipt(url: str) -> dict[str, object]:
        origin = "https://reference.example" if url == ref_url else "http://127.0.0.1:4173"
        return {
            "requestedUrl": url,
            "openUrl": url,
            "actualUrl": url,
            "analysisUrl": url,
            "analysisOrigin": origin,
            "responseStatus": 200,
            "readyState": "complete",
            "navigationType": "navigate",
            "errorDocument": False,
            "batchCommandCount": 6,
            "attempt": 1,
            "closeAttempts": 1,
            "closed": True,
        }

    ref_blocks = [str(record["text"]) for record in ref_records]
    impl_blocks = [str(record["text"]) for record in impl_records]
    missing_count = len(ref_blocks) - lcs_length
    extra_count = len(impl_blocks) - lcs_length
    return {
        "schemaVersion": 1,
        "status": "pass",
        "refUrl": ref_url,
        "implUrl": impl_url,
        "actualRefUrl": ref_url,
        "actualImplUrl": impl_url,
        "captureReceipt": {
            "ref": receipt(ref_url),
            "impl": receipt(impl_url),
        },
        "thresholds": {
            "minOrderedSimilarity": 0.85,
            "maxMissingRatio": 0.15,
            "maxMissingBlocks": max(1, int(len(ref_blocks) * 0.15)),
        },
        "ref": {
            "blockCount": len(ref_blocks),
            "blocks": ref_blocks,
            "records": ref_records,
            "samples": ref_samples,
            "phaseSampleStartIndex": 0,
        },
        "impl": {
            "blockCount": len(impl_blocks),
            "blocks": impl_blocks,
            "records": impl_records,
            "samples": impl_samples,
            "phaseSampleStartIndex": 0,
        },
        "comparison": {
            "lcsLength": lcs_length,
            "orderedSimilarity": round(
                2 * lcs_length / (len(ref_blocks) + len(impl_blocks)),
                4,
            ),
            "missingCount": missing_count,
            "missingRatio": round(missing_count / len(ref_blocks), 4),
            "extraCount": extra_count,
        },
        "phaseVariance": phase_variance,
        "violations": [],
    }


def _seal_runtime_text_artifact(ref: Path) -> None:
    artifact = ref / "runtime-text-sequence.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    artifact_bytes = artifact.read_bytes()
    (ref / "runtime-text-sequence.provenance.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "owner": "run-required-checks",
                "artifact": artifact.name,
                "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "artifactMtimeNs": artifact.stat().st_mtime_ns,
                "refUrl": data["refUrl"],
                "implUrl": data["implUrl"],
            }
        ),
        encoding="utf-8",
    )


def _write_valid_transition_proof(ref: Path, impl: Path) -> None:
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "hero-load", "trigger": "page-load"},
                    {"id": "hero-scroll", "trigger": "scroll-scrub"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-spec-coverage.json").write_text(
        json.dumps({"status": "pass", "total": 2, "covered": 2}),
        encoding="utf-8",
    )
    (ref / "spec-implementation-coverage.json").write_text(
        json.dumps({"status": "pass", "total": 2, "withMotion": 2}),
        encoding="utf-8",
    )
    (ref / "transition-coverage.json").write_text(
        json.dumps(
            {
                "animatedElements": [
                    {
                        "selector": ".hero",
                        "samples": [{"opacity": 0}, {"opacity": 1}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 1,
                "known_skip": 1,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [
                    {"id": "hero-scroll", "status": "known-skip"},
                    {"id": "hero-load", "status": "pass"},
                ],
            }
        ),
        encoding="utf-8",
    )
    script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "transition-proof-rollup.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fingerprint = compute_check_input_hash(impl, ref, "transition-proof")
    assert fingerprint
    sidecar_path(ref, "transition-proof").write_text(
        fingerprint, encoding="utf-8"
    )


def test_score_clone_attempt_reads_asset_section_and_proof_artifacts(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (ref / "sections").mkdir(parents=True)
    _write_impl(impl)
    (ref / "visible-images.json").write_text('{"images":[]}\n', encoding="utf-8")
    (ref / "blank-viewport.json").write_text('{"status":"pass"}\n', encoding="utf-8")
    _write_visual_ref_input(ref)
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
    assert score["transitionProof"] == "missing"
    assert score["completionStatus"] == "wip"
    assert score["attempt"] == 2
    assert isinstance(score["score"], int)


def test_score_clone_attempt_contaminated_completion_maps_to_low_score(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    (ref / "visible-images.json").write_text('{"images":[]}\n', encoding="utf-8")
    (ref / "blank-viewport.json").write_text('{"status":"pass"}\n', encoding="utf-8")
    _write_visual_ref_input(ref)
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


def test_forged_transition_pass_with_fresh_fingerprint_scores_no_points(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    _write_valid_transition_proof(ref, impl)

    proven = score_clone_attempt(ref, impl)
    sidecar_path(ref, "transition-proof").unlink()
    sidecarless = score_clone_attempt(ref, impl)
    (ref / "transition-proof.json").write_text(
        json.dumps({"schemaVersion": 1, "status": "pass"}),
        encoding="utf-8",
    )
    forged_hash = compute_check_input_hash(impl, ref, "transition-proof")
    assert forged_hash is not None
    sidecar_path(ref, "transition-proof").write_text(
        forged_hash, encoding="utf-8"
    )
    forged = score_clone_attempt(ref, impl)

    assert proven["transitionProof"] == "pass"
    assert sidecarless["transitionProof"] == "missing"
    assert forged["transitionProof"] == "missing"
    assert proven["score"] - forged["score"] == 500


def test_score_clone_attempt_missing_or_malformed_artifacts_is_stable_incomplete(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    (ref / "visible-images.json").write_text('{"images":[]}\n', encoding="utf-8")
    _write_visual_ref_input(ref)
    _write_runtime_text_ref_input(ref)
    (ref / "asset-placement.json").write_text("{not-json", encoding="utf-8")
    (ref / "sections").mkdir()
    (ref / "sections" / "result.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "summary": {"structuralOnly": 0},
                "sections": [{"aePerMpx": "not-a-number"}],
            }
        ),
        encoding="utf-8",
    )
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 10, "min": 10},
                "staticSections": [],
                "motion": {"axes": {"layout": 10, "text": 10, "color": 10, "animation": None}},
            }
        ),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "ref": {"blockCount": 0, "blocks": []},
                "impl": {"blockCount": 0, "blocks": []},
                "comparison": {"orderedSimilarity": 2, "missingCount": 0, "extraCount": 0},
                "violations": [],
            }
        ),
        encoding="utf-8",
    )

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


def test_materially_bad_done_scores_below_strong_wip(tmp_path: Path) -> None:
    impl = tmp_path / "impl"
    _write_impl(impl)

    def write_fidelity(ref: Path, *, strong: bool) -> None:
        (ref / "sections").mkdir(parents=True)
        _write_visual_ref_input(ref)
        _write_runtime_text_ref_input(ref)
        ae_values = [500, 900] if strong else [80_000, 120_000]
        (ref / "sections" / "result.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "summary": {"pass": 2, "fail": 0, "skip": 0, "structuralOnly": 0},
                    "sections": [
                        {"name": f"section-{index}", "aePerMpx": value}
                        for index, value in enumerate(ae_values)
                    ],
                }
            ),
            encoding="utf-8",
        )
        judge_score = 9 if strong else 2
        (ref / "visual-fidelity-judge.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "pass" if strong else "fail",
                    "overall": {"score": judge_score, "min": judge_score},
                    "staticSections": [],
                    "motion": {
                        "axes": {
                            "layout": judge_score,
                            "text": judge_score,
                            "color": judge_score,
                            "animation": judge_score,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        if strong:
            records = [
                _runtime_text_record(
                    f"reference-{index}",
                    f"main>p:nth-of-type({index + 1})::run(1)",
                )
                for index in range(10)
            ]
            runtime_text = _runtime_text_artifact(
                records,
                records,
                ref_samples=[records, records],
                impl_samples=[records, records],
                phase_variance={"accepted": False, "reason": "exact-match"},
                lcs_length=10,
            )
        else:
            runtime_text = {
                "schemaVersion": 1,
                "status": "fail",
                "ref": {
                    "blockCount": 8,
                    "blocks": [f"reference-{index}" for index in range(8)],
                },
                "impl": {
                    "blockCount": 6,
                    "blocks": [f"different-{index}" for index in range(6)],
                },
                "comparison": {
                    "lcsLength": 0,
                    "orderedSimilarity": 0,
                    "missingRatio": 1,
                    "missingCount": 8,
                    "extraCount": 6,
                },
                "violations": [{"kind": "sequence-mismatch"}],
            }
        (ref / "runtime-text-sequence.json").write_text(
            json.dumps(runtime_text),
            encoding="utf-8",
        )
        if strong:
            _seal_runtime_text_artifact(ref)

    strong_ref = tmp_path / "strong-ref"
    bad_ref = tmp_path / "bad-ref"
    write_fidelity(strong_ref, strong=True)
    write_fidelity(bad_ref, strong=False)

    strong_wip = score_clone_attempt(strong_ref, impl)
    bad_done = score_clone_attempt(bad_ref, impl, completion_status="done")

    assert strong_wip["done"] is False
    assert bad_done["done"] is True
    assert bad_done["score"] < strong_wip["score"]
    assert strong_wip["sectionAePerMpxMean"] == 700.0
    assert strong_wip["sectionAePerMpxWorst"] == 900.0
    assert strong_wip["sectionStructuralOnly"] == 0
    assert strong_wip["visualFidelityOverall"] == 9.0
    assert strong_wip["visualFidelityMin"] == 9.0
    assert strong_wip["visualFidelityAnimation"] == 9.0
    assert strong_wip["runtimeTextOrderedSimilarity"] == 1.0
    assert strong_wip["runtimeTextMissing"] == 0
    assert strong_wip["runtimeTextExtra"] == 0


def test_stale_strong_artifact_cannot_outrank_fresh_wip(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    source = impl / "src" / "app.ts"
    source.write_text("export const version = 1;\n", encoding="utf-8")
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    ref.mkdir()
    _write_visual_ref_input(ref)
    artifact = ref / "visual-fidelity-judge.json"
    artifact.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 10, "min": 10},
                "staticSections": [],
                "motion": {"axes": {"layout": 10, "text": 10, "color": 10, "animation": 10}},
            }
        ),
        encoding="utf-8",
    )

    fresh_wip = score_clone_attempt(ref, impl)
    source.write_text("export const version = 2;\n", encoding="utf-8")
    future_ns = artifact.stat().st_mtime_ns + 2_000_000_000
    os.utime(source, ns=(future_ns, future_ns))
    stale_done = score_clone_attempt(ref, impl, completion_status="done")

    assert fresh_wip["score"] == 3000
    assert stale_done["score"] == 1
    assert stale_done["score"] < fresh_wip["score"]
    assert "visualFidelityOverall" not in stale_done


def test_fingerprint_sidecar_detects_stale_content_with_old_mtime(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    source = impl / "src" / "app.ts"
    source.write_text("export const version = 1;\n", encoding="utf-8")
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    ref.mkdir()
    _write_visual_ref_input(ref)
    artifact = ref / "visual-fidelity-judge.json"
    artifact.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 8, "min": 8},
                "staticSections": [],
                "motion": {"axes": {"layout": 8, "text": 8, "color": 8, "animation": 8}},
            }
        ),
        encoding="utf-8",
    )
    fingerprint = compute_check_input_hash(impl, ref, "visual-fidelity-judge")
    assert fingerprint
    sidecar_path(ref, "visual-fidelity-judge").write_text(
        fingerprint,
        encoding="utf-8",
    )
    assert "visualFidelityOverall" in score_clone_attempt(ref, impl)

    source.write_text("export const version = 2;\n", encoding="utf-8")
    old_ns = artifact.stat().st_mtime_ns - 2_000_000_000
    os.utime(source, ns=(old_ns, old_ns))

    assert "visualFidelityOverall" not in score_clone_attempt(ref, impl)


def test_empty_impl_with_reference_fidelity_evidence_scores_zero(tmp_path: Path) -> None:
    """Reference-only artifacts cannot turn an empty clone into a scored attempt."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _write_visual_ref_input(ref)
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 10, "min": 10},
                "staticSections": [],
                "motion": {
                    "axes": {
                        "layout": 10,
                        "text": 10,
                        "color": 10,
                        "animation": 10,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    score = score_clone_attempt(ref, impl, completion_status="done")

    assert score["done"] is True
    assert score["score"] == 0
    assert "visualFidelityOverall" not in score


def test_deleted_declared_impl_input_invalidates_scored_sidecar(tmp_path: Path) -> None:
    """Deleting the only declared SRC file makes a prior visual hash unusable."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    source = _write_impl(impl)
    _write_visual_ref_input(ref)
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 8, "min": 8},
                "staticSections": [],
                "motion": {
                    "axes": {
                        "layout": 8,
                        "text": 8,
                        "color": 8,
                        "animation": 8,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    fingerprint = compute_check_input_hash(impl, ref, "visual-fidelity-judge")
    assert fingerprint
    sidecar_path(ref, "visual-fidelity-judge").write_text(
        fingerprint,
        encoding="utf-8",
    )
    assert "visualFidelityOverall" in score_clone_attempt(ref, impl)

    source.unlink()

    score = score_clone_attempt(ref, impl)
    assert score["score"] == 0
    assert "visualFidelityOverall" not in score


def test_real_unreadable_impl_input_invalidates_scored_sidecar(tmp_path: Path) -> None:
    """A real read denial fails score evidence closed without monkeypatching."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    source = _write_impl(impl)
    _write_visual_ref_input(ref)
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 8, "min": 8},
                "staticSections": [],
                "motion": {
                    "axes": {
                        "layout": 8,
                        "text": 8,
                        "color": 8,
                        "animation": 8,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    fingerprint = compute_check_input_hash(impl, ref, "visual-fidelity-judge")
    assert fingerprint
    sidecar_path(ref, "visual-fidelity-judge").write_text(
        fingerprint,
        encoding="utf-8",
    )
    source.chmod(0)
    try:
        if os.access(source, os.R_OK):
            pytest.skip("test process can read chmod(0) files")
        score = score_clone_attempt(ref, impl)
    finally:
        source.chmod(0o600)

    assert score["score"] == 0
    assert "visualFidelityOverall" not in score


def test_semantically_inconsistent_fidelity_artifacts_are_ignored(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (ref / "sections").mkdir(parents=True)
    _write_impl(impl)
    _write_visual_ref_input(ref)
    _write_runtime_text_ref_input(ref)
    (ref / "sections" / "result.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "summary": {"pass": 2, "fail": 0, "skip": 0, "structuralOnly": 0},
                "sections": [{"aePerMpx": 0}, {"aePerMpx": None}],
            }
        ),
        encoding="utf-8",
    )
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 10, "min": 10},
                "staticSections": [{"label": "hero", "score": 1}],
                "motion": {"axes": {"layout": 10, "text": 10, "color": 10, "animation": 10}},
            }
        ),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "ref": {"blockCount": 1, "blocks": ["real copy"]},
                "impl": {"blockCount": 1, "blocks": ["wrong copy"]},
                "comparison": {
                    "lcsLength": 1,
                    "orderedSimilarity": 1,
                    "missingCount": 0,
                    "missingRatio": 0,
                    "extraCount": 0,
                },
                "violations": [],
            }
        ),
        encoding="utf-8",
    )
    score = score_clone_attempt(ref, impl, completion_status="done")

    assert score["score"] == 1
    assert "sectionAePerMpxMean" not in score
    assert "visualFidelityOverall" not in score
    assert "runtimeTextOrderedSimilarity" not in score


@pytest.mark.parametrize(
    "bad_score",
    [True, float("nan"), float("inf"), -1, 11, 100],
    ids=["bool", "nan", "infinity", "negative", "eleven", "hundred"],
)
def test_visual_fidelity_nonfinite_or_out_of_range_scores_are_ignored(
    tmp_path: Path,
    bad_score: object,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    _write_visual_ref_input(ref)
    (ref / "visual-fidelity-judge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "overall": {"score": 10, "min": 10},
                "staticSections": [],
                "motion": {
                    "axes": {
                        "layout": bad_score,
                        "text": 10,
                        "color": 10,
                        "animation": 10,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    score = score_clone_attempt(ref, impl)

    assert "visualFidelityOverall" not in score


def test_valid_runtime_text_phase_variance_is_scored(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    _write_runtime_text_ref_input(ref)
    stable = [f"Stable copy {index}" for index in range(20)]

    ref_records = [
        _runtime_text_record(text, f"main>p:nth-of-type({index + 1})::run(1)")
        for index, text in enumerate(stable)
    ]
    variant = _runtime_text_record("Carousel variant", "main>h4::run(1)", "H4")
    impl_records = [*ref_records[:10], dict(variant), *ref_records[10:]]
    ref_samples = [
        impl_records,
        ref_records,
        ref_records,
        impl_records,
        ref_records,
    ]
    impl_samples = [impl_records, ref_records, ref_records, impl_records]
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(
            _runtime_text_artifact(
                ref_records,
                impl_records,
                ref_samples=ref_samples,
                impl_samples=impl_samples,
                lcs_length=20,
                phase_variance={
                    "accepted": True,
                    "advisory": "bounded rendered phase variance confirmed",
                    "gapCount": 1,
                    "proof": [
                        {
                            "gapIndex": 0,
                            "beforeSlot": "main>p:nth-of-type(10)::run(1)",
                            "afterSlot": "main>p:nth-of-type(11)::run(1)",
                            "beforeAnchor": {
                                "slot": "main>p:nth-of-type(10)::run(1)",
                                "text": "Stable copy 9",
                            },
                            "afterAnchor": {
                                "slot": "main>p:nth-of-type(11)::run(1)",
                                "text": "Stable copy 10",
                            },
                            "refBeforeAnchor": {
                                "slot": "main>p:nth-of-type(10)::run(1)",
                                "text": "Stable copy 9",
                            },
                            "refAfterAnchor": {
                                "slot": "main>p:nth-of-type(11)::run(1)",
                                "text": "Stable copy 10",
                            },
                            "implBeforeAnchor": {
                                "slot": "main>p:nth-of-type(10)::run(1)",
                                "text": "Stable copy 9",
                            },
                            "implAfterAnchor": {
                                "slot": "main>p:nth-of-type(11)::run(1)",
                                "text": "Stable copy 10",
                            },
                            "candidateSide": "impl",
                            "candidate": {
                                "slot": "main>h4::run(1)",
                                "text": "Carousel variant",
                                "tag": "H4",
                                "initialViewport": False,
                            },
                            "matchedReferenceCandidatePresentSample": 0,
                            "referenceCyclePolarity": "present-absent-present",
                            "matchedReferenceCandidateAbsentStartSample": 1,
                            "matchedReferenceCandidateRecurredSample": 3,
                            "referenceAbsenceRunLength": 2,
                            "referencePhaseSampleStartIndex": 0,
                            "referenceCandidate": {
                                "slot": "main>h4::run(1)",
                                "text": "Carousel variant",
                                "tag": "H4",
                                "initialViewport": False,
                            },
                            "matchedImplementationCandidateSample": 0,
                            "implementationCyclePolarity": "present-absent-present",
                            "matchedImplementationCandidateAbsentStartSample": 1,
                            "matchedImplementationCandidateRecurredSample": 3,
                            "implementationAbsenceRunLength": 2,
                            "implementationPhaseSampleStartIndex": 0,
                            "implementationCandidate": {
                                "slot": "main>h4::run(1)",
                                "text": "Carousel variant",
                                "tag": "H4",
                                "initialViewport": False,
                            },
                        }
                    ],
                    "referenceSampleCount": 5,
                    "implementationSampleCount": 4,
                },
            )
        ),
        encoding="utf-8",
    )
    _seal_runtime_text_artifact(ref)

    score = score_clone_attempt(ref, impl)

    assert score["runtimeTextStatus"] == "pass"
    assert score["runtimeTextOrderedSimilarity"] == round(40 / 41, 4)
    assert score["runtimeTextMissing"] == 0
    assert score["runtimeTextExtra"] == 1


def test_runtime_text_without_matching_dispatcher_provenance_scores_nothing(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    _write_runtime_text_ref_input(ref)
    records = [_runtime_text_record("Copy", "main>p::run(1)")]
    artifact = ref / "runtime-text-sequence.json"
    artifact.write_text(
        json.dumps(
            _runtime_text_artifact(
                records,
                records,
                ref_samples=[records, records],
                impl_samples=[records, records],
                phase_variance={"accepted": False, "reason": "exact-match"},
                lcs_length=1,
            )
        ),
        encoding="utf-8",
    )

    missing = score_clone_attempt(ref, impl)
    _seal_runtime_text_artifact(ref)
    proven = score_clone_attempt(ref, impl)
    provenance = json.loads(
        (ref / "runtime-text-sequence.provenance.json").read_text(encoding="utf-8")
    )
    provenance["artifactSha256"] = "0" * 64
    (ref / "runtime-text-sequence.provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    forged = score_clone_attempt(ref, impl)

    assert "runtimeTextStatus" not in missing
    assert proven["runtimeTextStatus"] == "pass"
    assert "runtimeTextStatus" not in forged


def test_runtime_text_empty_capture_error_awards_no_fidelity(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl(impl)
    _write_runtime_text_ref_input(ref)
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "error",
                "ref": {
                    "blockCount": 0,
                    "blocks": [],
                    "records": [],
                    "samples": [],
                },
                "impl": {
                    "blockCount": 0,
                    "blocks": [],
                    "records": [],
                    "samples": [],
                },
                "phaseVariance": {
                    "accepted": False,
                    "reason": "empty runtime capture",
                },
                "comparison": {
                    "lcsLength": 0,
                    "orderedSimilarity": 1,
                    "missingCount": 0,
                    "missingRatio": 0,
                    "extraCount": 0,
                    "missing": [],
                    "extra": [],
                },
                "violations": [{"kind": "empty-ref-capture"}],
            }
        ),
        encoding="utf-8",
    )

    score = score_clone_attempt(ref, impl)

    assert score["score"] == 0
    assert "runtimeTextStatus" not in score
    assert "runtimeTextOrderedSimilarity" not in score
