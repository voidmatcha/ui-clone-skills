from pathlib import Path

import pytest

from ui_clone.check_iteration import (
    _read_artifact,
    classify,
    record_attempt,
    select_rows,
    should_pause,
)


def test_read_artifact_recognizes_other_transitions_text_results(tmp_path: Path) -> None:
    # F4 (fable-20260910): previously ONLY the literal "transitions/result.txt"
    # was recognized as a text result; any other transitions/*-result.txt
    # (trajectory/hover-state/click-state/video-motion/temporal/keyframes-diff)
    # fell through to json.loads and silently became "missing" evidence.
    path = tmp_path / "transitions" / "trajectory-result.txt"
    path.parent.mkdir(parents=True)
    path.write_text("✅ all 4 sample points within ceiling")
    parsed = _read_artifact(path)
    assert parsed is not None
    assert parsed["status"] == "pass"
    assert "sample points" in parsed["text"]


def test_read_artifact_still_json_parses_unrelated_txt(tmp_path: Path) -> None:
    # A non-result.txt / non-sections-or-transitions text file is unaffected —
    # still goes through the generic JSON reader (not silently text-parsed).
    path = tmp_path / "transitions" / "notes.txt"
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert _read_artifact(path) is None


def test_arbitrary_timeout_env_var_moves_the_retry_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fable-20260910 follow-up (MEDIUM): the fingerprint used to hash only
    # RUN_REQUIRED_CHECK_TIMEOUT_SEC and RUN_REQUIRED_HEAVY_TIMEOUT_SEC.
    # Raising a DIFFERENT per-check timeout knob (e.g.
    # RUN_REQUIRED_HOVER_TIMEOUT_SEC) after two identical infra timeouts left
    # the fingerprint unchanged, so should_pause's guard could never be lifted
    # by that fix. Any *_TIMEOUT_SEC env var must now move the fingerprint.
    import json

    from ui_clone import check_iteration

    monkeypatch.setattr(check_iteration, "compute_check_input_hash", lambda *a: "stable-inputs")
    script = tmp_path / "compare.sh"
    script.write_text("#!/bin/bash\n")
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps({"status": "error"}))

    def _record(timeout_value: str) -> dict[str, object]:
        monkeypatch.setenv("RUN_REQUIRED_HOVER_TIMEOUT_SEC", timeout_value)
        check_iteration.main(
            [
                "record", str(tmp_path), "x", str(tmp_path), str(script),
                "https://ref.example", "http://localhost", str(artifact), "124", "1",
            ]
        )
        result: dict[str, object] = json.loads(check_iteration._receipt(tmp_path, "x").read_text())
        return result

    first = _record("60")
    assert first["attempts"] == 1
    second = _record("60")
    assert second["attempts"] == 2
    third = _record("3600")
    assert third["attempts"] == 1, "raising a per-check timeout must reset the pause"


def test_unavailable_declared_input_still_produces_a_pausing_fingerprint(tmp_path: Path) -> None:
    # fable-20260910 follow-up (LOW): a check whose declared input side has
    # zero matches (InputFingerprintUnavailable, e.g. css-mirror with no CSS
    # files yet) used to collapse to the same `fingerprint == ""` as an
    # UNREGISTERED check_id, and should_pause's `bool(fingerprint and ...)`
    # can never fire on an empty fingerprint — an unavailable-input check
    # would be redispatched forever with no path to a diagnostic pause.
    import json

    from ui_clone import check_iteration

    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (impl / "src" / "x.tsx").write_text("export const X = () => null;\n")
    script = tmp_path / "compare.sh"
    script.write_text("#!/bin/bash\n")
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps({"status": "error"}))

    for _ in range(3):
        check_iteration.main(
            [
                "record", str(ref), "css-mirror", str(impl), str(script),
                "https://ref.example", "http://localhost", str(artifact), "1", "1",
            ]
        )
    receipt = json.loads(check_iteration._receipt(ref, "css-mirror").read_text())
    assert receipt["inputHash"], "an unavailable declared input must not fingerprint as empty"
    assert check_iteration.should_pause(ref, "css-mirror", receipt["inputHash"])


