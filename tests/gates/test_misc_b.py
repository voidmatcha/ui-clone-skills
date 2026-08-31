import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from ._helpers import (
    _make_stub_compare,
    _project_root,
)


def _make_stub_hover_cleanup(plugin_root: Path, *, exit_code: int = 0) -> Path:
    cleanup = plugin_root / "scripts" / "verify" / "cleanup-sessions.sh"
    real_cleanup = _project_root() / "scripts" / "verify" / "cleanup-sessions.sh"
    cleanup.parent.mkdir(parents=True, exist_ok=True)
    cleanup.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$1" >> "${HOVER_CLEANUP_LOG:-/dev/null}"\n'
        f"bash {str(real_cleanup)!r} \"$1\" >/dev/null 2>&1 || true\n"
        f"exit {exit_code}\n"
    )
    cleanup.chmod(0o755)
    return cleanup


def test_hover_state_compare_single_viewport_back_compat(tmp_path: Path) -> None:
    """VIEWPORTS unset → no per-viewport subdir, no per-viewport line — current
    behavior preserved bit-for-bit so single-tier callers see no cost increase.

    Critical regression guard: the fan-out was an additive capability, NOT a
    coverage upgrade for existing callers. If unset-VIEWPORTS suddenly started
    fanning out to the four verification-plan default viewports, every
    standard-tier caller would 4× their browser cost overnight.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env["PLUGIN_ROOT"] = str(plugin_root)
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0
    assert "position-compare.sh" not in proc.stderr
    assert "dynamic_selectors_from_spec" not in proc.stderr
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "viewports: <single" in result
    # No per-viewport WxH subdir under hover-state/ — the target dir sits
    # directly under hover-state/<safe-name>/.
    assert (ref / "transitions" / "hover-state" / "btn").is_dir()
    assert not (ref / "transitions" / "hover-state" / "375x812").exists()


def test_hover_state_compare_empty_active_prefixes_are_bash32_safe(tmp_path: Path) -> None:
    """No active hover sessions must not trip set -u on macOS Bash 3.2."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env["PLUGIN_ROOT"] = str(_project_root())
    proc = subprocess.run(
        ["/bin/bash", str(script), "https://ref.example", "https://impl.example", "no-active", str(ref)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unbound variable" not in proc.stderr
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "no regions.json" in result


def test_hover_state_static_runtime_wrapper_rejects_malformed_stable_raf_counts() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    assert 'not isinstance(stable_count, int)' in body
    assert 'isinstance(stable_count, bool)' in body
    assert 'stable_count < 2' in body
    assert 'runtime_receipts_ok = runtime_receipt_ok(first_payload, first_cross_values, first_cross_rows, "first")' in body
    assert "expected_early = max(1, math.ceil(float(seconds) * float(fps)))" in body
    assert "runtime_row_count_drift_ok = (" in body
    assert "and runtime_receipts_ok" in body
    assert "and runtime_row_count_drift_ok" in body
    assert "abs(first_cross_rows - expected_rows) <= source_ratio" in body
    assert "abs(cross_rows - expected_rows) <= source_ratio" in body
    assert "abs(first_cross_rows - cross_rows) <= 2 * source_ratio" in body


def test_hover_state_static_runtime_wrapper_binds_selector_before_receipt_check() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    selector_bind = 'selector = action.split(":", 1)[1] if ":" in action else action'
    selector_guard = "if not selector:"
    selector_check = 'receipt.get("selector") == selector'

    assert selector_bind in body
    assert selector_guard in body
    assert body.index(selector_bind) < body.index("def static_validator_ok():")
    assert body.index("def static_validator_ok():") < body.index(selector_check)


def test_hover_state_static_runtime_wrapper_preserves_receipt_payload_for_metrics() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")
    static_body = body[body.index("def static_validator_ok():"):body.index("idle_states = ")]

    assert "source_payload = item.get(\"payload\")" in static_body
    assert not any(
        line.lstrip().startswith("payload = item.get(\"payload\")")
        for line in static_body.splitlines()
    )
    assert body.index("def static_validator_ok():") < body.index("payload.get(\"metrics\", {})")


def test_hover_state_static_runtime_wrapper_allows_bound_reference_self_receipt_key() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    assert 'payload.get("receipts") == {' not in body
    assert 'payload.get("receipts", {}).get("firstCaptureRetry") == {' in body
    assert 'payload.get("receipts", {}).get("retryCaptureRetry") == {' in body
    assert 'payload.get("receipts", {}).get("referenceSelf") == {' in body


def test_hover_state_static_runtime_wrapper_binds_target_payloads_to_action_selector() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    target_binding = 'or target_payload.get("selector") != selector'
    action_compare = 'action_payload.get("selector") != target_payload.get("selector")'

    assert "target_payloads_ok = " in body
    assert target_binding in body
    assert "target_payload.get(\"found\") is not True" in body
    assert "identity != target_identity" in body
    assert "rect != target_rect" not in body
    assert 'action_payload.get("rect") != target_payload.get("rect")' not in body
    assert "hover_rect_delta(" in body
    assert "hover_rect_deltas_match(" in body
    assert 'action_payload.get("transition") != target_payload.get("transition")' not in body
    assert "transition_contract_key(" in body
    assert 'if ancestor_delta[0] == ancestor_delta[1]:\n        return False' not in body
    assert 'if delta.intersection(declared):\n        return False' not in body
    assert 'if "all" in properties and delta.intersection(declared):' in body
    assert "target_payloads_ok" in body[body.index("def static_validator_ok():"):body.index("def runtime_receipt_ok")]
    assert body.index(target_binding) < body.index(action_compare)


def test_hover_state_static_runtime_wrapper_normalizes_synthetic_hover_helpers() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    assert "def normalize_ancestor_class_path(" in body
    assert 'token.startswith("h_")' in body
    assert "token[2:].isdigit()" in body
    assert 'if "#" in raw:' in body
    assert 'initial.get("ancestorClassPath")' in body
    assert "normalize_ancestor_class_path(initial.get(\"ancestorClassPath\"))" in body
    assert "normalize_ancestor_class_path(commit.get(\"ancestorClassPath\"))" in body
    assert "normalize_ancestor_class_path(mutation_snapshot.get(\"ancestorClassPath\"))" in body
    assert "normalize_ancestor_class_path(final.get(\"ancestorClassPath\"))" in body


def test_hover_state_static_runtime_wrapper_rejects_zero_effective_transition_duration() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    assert "effective_durations" in body
    assert "max(effective_durations) <= 0" in body


def test_hover_state_static_runtime_wrapper_defers_one_sided_arc_to_runtime_proof() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")
    static_body = body[body.index("def static_validator_ok():"):body.index("row_window_ok = ")]
    one_sided_guard = "if ref_duration <= 0 or impl_duration <= 0:"

    assert (
        f"{one_sided_guard}\n"
        "            arc_ok = False\n"
        "            break"
    ) in static_body
    assert f"{one_sided_guard}\n            return False" not in static_body


def test_hover_state_static_runtime_wrapper_allows_clean_reference_self_for_runtime_proof() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")
    static_body = body[body.index("def static_validator_ok():"):body.index("row_window_ok = ")]

    assert "if any(row > expected_rows for row in self_failure_rows):" in static_body
    assert "if not self_failure_rows or any(row > expected_rows for row in self_failure_rows):" not in static_body
    assert (
        "if self_failure_rows:\n"
        "        if not first_self_bins or not retry_self_bins:\n"
        "            return False"
    ) in static_body
    assert "self_clean_or_failures_inside_window" in static_body


def test_hover_state_runtime_wrapper_uses_mode_specific_css_transition_proof() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")

    assert "def runtime_proof_ok(state_change_mode):" in body
    assert 'if state_change_mode == "static-discrete":' in body
    assert 'elif state_change_mode == "declared-transition":' in body
    assert 'state_change_mode = "declared-transition" if changed_declared else "static-discrete"' in body
    assert "max_active_animation_count < 1" in body
    assert "def declared_transition_duration_ms(transition, changed_keys, idle_style, hover_style):" in body
    assert "changed_keys.intersection(mapped)" in body
    assert '"background": ("backgroundColor",),' in body
    assert '"border": ("borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor"),' in body


def test_hover_state_static_runtime_wrapper_requires_targets_for_target_bearing_rules() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    body = script.read_text(encoding="utf-8")
    guard = (
        'payload.get("rule")\n'
        "        not in {\n"
        '            "mixed-early-window-and-arc-only-capture-phase",\n'
        '            "static-discrete-hover-state-source-bin-proof",\n'
        "        }\n"
        "        or target_payloads_ok"
    )

    assert guard in body
    assert body.index("or target_payloads_ok") < body.index('payload.get("targets") == target_block')


def test_hover_state_compare_exits_nonzero_on_measured_divergence(tmp_path: Path) -> None:
    """A diverging MEASURED hover run must fail the gate (exit 1).

    hover-state-compare exited 1 only on FALLBACK_FAILED; a measured run with
    FAIL_COUNT>0 wrote '❌ N/M diverged' but still exit 0, so enforcement rested
    solely on the rollup's fragile text regex — the same class of exit-code lie
    already fixed in video-motion-compare and click-state-compare.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    invocations = tmp_path / "hover-stub-invocations"
    # Diverging inner compare: exit 1 => FAIL_COUNT>0 on the measured run.
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        '#!/usr/bin/env bash\n'
        'if [ -s "$HOVER_STUB_INVOCATIONS" ]; then\n'
        '  printf "%s\\n" "$1" >> "$HOVER_STUB_INVOCATIONS"\n'
        "  exit 0\n"
        "fi\n"
        'printf "%s\\n" "$1" >> "$HOVER_STUB_INVOCATIONS"\n'
        "echo '[stub] divergence'\nexit 1\n"
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env["PLUGIN_ROOT"] = str(plugin_root)
    env["HOVER_STUB_INVOCATIONS"] = str(invocations)
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 1, (
        f"diverging measured hover run must exit 1, got {proc.returncode}: {proc.stdout}"
    )
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "diverged" in result
    assert invocations.read_text().splitlines() == ["test-session-hs-1"]
    assert "no capture retry allowed" in result


def test_hover_state_compare_confirms_capture_flake_with_one_retry(tmp_path: Path) -> None:
    """A one-off measured capture failure is confirmed once, then accepted.

    Both attempts must remain inspectable, and the retry must use a fresh
    browser-session name instead of reusing potentially poisoned state.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    state = tmp_path / "hover-stub-state"
    invocations = tmp_path / "hover-stub-invocations"
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "count=0\n"
        '[ -f "$HOVER_STUB_STATE" ] && count="$(cat "$HOVER_STUB_STATE")"\n'
        'count=$((count + 1)); printf "%s\\n" "$count" > "$HOVER_STUB_STATE"\n'
        'mkdir -p "$4"; touch "$4/attempt-$count.marker"\n'
        'printf "%s\\t%s\\n" "$1" "$4" >> "$HOVER_STUB_INVOCATIONS"\n'
        '[ "$count" -eq 1 ] && exit 2\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "HOVER_STUB_STATE": str(state),
        "HOVER_STUB_INVOCATIONS": str(invocations),
    })
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "pass-after-retry" in result
    assert "capture-flake-confirmed" in result
    assert "failed=0" in result
    target = ref / "transitions" / "hover-state" / "btn"
    retry = ref / "transitions" / "hover-state" / "btn-retry-1"
    assert (target / "attempt-1.marker").is_file()
    assert (retry / "attempt-2.marker").is_file()
    calls = [line.split("\t") for line in invocations.read_text().splitlines()]
    assert calls == [
        ["test-session-hs-1", str(target)],
        ["test-session-hs-1-retry1", str(retry)],
    ]