def test_repeated_failure_pauses_and_change_resets(tmp_path: Path) -> None:
    artifact = {"status": "fail", "checks": [{"id": "layout", "status": "fail"}]}
    record_attempt(tmp_path, "layout", "a", artifact, 1, True)
    assert not should_pause(tmp_path, "layout", "a")
    record_attempt(tmp_path, "layout", "a", artifact, 1, True)
    assert should_pause(tmp_path, "layout", "a")
    assert not should_pause(tmp_path, "layout", "b")
    record_attempt(tmp_path, "layout", "a", {"status": "pass"}, 0, True)
    assert not should_pause(tmp_path, "layout", "a")


def test_changed_failure_is_progress(tmp_path: Path) -> None:
    record_attempt(tmp_path, "x", "a", {"status": "fail", "reason": "width"}, 1, True)
    record_attempt(tmp_path, "x", "a", {"status": "fail", "reason": "height"}, 1, True)
    assert not should_pause(tmp_path, "x", "a")


def test_classification() -> None:
    assert classify(None, 0, False) == "missing"
    assert classify({"status": "pass"}, 1, False) == "stale"
    assert classify(None, 124, False) == "infrastructure"
    assert (
        classify({"status": "error", "reason": "measurement unavailable"}, 1, True) == "measurement"
    )
    assert classify({"status": "fail"}, 1, True) == "implementation"


def test_selection_keeps_dependencies_and_marks_unselected() -> None:
    rows = [
        "RUN\ta\tx\targs\ta.json\tblock\t",
        "RUN\tb\tx\targs\tb.json\tblock\ta",
        "RUN\tc\tx\targs\tc.json\tblock\t",
    ]
    selected = select_rows(rows, {"b"}, None)
    assert selected[0].startswith("RUN\ta")
    assert selected[1].startswith("RUN\tb")
    assert selected[2].startswith("DEFERRED\tc")