def test_hover_state_compare_stays_unmeasurable_after_one_retry(tmp_path: Path) -> None:
    """A retryable capture that fails measurement twice exits unmeasurable."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    invocations = tmp_path / "hover-stub-invocations"
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'mkdir -p "$4"; touch "$4/failed.marker"\n'
        'printf "%s\\t%s\\n" "$1" "$4" >> "$HOVER_STUB_INVOCATIONS"\n'
        "exit 2\n"
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "HOVER_STUB_INVOCATIONS": str(invocations),
    })
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "unmeasurable-after-retry" in result
    assert "failed=0" in result
    assert "unmeasurable=1" in result
    assert "fallback probe skipped" in result
    target = ref / "transitions" / "hover-state" / "btn"
    retry = ref / "transitions" / "hover-state" / "btn-retry-1"
    assert (target / "failed.marker").is_file()
    assert (retry / "failed.marker").is_file()
    calls = [line.split("\t") for line in invocations.read_text().splitlines()]
    assert calls == [
        ["test-session-hs-1", str(target)],
        ["test-session-hs-1-retry1", str(retry)],
    ]


@pytest.mark.parametrize(
    "calibration_mode",
    [
        "valid",
        "invalid-status",
        "tampered-series",
        "tampered-first-series",
        "tampered-first-receipt",
        "tampered-threshold",
        "tampered-attempt",
        "tampered-action",
        "clean-self-divergence",
    ],
)
def test_hover_state_compare_requires_valid_reference_self_calibration_after_two_early_window_retries(
    tmp_path: Path,
    calibration_mode: str,
) -> None:
    """Two early-window exit-2 attempts never pass from calibration alone."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    invocations = tmp_path / "hover-stub-invocations"
    cleanup_log = tmp_path / "hover-cleanup-invocations"
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'mkdir -p "$4/ref-frames" "$4/ref-delta-frames" "$4/diff-frames"\n'
        'printf "2\\n" > "$4/ref-frames/.first-change"\n'
        'for index in 1 2 3 4; do touch "$(printf "$4/ref-delta-frames/f-%06d.png" "$index")"; done\n'
        'printf "0.81\\n0.95\\n0.96\\n" > "$4/diff-frames/target-raw-ssim.txt"\n'
        'cat > "$4/capture-retry.json" <<EOF\n'
        '{"schemaVersion":1,"status":"retryable-unmeasurable",'
        '"reason":"early-window-capture-phase","selector":".btn",'
        '"threshold":0.9,"rows":3,"failures":1,"failureRows":[1],'
        '"firstStablePassingRow":2,"lastFailureRow":1,'
        '"earlyWindowRows":1,"extractedFps":60,"minSsim":0.81,'
        '"arc":{"ref":{"firstChange":1,"lastChange":10,"durationFrames":9},'
        '"impl":{"firstChange":1,"lastChange":11,"durationFrames":10},'
        '"deltaFrames":1,"maxDeltaFrames":18,"withinTolerance":true}}\n'
        "EOF\n"
        'printf "%s\\t%s\\n" "$1" "$4" >> "$HOVER_STUB_INVOCATIONS"\n'
        "exit 2\n"
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)
    verify_lib = plugin_root / "scripts" / "verify" / "lib"
    verify_lib.mkdir(parents=True, exist_ok=True)
    (verify_lib / "position-compare.sh").write_text(
        "dynamic_selectors_from_spec() { :; }\n",
        encoding="utf-8",
    )
    shutil.copy2(
        _project_root() / "scripts" / "verify" / "lib" / "frame-align.sh",
        verify_lib / "frame-align.sh",
    )

    calibrator = tmp_path / "reference-self-calibrator"
    real_calibrator = (
        _project_root()
        / "scripts"
        / "verify"
        / "lib"
        / "reference_self_calibration.py"
    )
    calibrator.write_text(
        "#!/usr/bin/env bash\n"
        f"python3 {str(real_calibrator)!r} \"$@\" || exit $?\n"
        + (
            ""
            if calibration_mode in {"valid", "clean-self-divergence"}
            else
            "out=''; first_cross=''; cross=''; first_receipt=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '--out' ]; then out=\"$2\"; break; fi\n"
            "  if [ \"$1\" = '--first-cross-series' ]; then first_cross=\"$2\"; shift 2; continue; fi\n"
            "  if [ \"$1\" = '--retry-cross-series' ]; then cross=\"$2\"; shift 2; continue; fi\n"
            "  if [ \"$1\" = '--first-capture-retry' ]; then first_receipt=\"$2\"; shift 2; continue; fi\n"
            "  shift\n"
            "done\n"
            f"python3 - \"$out\" \"$cross\" \"$first_cross\" \"$first_receipt\" {calibration_mode!r} <<'PY'\n"
            "import json, sys\n"
            "path, cross, first_cross, first_receipt, mode = sys.argv[1:]\n"
            "if mode == 'tampered-series':\n"
            "    open(cross, 'a', encoding='utf-8').write('1.0\\n')\n"
            "elif mode == 'tampered-first-series':\n"
            "    open(first_cross, 'a', encoding='utf-8').write('1.0\\n')\n"
            "elif mode == 'tampered-first-receipt':\n"
            "    receipt = json.load(open(first_receipt, encoding='utf-8'))\n"
            "    receipt['failureRows'] = [1, 2]\n"
            "    open(first_receipt, 'w', encoding='utf-8').write(json.dumps(receipt) + '\\n')\n"
            "else:\n"
            "    payload = json.load(open(path, encoding='utf-8'))\n"
            "    if mode == 'invalid-status': payload['status'] = 'reference-self-calibration-failed'\n"
            "    if mode == 'tampered-threshold': payload['threshold'] = 0.91\n"
            "    if mode == 'tampered-attempt': payload['attempts']['retry']['id'] = 'other-attempt'\n"
            "    if mode == 'tampered-action': payload['action'] = 'hover:.other'\n"
            "    open(path, 'w', encoding='utf-8').write(json.dumps(payload) + '\\n')\n"
            "PY\n"
        )
    )
    calibrator.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ffmpeg = fake_bin / "ffmpeg"
    fake_counter = tmp_path / "fake-ffmpeg-counter"
    fake_self_values = (
        "1.000000 1.000000 1.000000"
        if calibration_mode == "clean-self-divergence"
        else "0.800000 0.950000 0.960000"
    )
    fake_ffmpeg.write_text(
        "#!/usr/bin/env bash\n"
        f"counter={str(fake_counter)!r}\n"
        'count=$(cat "$counter" 2>/dev/null || echo 0)\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" > "$counter"\n'
        f"values=({fake_self_values})\n"
        'index=$((count - 1))\n'
        'if [ "$index" -ge "${#values[@]}" ]; then index=$((${#values[@]} - 1)); fi\n'
        'value="${values[$index]}"\n'
        'printf "%s\\n" "SSIM Y:$value All:$value (0.0)" >&2\n',
        encoding="utf-8",
    )
    fake_ffmpeg.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "HOVER_STUB_INVOCATIONS": str(invocations),
        "HOVER_REFERENCE_SELF_CALIBRATOR": str(calibrator),
        "HOVER_CLEANUP_LOG": str(cleanup_log),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "UI_CLONE_VMC_JITTER_FRAMES": "0",
    })
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    if calibration_mode == "valid":
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "pass-after-reference-self-calibration" in result
        assert "unmeasurable-after-retry" not in result
        assert "unmeasurable=0" in result
    elif calibration_mode == "clean-self-divergence":
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "divergence-after-clean-reference-self" in result
        assert "failed=1" in result
        assert "unmeasurable=0" in result
    else:
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "unmeasurable-after-retry" in result
        assert "unmeasurable=1" in result
    assert invocations.read_text().splitlines() == [
        f"test-session-hs-1\t{ref / 'transitions' / 'hover-state' / 'btn'}",
        f"test-session-hs-1-retry1\t{ref / 'transitions' / 'hover-state' / 'btn-retry-1'}",
    ]
    cleanup_calls = cleanup_log.read_text().splitlines()
    assert "test-session-hs-1" in cleanup_calls
    assert "test-session-hs-1-retry1" in cleanup_calls