def test_iteration_dispatch_leaves_other_artifacts_missing(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text("{}")
    (impl / "src/App.tsx").write_text("export default function App() {}")
    (ref / ".impl-root").write_text(str(impl))
    rows = []
    for cid in ("chosen", "other"):
        script = tmp_path / f"{cid}.sh"
        script.write_text('#!/bin/bash\nprintf \'{"status":"pass"}\' > "$1/' + cid + '.json"\n')
        rows.append(
            {
                "id": cid,
                "script": str(script),
                "argsRecipe": "{ref_dir}",
                "produces": cid + ".json",
                "severity": "block",
            }
        )
    (ref / "verification-plan.json").write_text(json.dumps({"requiredChecks": rows}))
    env = {
        **os.environ,
        "PLUGIN_ROOT": str(root),
        "PYTHON_BIN": sys.executable,
        "UI_CLONE_ITERATION_CHECKS": "chosen",
    }
    run = subprocess.run(
        [
            "bash",
            str(root / "scripts/verify/run-required-checks.sh"),
            "iteration-test",
            "https://example.test",
            "http://localhost:1",
            str(ref),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert (ref / "chosen.json").exists()
    assert not (ref / "other.json").exists()
    assert json.loads((ref / "iteration-receipt.json").read_text())["status"] == "partial"
    assert "ITERATION_CHECKS_FINISHED" in run.stdout
    env.pop("UI_CLONE_ITERATION_CHECKS")
    env["UI_CLONE_DISPATCH_DRY"] = "1"
    command = [
        "bash",
        str(root / "scripts/verify/run-required-checks.sh"),
        "iteration-test",
        "https://example.test",
        "http://localhost:1",
        str(ref),
    ]
    run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads((ref / "iteration-receipt.json").read_text())["status"] == "running"
    assert not (ref / "other.json").exists()
    env.pop("UI_CLONE_DISPATCH_DRY")
    run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads((ref / "iteration-receipt.json").read_text())["status"] == "completed"
    assert (ref / "other.json").exists()


def test_dispatcher_stops_identical_failure_and_resets_on_source_edit(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text("{}")
    source = impl / "src/App.tsx"
    source.write_text("export default function App() {}")
    (ref / ".impl-root").write_text(str(impl))
    script = tmp_path / "producer.sh"
    script.write_text(
        '#!/bin/bash\necho run >> "$1/calls"\nprintf \'{"status":"fail","reason":"mismatch"}\' > "$1/hydration.json"\nexit 1\n'
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "requiredChecks": [
                    {
                        "id": "hydration-check",
                        "script": str(script),
                        "argsRecipe": "{ref_dir}",
                        "produces": "hydration.json",
                        "severity": "block",
                    }
                ]
            }
        )
    )
    env = {**os.environ, "PLUGIN_ROOT": str(root), "PYTHON_BIN": sys.executable}
    env.pop("UI_CLONE_ITERATION_CHECKS", None)
    env.pop("UI_CLONE_CHANGED_FILES", None)
    command = [
        "bash",
        str(root / "scripts/verify/run-required-checks.sh"),
        "retry-test",
        "https://example.test",
        "http://localhost:1",
        str(ref),
    ]
    for _ in range(3):
        run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        assert run.returncode == 1, run.stdout + run.stderr
    assert len((ref / "calls").read_text().splitlines()) == 2
    assert "NO_PROGRESS" in run.stderr
    receipt = json.loads((ref / "iteration-receipt.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["failedChecks"] == ["hydration-check"]
    source.write_text("export default function App() { return null; }")
    run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 1, run.stdout + run.stderr
    assert len((ref / "calls").read_text().splitlines()) == 3


def test_interrupted_full_dispatch_records_terminal_receipt(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys
    import time

    root = Path(__file__).resolve().parents[1]
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text("{}")
    (impl / "src/App.tsx").write_text("export default function App() {}")
    (ref / ".impl-root").write_text(str(impl))
    script = tmp_path / "slow-check.sh"
    script.write_text(
        '#!/bin/bash\nsleep 2\nprintf \'{"status":"pass"}\' > "$1/slow.json"\n'
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "requiredChecks": [
                    {
                        "id": "independent-diagnostic",
                        "script": str(script),
                        "argsRecipe": "{ref_dir}",
                        "produces": "slow.json",
                        "severity": "block",
                    }
                ]
            }
        )
    )
    proc = subprocess.Popen(
        [
            "bash",
            str(root / "scripts/verify/run-required-checks.sh"),
            "interrupt-test",
            "https://example.test",
            "http://localhost:1",
            str(ref),
        ],
        env={**os.environ, "PLUGIN_ROOT": str(root), "PYTHON_BIN": sys.executable},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    receipt_path = ref / "iteration-receipt.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("status") == "running":
                break
        time.sleep(0.02)
    else:
        proc.kill()
        proc.communicate(timeout=5)
        pytest.fail("dispatcher did not create an active full receipt")

    proc.terminate()
    proc.communicate(timeout=8)
    receipt = json.loads(receipt_path.read_text())
    assert proc.returncode != 0
    assert receipt["mode"] == "final"
    assert receipt["status"] == "interrupted"


def test_css_change_uses_declared_inputs_and_keeps_unknown_checks() -> None:
    rows = [
        "RUN\thydration-check\tx\targs\ta.json\tblock\t",
        "RUN\tunknown-check\tx\targs\tb.json\tblock\t",
    ]
    selected = select_rows(rows, set(), ["src/App.css"])
    assert selected[0].startswith("DEFERRED")
    assert selected[1].startswith("RUN")


def test_corrupt_attempt_count_does_not_crash(tmp_path: Path) -> None:
    import json

    from ui_clone.check_iteration import _receipt

    path = _receipt(tmp_path, "x")
    path.parent.mkdir()
    path.write_text(json.dumps({"inputHash": "a", "attempts": "broken"}))
    assert not should_pause(tmp_path, "x", "a")
    record_attempt(tmp_path, "x", "a", {"status": "fail"}, 1, True)
    value = json.loads(path.read_text())
    value["attempts"] = []
    path.write_text(json.dumps(value))
    record_attempt(tmp_path, "x", "a", {"status": "fail"}, 1, True)
    assert json.loads(path.read_text())["attempts"] == 1


def test_full_dispatch_request_cannot_clear_partial_receipt(tmp_path: Path) -> None:
    import json

    from ui_clone.check_iteration import main

    dispatch = tmp_path / "dispatch"
    dispatch.write_text("")
    main(["select", str(tmp_path), str(dispatch)])
    assert json.loads((tmp_path / "iteration-receipt.json").read_text())["status"] == "running"
    main(["finish", str(tmp_path)])
    assert json.loads((tmp_path / "iteration-receipt.json").read_text())["status"] == "completed"


def test_full_dispatch_terminal_states_do_not_upgrade_partial_receipt(tmp_path: Path) -> None:
    import json

    from ui_clone.check_iteration import main

    dispatch = tmp_path / "dispatch"
    dispatch.write_text("RUN\tchosen\tx\targs\ta.json\tblock\t\n")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("UI_CLONE_ITERATION_CHECKS", "chosen")
        main(["select", str(tmp_path), str(dispatch)])
    main(["fail", str(tmp_path), "chosen"])
    receipt = json.loads((tmp_path / "iteration-receipt.json").read_text())
    assert receipt["mode"] == "iteration"
    assert receipt["status"] == "partial"

    with pytest.raises(ValueError, match="active full dispatch"):
        main(["finish", str(tmp_path)])

    (tmp_path / "iteration-receipt.json").write_text(
        json.dumps({"mode": "iteration", "status": "running"})
    )
    with pytest.raises(ValueError, match="Invalid partial"):
        main(["fail", str(tmp_path), "chosen"])


@pytest.mark.parametrize("command,status", [("setup-fail", "setup-failed"), ("interrupt", "interrupted")])
def test_full_dispatch_records_non_success_terminal_state(
    tmp_path: Path, command: str, status: str
) -> None:
    import json

    from ui_clone.check_iteration import main

    dispatch = tmp_path / "dispatch"
    dispatch.write_text("")
    main(["select", str(tmp_path), str(dispatch)])
    main([command, str(tmp_path)])
    assert json.loads((tmp_path / "iteration-receipt.json").read_text())["status"] == status


def test_terminal_update_rejects_stale_dispatch_receipt(tmp_path: Path) -> None:
    import json

    from ui_clone.check_iteration import main

    receipt = tmp_path / "iteration-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "mode": "final",
                "status": "running",
                "dispatchId": "older-run",
            }
        )
    )
    with pytest.raises(ValueError, match="different run"):
        main(["interrupt", str(tmp_path), "--dispatch-id", "new-run"])
    assert json.loads(receipt.read_text())["status"] == "running"


def _record_text_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    content: str | None,
    rc: int = 0,
    fresh: bool = True,
) -> dict[str, object]:
    import json

    from ui_clone import check_iteration

    monkeypatch.setattr(check_iteration, "compute_check_input_hash", lambda *args: "stable-inputs")
    script = tmp_path / "compare.sh"
    script.write_text("#!/bin/bash\n")
    artifact = tmp_path / relative
    artifact.parent.mkdir(exist_ok=True)
    if content is not None:
        artifact.write_text(content)
    check_iteration.main(
        [
            "record",
            str(tmp_path),
            relative,
            str(tmp_path),
            str(script),
            "https://reference.test",
            "http://localhost",
            str(artifact),
            str(rc),
            "1" if fresh else "0",
        ]
    )
    receipt: dict[str, object] = json.loads(
        check_iteration._receipt(tmp_path, relative).read_text()
    )
    return receipt


@pytest.mark.parametrize(
    "relative,text",
    [
        (
            "sections/result.txt",
            "| hero | 1440 | ✅ |\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**",
        ),
        ("transitions/result.txt", "| menu hover | ✅ |\nTransition compare: 1 PASS, 0 FAIL"),
        # F4 (fable-20260910): other transitions/*-result.txt formats share the
        # same ✅/❌ convention as transitions/result.txt but were previously
        # unmatched (exact-literal-name check) -> fell through to json.loads,
        # which raised on plain text and silently classified as "missing".
        ("transitions/trajectory-result.txt", "✅ all 4 sample points within ceiling"),
        ("transitions/hover-state-result.txt", "✅ .card clean [1280x800]"),
        ("transitions/click-state-result.txt", "✅ .cta clean [1280x800]"),
    ],
)
def test_text_pass_resets_retry_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, text: str
) -> None:
    first = _record_text_result(tmp_path, monkeypatch, relative, text)
    second = _record_text_result(tmp_path, monkeypatch, relative, text)
    assert first["category"] == second["category"] == "pass"
    assert second["attempts"] == 0
    assert second["canonical"] is False


@pytest.mark.parametrize(
    "relative,footer",
    [
        ("sections/result.txt", "**Result: 0 PASS, 1 FAIL, 0 SKIP**"),
        ("transitions/result.txt", "Transition compare: 0 PASS, 1 FAIL"),
        ("transitions/trajectory-result.txt", ""),
    ],
)
def test_changed_text_failure_is_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, footer: str
) -> None:
    first = _record_text_result(
        tmp_path, monkeypatch, relative, "| hero | ❌ AE=40% |\n" + footer, rc=1
    )
    second = _record_text_result(
        tmp_path, monkeypatch, relative, "| hero | ❌ AE=20% |\n" + footer, rc=1
    )
    assert second["category"] == "implementation"
    assert second["failureSignature"] != first["failureSignature"]
    assert second["attempts"] == 1
    third = _record_text_result(
        tmp_path, monkeypatch, relative, "| hero | ❌ AE=20% |\n" + footer, rc=1
    )
    assert third["status"] == "needs-diagnosis"