@pytest.mark.parametrize("missing_required_frame", [False, True])
def test_hover_state_reference_self_calibration_requires_contiguous_aligned_frames(
    tmp_path: Path,
    missing_required_frame: bool,
) -> None:
    """Timing markers come from raw frames and required delta inputs are contiguous."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    verify_lib = plugin_root / "scripts" / "verify" / "lib"
    verify_lib.mkdir(parents=True)
    (verify_lib / "position-compare.sh").write_text(
        "dynamic_selectors_from_spec() { :; }\n",
        encoding="utf-8",
    )
    shutil.copy2(
        _project_root() / "scripts" / "verify" / "lib" / "frame-align.sh",
        verify_lib / "frame-align.sh",
    )
    shutil.copy2(
        _project_root()
        / "scripts"
        / "verify"
        / "lib"
        / "reference_self_calibration.py",
        verify_lib / "reference_self_calibration.py",
    )
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    frame_indexes = "1 2 4" if missing_required_frame else "1 2 3 4"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'mkdir -p "$4/ref-frames" "$4/ref-delta-frames" "$4/diff-frames"\n'
        'printf "2\\n" > "$4/ref-frames/.first-change"\n'
        f'for index in {frame_indexes}; do touch "$(printf "$4/ref-delta-frames/f-%06d.png" "$index")"; done\n'
        'printf "0.81\\n0.95\\n0.96\\n" > "$4/diff-frames/target-raw-ssim.txt"\n'
        'cat > "$4/capture-retry.json" <<EOF\n'
        '{"schemaVersion":1,"status":"retryable-unmeasurable",'
        '"reason":"early-window-capture-phase","selector":".btn",'
        '"threshold":0.9,"rows":3,"failures":1,"failureRows":[1],'
        '"firstStablePassingRow":2,"lastFailureRow":1,'
        '"earlyWindowRows":1,"extractedFps":60,"minSsim":0.81,'
        '"arc":{"ref":{"firstChange":1,"lastChange":10,"durationFrames":9},'
        '"impl":{"firstChange":1,"lastChange":11,"durationFrames":10},'
        '"deltaFrames":1,"maxDeltaFrames":18,"withinTolerance":true}}\n'
        "EOF\n"
        "exit 2\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ffmpeg = fake_bin / "ffmpeg"
    fake_counter = tmp_path / "fake-ffmpeg-counter"
    fake_ffmpeg.write_text(
        "#!/usr/bin/env bash\n"
        f"counter={str(fake_counter)!r}\n"
        'count=$(cat "$counter" 2>/dev/null || echo 0)\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" > "$counter"\n'
        "values=(0.800000 0.950000 0.960000)\n"
        'index=$((count - 1))\n'
        'if [ "$index" -ge "${#values[@]}" ]; then index=$((${#values[@]} - 1)); fi\n'
        'value="${values[$index]}"\n'
        'printf "%s\\n" "SSIM Y:$value All:$value (0.0)" >&2\n',
        encoding="utf-8",
    )
    fake_ffmpeg.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SSIM_THRESHOLD": "0.90",
        "UI_CLONE_VMC_JITTER_FRAMES": "0",
    })
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    retry = ref / "transitions" / "hover-state" / "btn-retry-1"
    assert not (retry / "ref-delta-frames" / ".first-change").exists()
    if missing_required_frame:
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert not (retry / "reference-self-calibration.json").exists()
        result = (ref / "transitions" / "hover-state-result.txt").read_text()
        assert "unmeasurable-after-retry" in result
        return

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "pass-after-reference-self-calibration" in result
    assert "unmeasurable-after-retry" not in result
    receipt = json.loads(
        (retry / "reference-self-calibration.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "pass-after-reference-self-calibration"
    assert receipt["expectedRows"] == 3
    assert receipt["series"]["referenceSelf"]["rows"] == 3
    assert receipt["series"]["retryCross"]["rows"] == 3
    self_series = retry / "diff-frames" / "reference-self-ssim.txt"
    first_cross_series = ref / "transitions" / "hover-state" / "btn" / "diff-frames" / "target-raw-ssim.txt"
    cross_series = retry / "diff-frames" / "target-raw-ssim.txt"
    assert receipt["series"]["referenceSelf"]["sha256"] == hashlib.sha256(
        self_series.read_bytes()
    ).hexdigest()
    assert receipt["series"]["firstCross"]["sha256"] == hashlib.sha256(
        first_cross_series.read_bytes()
    ).hexdigest()
    assert receipt["series"]["retryCross"]["sha256"] == hashlib.sha256(
        cross_series.read_bytes()
    ).hexdigest()
    assert receipt["attempts"] == {
        "first": {"id": "btn", "offset": 1},
        "retry": {"id": "btn-retry-1", "offset": 1},
    }
    assert receipt["action"] == "hover:.btn"


@pytest.mark.parametrize(
    ("calibration_mode", "trigger_type"),
    [
        ("valid", "css-hover"),
        ("valid", "hover"),
        ("valid", "synth-hover-candidate"),
        ("tampered-first-series", "css-hover"),
        ("tampered-retry-receipt", "css-hover"),
        ("tampered-target", "css-hover"),
        ("selector-mismatch", "css-hover"),
    ],
)
def test_hover_state_compare_accepts_bound_complementary_calibration(
    tmp_path: Path,
    calibration_mode: str,
    trigger_type: str,
) -> None:
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({
            "hover": [{"name": "btn", "triggerType": trigger_type, "selector": ".btn"}]
        })
    )
    plugin_root = tmp_path / "fake-plugin-root"
    verify_lib = plugin_root / "scripts" / "verify" / "lib"
    verify_lib.mkdir(parents=True, exist_ok=True)
    (verify_lib / "position-compare.sh").write_text(
        "dynamic_selectors_from_spec() { :; }\n",
        encoding="utf-8",
    )
    shutil.copy2(
        _project_root() / "scripts" / "verify" / "lib" / "frame-align.sh",
        verify_lib / "frame-align.sh",
    )
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'target_selector="${HOVER_STUB_TARGET_SELECTOR:-.btn}"\n'
        'mkdir -p "$4/ref-frames" "$4/ref-delta-frames" "$4/diff-frames" "$4/ref-video" "$4/impl-video"\n'
        'for side in ref impl; do cat > "$4/${side}-video/target-rect.raw.json" <<EOF\n'
        '{"found":true,"selector":"'"$target_selector"'","matchIndex":0,"matchCount":1,'
        '"rect":{"x":80,"y":60,"width":160,"height":120},'
        '"transition":{"property":"background-color,border-color",'
        '"duration":"0.2,0.2","delay":"0,0",'
        '"timingFunction":"cubic-bezier(0.33, 1, 0.68, 1), cubic-bezier(0.33, 1, 0.68, 1)"}}\n'
        "EOF\n"
        "done\n"
        'printf "1\\n" > "$4/ref-frames/.first-change"\n'
        'for index in $(seq 1 18); do touch "$(printf "$4/ref-delta-frames/f-%06d.png" "$index")"; done\n'
        'if [[ "$1" == *retry1 ]]; then\n'
        '  yes 0.961 | head -18 > "$4/diff-frames/target-raw-ssim.txt"\n'
        '  cat > "$4/capture-retry.json" <<EOF\n'
        '{"schemaVersion":1,"status":"retryable-unmeasurable",'
        '"reason":"arc-only-capture-jitter","selector":".btn",'
        '"threshold":0.9,"rows":18,"failures":0,"failureRows":[],'
        '"firstStablePassingRow":1,"lastFailureRow":0,"minSsim":0.961,'
        '"arc":{"ref":{"firstChange":1,"lastChange":37,"durationFrames":36},'
        '"impl":{"firstChange":1,"lastChange":13,"durationFrames":12},'
        '"deltaFrames":24,"maxDeltaFrames":18,"withinTolerance":false}}\n'
        "EOF\n"
        "else\n"
        '  { yes 0.81 | head -5; yes 0.97 | head -13; } > "$4/diff-frames/target-raw-ssim.txt"\n'
        '  cat > "$4/capture-retry.json" <<EOF\n'
        '{"schemaVersion":1,"status":"retryable-unmeasurable",'
        '"reason":"early-window-capture-phase","selector":".btn",'
        '"threshold":0.9,"rows":18,"failures":5,"failureRows":[1,2,3,4,5],'
        '"firstStablePassingRow":6,"lastFailureRow":5,'
        '"earlyWindowRows":5,"extractedFps":60,"minSsim":0.81,'
        '"arc":{"ref":{"firstChange":1,"lastChange":7,"durationFrames":6},'
        '"impl":{"firstChange":1,"lastChange":13,"durationFrames":12},'
        '"deltaFrames":6,"maxDeltaFrames":18,"withinTolerance":true}}\n'
        "EOF\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)
    calibrator = tmp_path / "reference-self-calibrator"
    real_calibrator = (
        _project_root()
        / "scripts"
        / "verify"
        / "lib"
        / "reference_self_calibration.py"
    )
    calibrator.write_text(
        "#!/usr/bin/env bash\n"
        f"python3 {str(real_calibrator)!r} \"$@\" || exit $?\n"
        "first_cross=''; retry_receipt=''; first_ref_target=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = '--first-cross-series' ]; then first_cross=\"$2\"; shift 2; continue; fi\n"
        "  if [ \"$1\" = '--retry-capture-retry' ]; then retry_receipt=\"$2\"; shift 2; continue; fi\n"
        "  if [ \"$1\" = '--first-ref-target' ]; then first_ref_target=\"$2\"; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        f"python3 - \"$first_cross\" \"$retry_receipt\" \"$first_ref_target\" {calibration_mode!r} <<'PY'\n"
        "import json, sys\n"
        "first_cross, retry_receipt, first_ref_target, mode = sys.argv[1:]\n"
        "if mode == 'tampered-first-series':\n"
        "    open(first_cross, 'a', encoding='utf-8').write('1.0\\n')\n"
        "elif mode == 'tampered-retry-receipt':\n"
        "    payload = json.load(open(retry_receipt, encoding='utf-8'))\n"
        "    payload['minSsim'] = 0.90\n"
        "    open(retry_receipt, 'w', encoding='utf-8').write(json.dumps(payload) + '\\n')\n"
        "elif mode == 'tampered-target':\n"
        "    payload = json.load(open(first_ref_target, encoding='utf-8'))\n"
        "    payload['matchIndex'] = 1\n"
        "    open(first_ref_target, 'w', encoding='utf-8').write(json.dumps(payload) + '\\n')\n"
        "PY\n",
        encoding="utf-8",
    )
    calibrator.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ffmpeg = fake_bin / "ffmpeg"
    fake_counter = tmp_path / "fake-ffmpeg-counter"
    fake_ffmpeg.write_text(
        "#!/usr/bin/env bash\n"
        f"counter={str(fake_counter)!r}\n"
        'count=$(cat "$counter" 2>/dev/null || echo 0)\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" > "$counter"\n'
        'if [ "$count" -le 5 ]; then value=0.800000; else value=0.960000; fi\n'
        'printf "%s\\n" "SSIM Y:$value All:$value (0.0)" >&2\n',
        encoding="utf-8",
    )
    fake_ffmpeg.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "HOVER_REFERENCE_SELF_CALIBRATOR": str(calibrator),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "UI_CLONE_VMC_JITTER_FRAMES": "0",
        "HOVER_STUB_TARGET_SELECTOR": (
            ".other" if calibration_mode == "selector-mismatch" else ".btn"
        ),
    })
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    result = (ref / "transitions" / "hover-state-result.txt").read_text()

    if calibration_mode == "valid" and trigger_type == "css-hover":
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "pass-after-complementary-reference-self-calibration" in result
        receipt = json.loads(
            (
                ref
                / "transitions"
                / "hover-state"
                / "btn-retry-1"
                / "reference-self-complementary-calibration.json"
            ).read_text(encoding="utf-8")
        )
        assert receipt["metrics"]["earlySide"] == "first"
        assert receipt["metrics"]["arcOnlySide"] == "retry"
        assert receipt["metrics"]["provenanceValid"] is True
        assert receipt["provenance"] == {
            "triggerType": "css-hover",
            "provenance": "css-hover",
        }
        return

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "pass-after-complementary-reference-self-calibration" not in result
    assert "unmeasurable-after-retry" in result


def test_hover_state_compare_cleanup_failure_stops_before_retry(tmp_path: Path) -> None:
    """A failed cleanup makes the run unmeasurable before another capture."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    invocations = tmp_path / "hover-stub-invocations"
    cleanup_log = tmp_path / "hover-cleanup-invocations"
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$1" >> "$HOVER_STUB_INVOCATIONS"\nexit 2\n'
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root, exit_code=9)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "HOVER_STUB_INVOCATIONS": str(invocations),
        "HOVER_CLEANUP_LOG": str(cleanup_log),
    })
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert not invocations.exists(), "presence cleanup failure must stop before the first compare"
    assert cleanup_log.read_text().splitlines() == ["test-session-hsprobe"] * 6
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "presence-probe session cleanup failed" in result


def test_hover_state_compare_term_preserves_status_and_cleans_only_active_owned_session(
    tmp_path: Path,
) -> None:
    """TERM exits 143 and closes the active owned measure prefix without broad cleanup."""
    import signal
    import subprocess
    import time

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    cleanup_log = tmp_path / "hover-cleanup-invocations"
    started = tmp_path / "compare-started"
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'touch "$HOVER_COMPARE_STARTED"\n'
        "trap 'exit 143' TERM\n"
        "while :; do /bin/sleep 1; done\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    _make_stub_hover_cleanup(plugin_root)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_agent_browser = fake_bin / "agent-browser"
    fake_agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        "case \" $* \" in\n"
        "  *' eval '*) printf '%s\\n' '{\".btn\":true}' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_agent_browser.chmod(0o755)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)
    temp_area = tmp_path / "tmp"
    temp_area.mkdir()

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env.update({
        "PLUGIN_ROOT": str(plugin_root),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(temp_area),
        "HOVER_CLEANUP_LOG": str(cleanup_log),
        "HOVER_COMPARE_STARTED": str(started),
    })
    proc = subprocess.Popen(
        ["bash", str(script), "https://ref.example", "https://impl.example", "owned", str(ref)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not started.exists():
        time.sleep(0.05)
    assert started.exists(), "hover compare did not reach the active measure session"
    os.killpg(proc.pid, signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=10)

    assert proc.returncode == 143, stdout + stderr
    cleanup_calls = cleanup_log.read_text().splitlines()
    assert "owned-hsprobe" in cleanup_calls
    assert "owned-hs-1" in cleanup_calls
    assert all(call.startswith("owned-") for call in cleanup_calls)
    assert not any("unrelated" in call for call in cleanup_calls)
    assert list(temp_area.iterdir()) == []


def test_hover_state_compare_settles_owned_sessions_between_captures() -> None:
    """The presence probe and each 60fps target create separate browser families.

    Closing is asynchronous, so starting the next recording immediately can
    lose the hover-in arc or contaminate its first frames. The wrapper must use
    list-first cleanup after the probe, every measured target, and fallback.
    """
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "hover-state-compare.sh"
    )
    body = script.read_text(encoding="utf-8")

    assert 'CLEANUP_SESSIONS="$PROJECT_ROOT/scripts/verify/cleanup-sessions.sh"' in body
    assert 'cleanup_hover_sessions "$PROBE_SESSION"' in body
    assert 'cleanup_hover_sessions "$MEASURE_SESSION"' in body
    assert 'cleanup_hover_sessions "$RETRY_SESSION"' in body
    assert 'cleanup_hover_sessions "${SESSION}-hfb"' in body
    compare_at = body.index('bash "$COMPARE" "$MEASURE_SESSION"')
    cleanup_at = body.index('cleanup_hover_sessions "$MEASURE_SESSION"')
    verdict_at = body.index('case "$MEASURE_STATUS" in')
    assert compare_at < cleanup_at < verdict_at


def test_hover_state_compare_fails_when_hasHover_but_no_regions(tmp_path: Path) -> None:
    """signals.hasHover=true + full-page-only regions.json (no triggerType) → FAIL.

    Loop-claude-144 realfood regression: verification-plan scheduled the row
    (severity:block, reason "signals.hasHover=true"), 12 :hover CSS rules exist,
    yet the Lenis regions.json producer emits one full-page region with zero
    triggerType fields. The old script self-certified PASS (exit 0, ✅) because
    the triggerType jq matched nothing — shipping hover motion UNVERIFIED while
    the gate showed green. With the scheduling-signal cross-check the empty
    target list must now be a blocking FAIL.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": 20133}]
    }))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "signals": {"hasHover": True},
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root)}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "❌" in result
    assert "UNVERIFIED" in result


def test_hover_state_compare_synthesizes_from_hover_css_rules(tmp_path: Path) -> None:
    """Empty regions.json + hover-css-rules.json → targets synthesized from CSS.

    The compound CSS selector `.dga_pdf_card__RKAwD:hover .dga_pdf_card_tooltip__azeG1`
    must reduce to the hoverable base `.dga_pdf_card__RKAwD` (split on first comma,
    then first colon). With a stub inner that exits 0 the run passes (exit 0), the
    result records the synth source, and a target dir mangled from the base selector
    exists. Confirms synthesis ran instead of the old silent skip.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": 20133}]
    }))
    (ref / "hover-css-rules.json").write_text(json.dumps([
        {
            "selector": ".dga_pdf_card__RKAwD:hover .dga_pdf_card_tooltip__azeG1",
            "css": "opacity: 1; transform: translateY(-50%) translateX(0px);",
            "media": "(min-width: 901px)",
        }
    ]))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root)}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "synth-hover-css" in result
    # Base selector ".dga_pdf_card__RKAwD" → SAFE_NAME mangles non-word chars to "_".
    assert (ref / "transitions" / "hover-state" / "_dga_pdf_card__RKAwD").is_dir()