@pytest.mark.parametrize(
    "relative,text",
    [
        ("sections/result.txt", None),
        ("sections/result.txt", "garbage"),
        ("sections/result.txt", "**Result: 0 PASS, 0 FAIL, 4 SKIP, 2 STRUCTURAL_ONLY**"),
        ("transitions/result.txt", "garbage"),
        ("transitions/result.txt", "Transition compare: 1 PASS, 0 FAIL"),
        ("transitions/result.txt", "| menu | ❌ |\nTransition compare: 1 PASS, 0 FAIL"),
        ("transitions/trajectory-result.txt", None),
        ("transitions/trajectory-result.txt", "❌ 2/4 sample point(s) exceeded ceiling"),
    ],
)
def test_text_exit_zero_does_not_prove_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, text: str | None
) -> None:
    receipt = _record_text_result(tmp_path, monkeypatch, relative, text)
    assert receipt["category"] != "pass"
    assert receipt["attempts"] == 1


def test_section_unmeasured_is_measurement_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _record_text_result(
        tmp_path, monkeypatch, "sections/result.txt",
        "**Result: 8 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 4 UNMEASURED**",
        rc=1,
    )
    assert receipt["category"] == "measurement"


def test_section_multi_viewport_does_not_use_first_pass_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _record_text_result(
        tmp_path, monkeypatch, "sections/result.txt",
        "[1440x900] Result: 2 PASS, 0 FAIL\n[1024x768] Result: 1 PASS, 1 FAIL",
        rc=0,
    )
    assert receipt["category"] == "implementation"