def test_hover_state_compare_synthesizes_from_candidates(tmp_path: Path) -> None:
    """Empty regions.json + hover-candidates.json → targets synthesized from candidates.

    realfood loop-omx-36 shape: [{selector, source, text, transition}] (no rect).
    `.text` names the target ("Real Food"); synth-hover-candidate is recorded and a
    target dir mangled from the text exists.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": 20133}]
    }))
    (ref / "hover-candidates.json").write_text(json.dumps([
        {
            "selector": "button.nav_dot_button__kZB4V",
            "source": "css-transition",
            "text": "Real Food",
            "transition": "all",
        }
    ]))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root)}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "synth-hover-candidate" in result
    # "Real Food" → SAFE_NAME mangles the space → "Real_Food".
    assert (ref / "transitions" / "hover-state" / "Real_Food").is_dir()


def test_hover_state_compare_passes_when_no_hover_signal_anywhere(tmp_path: Path) -> None:
    """Empty regions.json + NO plan/hover artifacts → legitimate skip survives.

    Back-compat: without a scheduling signal and without recoverable hover
    targets, the gate must keep the ✅ exit-0 skip path (no false FAIL).
    Run through the system Bash so macOS 3.2 also exercises the empty-array
    EXIT cleanup path under ``set -u``.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": 20133}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root)}
    proc = subprocess.run(
        ["/bin/bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "✅" in result


def test_click_state_compare_fans_out_per_viewport(tmp_path: Path) -> None:
    """VIEWPORTS=\"375x812,1280x800\" → per-viewport subdirs + result.txt sections.

    Click-state's responsive divergence is the killer case: modals render as
    full-screen sheets on mobile and floating panels on desktop; menu toggles
    swap between hamburger and inline nav. A single-viewport sweep can pass
    the desktop arc cleanly while mobile drops the entire panel.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "click-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,1280x800"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, f"click fan-out failed: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "click-state-result.txt").read_text()
    assert "viewport: 375x812" in result
    assert "viewport: 1280x800" in result
    assert (ref / "transitions" / "click-state" / "375x812" / "tabs").is_dir()
    assert (ref / "transitions" / "click-state" / "1280x800" / "tabs").is_dir()


def test_click_state_compare_exits_nonzero_on_divergence(tmp_path: Path) -> None:
    """A diverging click target-run must FAIL the gate (exit 1).

    verification-plan registers click-state-compare severity=block and the
    dispatcher trusts the exit code, but the script previously `exit 0`'d even
    with FAIL_COUNT>0 — tab/accordion/modal motion could diverge arbitrarily
    and the ❌ rows landed in a file no consumer reads. A gate that can never
    fail certifies a broken clone.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    # Diverging inner compare: exit 1 => the outer loop must count a failure.
    stub = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("#!/usr/bin/env bash\necho '[stub] divergence'\nexit 1\n")
    stub.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "click-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "1280x800"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 1, (
        f"diverging click-state must exit 1, got {proc.returncode}: {proc.stdout}"
    )
    result = (ref / "transitions" / "click-state-result.txt").read_text()
    assert "diverged" in result


def test_hover_state_compare_rejects_malformed_viewport(tmp_path: Path) -> None:
    """Malformed VIEWPORTS entry → exit 2 with clear error.

    A silent coerce would write garbage to VIEW_W/VIEW_H and ship a broken
    capture; exit 2 is the explicit signal that the env var is wrong.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)
    _make_stub_hover_cleanup(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,bogus"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 2
    assert "malformed" in proc.stderr.lower() or "bogus" in proc.stderr



def test_image_fidelity_passes_when_impl_references_all_urls(tmp_path: Path) -> None:
    """impl source mentions every visible-images.json URL → exit 0, status=pass.

    Closes the inverse failure mode: a too-strict matcher (requiring exact-URL
    match only) false-fails impls that import the same asset via a basename
    proxy or via a CDN-rewritten path. The matcher falls back: full URL →
    basename → basename-without-query → stem.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/hero.jpg", "element": "img.hero"},
        {"type": "bg-image", "src": "https://cdn.example.com/banner.png", "element": "div", "width": 800, "height": 600},
    ]))
    (impl / "src" / "Hero.tsx").write_text(
        'export const Hero = () => <img src="https://cdn.example.com/hero.jpg" />;\n'
    )
    (impl / "src" / "Banner.tsx").write_text(
        'export const Banner = () => <div style={{ backgroundImage: "url(https://cdn.example.com/banner.png)", width: 800, height: 600 }} />;\n'
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["matched"] == 2
    assert artifact["implRoot"] == str(impl)
    assert artifact["implDir"] == str(impl)
    assert artifact["implSrcDir"] == str(impl / "src")
    assert artifact["implPkgJson"] == str(impl / "package.json")



def test_image_fidelity_fails_when_url_dropped(tmp_path: Path) -> None:
    """impl source missing a ref URL → exit 1, status=fail, unmatched lists it.

    This is the failure class the gate exists for: agent generated a component
    that silently dropped a hero/logo/banner image. AE/SSIM catches the pixel
    diff but the URL-level signal here points the agent at the specific asset
    to fix, not at a region of pixel-diff to investigate.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/dropped.jpg", "element": "img.dropped"},
    ]))
    (impl / "src" / "Empty.tsx").write_text('export const Empty = () => null;\n')
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert len(artifact["unmatched"]) == 1
    assert artifact["unmatched"][0]["src"] == "https://cdn.example.com/dropped.jpg"



def test_image_fidelity_warns_on_dimension_mismatch(tmp_path: Path) -> None:
    """impl references URL but declares a width outside DIM_TOLERANCE → status=warn.

    Warn (not fail) because CSS-driven sizing is the common case and the
    declared prop may be a min-width / hint rather than ground truth. Exit 0
    so the gate doesn't block on a soft signal — the artifact still surfaces
    the mismatch for the agent to read.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "bg-image", "src": "https://cdn.example.com/big.png", "element": "div", "width": 1000, "height": 500},
    ]))
    (impl / "src" / "Big.tsx").write_text(
        'export const Big = () => <div style={{ backgroundImage: "url(https://cdn.example.com/big.png)", width: 200, height: 500 }} />;\n'
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    # Exit 0 because warn is a soft signal — the failure class for blocking
    # is "impl dropped the URL entirely", not "impl used a different width".
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "warn"
    assert len(artifact["dimensionMismatches"]) == 1
    assert "width: ref=1000 impl=200" in artifact["dimensionMismatches"][0]["issues"]



def test_image_fidelity_reads_element_own_dims_not_ancestor_maxwidth(tmp_path: Path) -> None:
    """Regression (loop-claude-ebay-F-1): the dimension reader must read the
    element's OWN width/height, not an ancestor container's `maxWidth`/`minHeight`.

    The element carrying the URL declares width:210/height:210 (== ref), but an
    ancestor wrapper on a nearby line declares maxWidth:1344 / minHeight:220. The
    pre-fix reader used a case-insensitive, unanchored `width|height` regex over a
    ±5-line window and took the FIRST match, so it picked the ancestor's
    maxWidth=1344 / minHeight=220 and reported a bogus 1344×220 dimension mismatch.
    With the word-boundary regex + needle-line-first, it reads 210×210 → pass.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "bg-image", "src": "https://cdn.example.com/tile.png", "element": "div", "width": 210, "height": 210},
    ]))
    # Ancestor wrapper carries maxWidth/minHeight on lines BEFORE the element's own
    # line — exactly the layout that tricked the old first-match-in-window reader.
    (impl / "src" / "Grid.tsx").write_text(
        "export const Grid = () => (\n"
        '  <div style={{ minHeight: "220px", maxWidth: "1344px" }}>\n'
        '    <div style={{ backgroundImage: "url(https://cdn.example.com/tile.png)", width: "210px", height: "210px" }} />\n'
        "  </div>\n"
        ");\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass", artifact
    assert artifact["dimensionMismatches"] == [], artifact["dimensionMismatches"]



def test_image_fidelity_fails_on_local_cdn_optimizer_runtime_path(tmp_path: Path) -> None:
    """Loop-55 regression: static basename matching passed even though the
    browser loaded `/cdn-cgi/image/widtth=.../foo.webp` from the local Next app.

    The asset existed in public/ and the source mentioned `foo.webp`, so
    image-fidelity + asset-transfer both passed. At runtime, the local app
    does not serve Cloudflare image optimizer URLs, and a JS string typo
    (`widt\\u0074h`) made the path even worse. This must be a blocking
    image-fidelity failure, not a pixel-diff-only discovery.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/images/foo.webp", "element": "img.foo"},
    ]))
    (impl / "src" / "Foo.tsx").write_text(
        'export const Foo = () => <img src="/cdn-cgi/image/widt\\u0074h=640,quality=90/images/foo.webp" />;\n',
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["matched"] == 1
    assert artifact["runtimeImageIssues"]
    assert artifact["runtimeImageIssues"][0]["kind"] == "local-cdn-optimizer-path"
    assert "widt\\u0074h" in artifact["runtimeImageIssues"][0]["snippet"]



def test_image_fidelity_skips_when_no_visible_images_json(tmp_path: Path) -> None:
    """Missing visible-images.json → status=pass, exit 0 (no-op, not an error).

    Mirrors runtime-spec-coverage.sh skip behavior: the verification-plan
    only wires this row when visible-images.json exists, but the script must
    still tolerate a missing input gracefully — defensive parity in case the
    script is invoked outside the dispatcher.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass"



def test_image_fidelity_rejects_hidden_reference_manifest_only_usage(tmp_path: Path) -> None:
    """Hidden reference manifests are not rendered asset usage.

    Loop validation found impls that stuffed every ref URL into a hidden
    `reference-manifest` node so static string matching passed while the
    visible page still used placeholders. image-fidelity must ignore that
    manifest surface and fail the actually unmatched images.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/food-{i}.webp" for i in range(5)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"img.food-{i}"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "reference-manifest.tsx").write_text(
        "export function ReferenceManifest() {\n"
        "  return <div className=\"reference-manifest\" hidden>\n"
        + "\n".join(f"    <span>{url}</span>" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 1, f"hidden manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["matched"] == 0
    assert len(artifact["unmatched"]) == 5



def test_asset_utilization_rejects_hidden_reference_manifest_only_usage(tmp_path: Path) -> None:
    """asset-utilization must not count hidden reference-manifest strings as usage."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/asset-{i}.png" for i in range(5)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"img.asset-{i}"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <div className=\"reference-manifest\" style={{ display: 'none' }}>\n"
        + "\n".join(f"    <span>{url}</span>" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 1, f"hidden manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["referenced"] == 0
    assert "reference-manifest" in artifact["reason"]



def test_asset_utilization_rejects_low_opacity_asset_rail_usage(tmp_path: Path) -> None:
    """Bulk low-opacity/offscreen asset rails are not original-position usage."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/photo-{i}.webp" for i in range(6)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"section:nth-child({i + 1}) img"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <div className=\"asset-rail fixed bottom-0 opacity-10 pointer-events-none blur-sm\" aria-hidden>\n"
        + "\n".join(f"    <img src=\"/images/{Path(url).name}\" />" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 1, f"asset rail must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "fail"
    assert "asset rail" in artifact["reason"]


def test_asset_utilization_does_not_treat_generated_ref_css_as_asset_rail(tmp_path: Path) -> None:
    """Generated ref-css can contain many original asset strings without
    becoming a hidden-manifest/asset-rail cheat.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src" / "ref-css").mkdir(parents=True)
    urls = [f"https://cdn.example.com/ref-{i}.webp" for i in range(6)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"section:nth-child({i + 1}) img"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "ref-css" / "page.css").write_text(
        ".reference-manifest.asset-rail { display: none; opacity: 0; }\n"
        + "\n".join(
            f".ref-{i} {{ background-image: url('/images/{Path(url).name}'); }}"
            for i, url in enumerate(urls)
        )
        + "\n",
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "import './ref-css/page.css';\n"
        "export default function App() { return <main className=\"page\">ok</main>; }\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, f"generated ref-css must not trip asset-rail guard: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "pass"
    assert "asset rail" not in artifact["reason"]


def test_asset_utilization_uses_configured_generated_evidence_dirs(tmp_path: Path) -> None:
    """Generated evidence dir names are configurable, not hardwired to ref-css."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    generated_dir = impl / "src" / "reference-css"
    ref.mkdir()
    generated_dir.mkdir(parents=True)
    urls = [f"https://cdn.example.com/generated-{i}.webp" for i in range(6)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"section:nth-child({i + 1}) img"}
        for i, url in enumerate(urls)
    ]))
    (generated_dir / "page.css").write_text(
        ".reference-manifest.asset-rail { display: none; opacity: 0; }\n"
        + "\n".join(
            f".generated-{i} {{ background-image: url('/images/{Path(url).name}'); }}"
            for i, url in enumerate(urls)
        )
        + "\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "UI_CLONE_GENERATED_EVIDENCE_DIRS": "reference-css"},
    )

    assert proc.returncode == 0, f"configured generated dir must not trip asset-rail guard: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "pass"
    assert "asset rail" not in artifact["reason"]



def test_bundle_impl_coverage_script_fails_when_libs_missing(tmp_path: Path) -> None:
    """End-to-end: bundle-map detects gsap+lenis, impl/package.json lacks both → exit 1.
    """
    import subprocess
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"v.js": {"role": "vendor", "libs": ["gsap-like-strings", "motion-like"]}},
        "notes": "lenis on <html>",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl", "dependencies": {"next": "16", "react": "19", "react-dom": "19"},
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "package.json")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, f"missing libs must fail: {proc.stderr}"
    out = json.loads((ref / "bundle-impl-coverage.json").read_text())
    assert out["status"] == "fail"
    sigs = {m["signature"] for m in out["missingDeps"]}
    assert "gsap-like-strings" in sigs
    assert "motion-like" in sigs
    assert "lenis" in sigs



def test_bundle_impl_coverage_uses_standalone_python_driver() -> None:
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    driver = script.with_name("bundle_impl_coverage.py")
    shell = script.read_text(encoding="utf-8")

    assert "<<" not in shell, "bundle coverage check must not execute Bash heredocs"
    assert 'python3 "$SCRIPT_DIR/bundle_impl_coverage.py"' in shell
    compile(driver.read_text(encoding="utf-8"), str(driver), "exec")


def test_bundle_impl_coverage_script_passes_when_all_installed(tmp_path: Path) -> None:
    import subprocess
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"v.js": {"role": "vendor", "libs": ["gsap-like-strings"]}},
        "notes": "lenis on <html>",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"next": "16", "gsap": "3.12", "lenis": "1.0"},
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "package.json")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, f"all installed must pass: {proc.stderr}"
