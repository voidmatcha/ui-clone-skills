"""Scoped clone runs (section-only / element-only / trigger-opened modal).

The documented scoped flow (operational-rules.md "Scope adjustments by request
shape", element-capture.md) never runs the page-level pipeline: it records the
target with scripts/extract/element-evidence.sh (-> element-target.json),
captures clip frames under frames/ref, and writes the target component. The
hooks must recognise that ref dir as a sanctioned run instead of denying the
write as off-pipeline, running page-level gates, or demanding page-level
completion at Stop.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from ui_clone.hooks._common import (
    find_scoped_ref_dirs,
    is_valid_element_target,
    mark_ref_session,
    select_scoped_ref_dir,
)
from ui_clone.hooks.pre_bash_rules.bash_write import _bash_enforcement_state_target

from ._helpers import (
    _populate_pre_generate_artifacts,
    _set_pre_generate_passed_state,
    run_hook,
    set_active_marker,
    write_extracted_json,
)

PRE_GENERATE = "ui_clone.hooks.pre_generate"
SECTION_GATE = "ui_clone.hooks.section_gate"
SESSION = "scoped-run-session"


def _element_target_payload(selector: str = "section.hero") -> dict[str, object]:
    """Shape written by scripts/extract/element-evidence.sh (schemaVersion 1)."""
    return {
        "schemaVersion": 1,
        "ok": True,
        "url": "https://example.org/",
        "annotation": {
            "id": "element-probe",
            "selector": selector,
            "selectorCandidates": [selector],
            "text": "Hero",
            "bbox": {"x": 0, "y": 0, "width": 1440, "height": 900},
            "attributes": {},
            "computedStyle": {},
            "timeline": [],
            "animations": [],
        },
    }


def _scoped_ref(root: Path, name: str = "hero", payload: object | None = None) -> Path:
    ref = root / "tmp" / "ref" / name
    (ref / "frames" / "ref").mkdir(parents=True, exist_ok=True)
    (ref / "frames" / "ref" / "0000.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    body = _element_target_payload() if payload is None else payload
    text = body if isinstance(body, str) else json.dumps(body)
    (ref / "element-target.json").write_text(text, encoding="utf-8")
    return ref


def _browse_crumb(root: Path, session_id: str = SESSION) -> None:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    d = root / "tmp" / ".ui-re-external-browse"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{digest}.json").write_text(json.dumps({"url": "https://example.org"}))


def _clone_writes_crumb(root: Path, session_id: str = SESSION) -> None:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    d = root / "tmp" / ".ui-re-external-browse"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{digest}-writes.json").write_text(json.dumps({"paths": ["impl/src/components/Hero.tsx"]}))


def _write_payload(file_path: Path) -> str:
    return json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(file_path), "content": "export default 1"},
            "session_id": SESSION,
        }
    )


def _pre_generate(root: Path, file_path: Path) -> dict[str, object] | None:
    result = run_hook(
        PRE_GENERATE,
        stdin_data=_write_payload(file_path),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    return json.loads(out) if out else None


def _stop(root: Path) -> str:
    result = run_hook(
        SECTION_GATE,
        stdin_data=json.dumps({"session_id": SESSION, "stop_hook_active": False}),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    return str(result.stdout) + str(result.stderr)


# -- recognition rule -------------------------------------------------------


def test_valid_element_target_matches_canonical_script_shape(tmp_path: Path) -> None:
    ref = _scoped_ref(tmp_path)
    assert is_valid_element_target(ref / "element-target.json")
    assert find_scoped_ref_dirs(tmp_path / "tmp" / "ref") == [ref]


def test_empty_or_hand_written_element_target_is_not_a_scoped_run(tmp_path: Path) -> None:
    bad_payloads: list[object] = [
        "",
        "{}",
        {"schemaVersion": 1, "ok": True},
        {**_element_target_payload(), "ok": False, "error": "selector not found"},
        {**_element_target_payload(), "url": "about:blank"},
        {**_element_target_payload(), "annotation": {"id": "element-probe", "selector": ""}},
        {**_element_target_payload(), "schemaVersion": 2},
    ]
    for index, payload in enumerate(bad_payloads):
        ref = _scoped_ref(tmp_path, f"bad-{index}", payload)
        assert not is_valid_element_target(ref / "element-target.json"), payload
    assert find_scoped_ref_dirs(tmp_path / "tmp" / "ref") == []


def test_page_level_markers_disqualify_scoped_run(tmp_path: Path) -> None:
    for marker in (".ui-re-active", "extracted.json", "pipeline-state.json"):
        ref = _scoped_ref(tmp_path, f"page-{marker.strip('.')}")
        (ref / marker).write_text("{}", encoding="utf-8")
    assert find_scoped_ref_dirs(tmp_path / "tmp" / "ref") == []


def test_stale_element_target_is_ignored(tmp_path: Path) -> None:
    ref = _scoped_ref(tmp_path)
    old = time.time() - 30 * 24 * 3600
    os.utime(ref / "element-target.json", (old, old))
    assert find_scoped_ref_dirs(tmp_path / "tmp" / "ref") == []


def test_target_name_match_wins_over_newer_scoped_dir(tmp_path: Path) -> None:
    hero = _scoped_ref(tmp_path, "hero")
    old = time.time() - 60
    os.utime(hero / "element-target.json", (old, old))
    _scoped_ref(tmp_path, "pricing-modal")
    search_root = tmp_path / "tmp" / "ref"
    target = tmp_path / "impl" / "src" / "components" / "Hero.tsx"
    assert select_scoped_ref_dir(search_root, str(target), None) == hero
    modal = tmp_path / "impl" / "src" / "components" / "PricingModal.tsx"
    assert select_scoped_ref_dir(search_root, str(modal), None) == search_root / "pricing-modal"


# -- pre_generate ------------------------------------------------------------


def test_scoped_write_allowed_after_external_browse(tmp_path: Path) -> None:
    """Original repro: the scoped flow was denied with 'Enter the pipeline first'."""
    ref = _scoped_ref(tmp_path)
    _browse_crumb(tmp_path)
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Hero.tsx")
    assert decision is None, decision
    # Scoped runs never activate the page-level Stop enforcement chain.
    assert not (ref / ".ui-re-active").exists()


def test_offpipeline_write_without_scoped_marker_still_denied(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref").mkdir(parents=True)
    _browse_crumb(tmp_path)
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Hero.tsx")
    assert decision is not None
    assert decision["decision"] == "block"
    assert "Enter the pipeline first" in str(decision["reason"])


def test_hand_written_element_target_does_not_unlock_writes(tmp_path: Path) -> None:
    _scoped_ref(tmp_path, payload="{}")
    _browse_crumb(tmp_path)
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Hero.tsx")
    assert decision is not None
    assert "Enter the pipeline first" in str(decision["reason"])


def test_tool_write_to_element_target_is_denied(tmp_path: Path) -> None:
    ref = _scoped_ref(tmp_path)
    decision = _pre_generate(tmp_path, ref / "element-target.json")
    assert decision is not None
    assert "element-evidence.sh" in str(decision["reason"])


def test_bash_write_to_element_target_is_enforcement_state() -> None:
    assert _bash_enforcement_state_target(
        "echo '{}' > tmp/ref/hero/element-target.json"
    )
    assert _bash_enforcement_state_target("rm tmp/ref/hero/element-target.json")
    # The canonical producer names the output path as an argument, not a verb target.
    assert (
        _bash_enforcement_state_target(
            'bash scripts/extract/element-evidence.sh s https://example.org/ '
            '"section.hero" tmp/ref/hero/element-target.json'
        )
        is None
    )


def _pre_bash(root: Path, cmd: str) -> str:
    result = run_hook(
        "ui_clone.hooks.pre_bash",
        stdin_data=json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
                "session_id": SESSION,
                "cwd": str(root),
            }
        ),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    return str(result.stdout) + str(result.stderr)


def test_bash_component_write_follows_scoped_rule(tmp_path: Path) -> None:
    search_root = tmp_path / "tmp" / "ref"
    page = search_root / "landing"
    page.mkdir(parents=True)
    write_extracted_json(page)
    _scoped_ref(tmp_path, "hero")
    hero = tmp_path / "impl" / "src" / "components" / "Hero.tsx"
    assert '"deny"' not in _pre_bash(tmp_path, f"echo x > {hero}")
    footer = tmp_path / "impl" / "src" / "components" / "Footer.tsx"
    assert "extraction incomplete" in _pre_bash(tmp_path, f"echo x > {footer}")


def test_declaration_not_blocked_as_offpipeline_for_scoped_run(tmp_path: Path) -> None:
    _browse_crumb(tmp_path)
    _clone_writes_crumb(tmp_path)
    (tmp_path / "tmp" / "ref").mkdir(parents=True)
    out = _pre_bash(tmp_path, "git commit -m 'hero clone'")
    assert "deny" in out and "NO tmp/ref/<component>" in out
    # With a scoped run the off-pipeline block is gone, but the commit still
    # waits for the scoped completion command (no frames/impl, no diff yet).
    _scoped_ref(tmp_path)
    out = _pre_bash(tmp_path, "git commit -m 'hero clone'")
    assert "NO tmp/ref/<component>" not in out
    assert "has not passed scoped_check" in out
    assert "impl-frames-missing" in out and "diff-missing" in out
    # Non-declaration commands are never held by the scoped completion check.
    assert "deny" not in _pre_bash(tmp_path, "ls tmp/ref")


def test_page_level_run_unchanged_when_unrelated_scoped_dir_exists(tmp_path: Path) -> None:
    """A page-level ref with missing extraction still blocks a non-matching write."""
    search_root = tmp_path / "tmp" / "ref"
    page = search_root / "landing"
    page.mkdir(parents=True)
    write_extracted_json(page)
    _scoped_ref(tmp_path, "hero")
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Footer.tsx")
    assert decision is not None
    assert "extraction incomplete" in str(decision["reason"])


def test_page_level_run_passing_gate_still_activates(tmp_path: Path) -> None:
    search_root = tmp_path / "tmp" / "ref"
    page = search_root / "landing"
    page.mkdir(parents=True)
    _populate_pre_generate_artifacts(page)
    _set_pre_generate_passed_state(page)
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Footer.tsx")
    assert decision is None, decision
    assert (page / ".ui-re-active").is_file()


def test_scoped_target_match_bypasses_unrelated_page_ref(tmp_path: Path) -> None:
    search_root = tmp_path / "tmp" / "ref"
    page = search_root / "landing"
    page.mkdir(parents=True)
    write_extracted_json(page)
    _scoped_ref(tmp_path, "hero")
    _browse_crumb(tmp_path)
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Hero.tsx")
    assert decision is None, decision
    assert not (page / ".ui-re-active").exists()


def test_scoped_run_after_terminal_page_ref_is_allowed(tmp_path: Path) -> None:
    search_root = tmp_path / "tmp" / "ref"
    page = search_root / "landing"
    page.mkdir(parents=True)
    write_extracted_json(page)
    (page / "pipeline-state.json").write_text(
        json.dumps(
            {
                "current_gate": "done",
                "terminalState": {"status": "success", "writtenBy": "pipeline"},
            }
        ),
        encoding="utf-8",
    )
    _scoped_ref(tmp_path, "pricing-modal")
    _browse_crumb(tmp_path)
    decision = _pre_generate(tmp_path, tmp_path / "impl" / "src" / "components" / "Dialog.tsx")
    assert decision is None, decision


# -- Stop gate -----------------------------------------------------------------


def test_stop_does_not_demand_page_completion_for_scoped_run(tmp_path: Path) -> None:
    """A write attempted before the capture dir existed left a clone-write crumb;
    once the scoped run exists the off-pipeline Stop gate must not fire."""
    _browse_crumb(tmp_path)
    _clone_writes_crumb(tmp_path)
    _scoped_ref(tmp_path)
    out = _stop(tmp_path)
    # Neither the off-pipeline nor a page-level gate fires; the scoped
    # completion gate does, because this evidence has no impl frames/diff.
    assert "off-pipeline Stop gate" not in out
    assert "pipeline-state" not in out
    assert '"decision": "block"' in out, out
    assert "scoped completion gate" in out
    assert "python -m ui_clone.scoped_check" in out


def test_stop_still_blocks_offpipeline_without_scoped_run(tmp_path: Path) -> None:
    _browse_crumb(tmp_path)
    _clone_writes_crumb(tmp_path)
    _scoped_ref(tmp_path, payload="{}")
    out = _stop(tmp_path)
    assert '"decision": "block"' in out, out
    assert "off-pipeline Stop gate" in out


def test_stop_page_level_active_ref_still_enforced(tmp_path: Path) -> None:
    search_root = tmp_path / "tmp" / "ref"
    page = search_root / "landing"
    page.mkdir(parents=True)
    write_extracted_json(page)
    set_active_marker(page)
    mark_ref_session(page, SESSION, source="test")
    _scoped_ref(tmp_path, "hero")
    out = _stop(tmp_path)
    assert '"decision": "block"' in out, out


# -- scoped completion evidence is script-produced only ----------------------


def test_bash_write_to_scoped_evidence_is_enforcement_state() -> None:
    for name in (
        "tmp/ref/hero/pixel-perfect-diff.json",
        "tmp/ref/hero/frames/impl/capture-manifest.json",
        "tmp/ref/hero/.scoped-check-cache.json",
    ):
        assert _bash_enforcement_state_target(f"echo '{{}}' > {name}"), name
        assert _bash_enforcement_state_target(f"cp /tmp/x.json {name}"), name
        assert _bash_enforcement_state_target(f"python3 -c \"open('{name}','w').write('x')\""), name
        assert _bash_enforcement_state_target(f"rm {name}"), name
    # The canonical producers name the ref dir / script, never the artifact.
    for cmd in (
        "python -m ui_clone.scoped_diff tmp/ref/hero",
        "python -m ui_clone.scoped_check tmp/ref/hero --json",
        "bash scripts/extract/element-state-capture.sh clip s http://localhost:5173/ "
        '"section.hero" tmp/ref/hero impl idle',
        "bash scripts/extract/element-state-capture.sh video s http://localhost:5173/ "
        "tmp/ref/hero impl tmp/ref/hero/open.webm open",
        "jq . tmp/ref/hero/pixel-perfect-diff.json",
    ):
        assert _bash_enforcement_state_target(cmd) is None, cmd


def test_tool_write_to_scoped_evidence_is_denied(tmp_path: Path) -> None:
    ref = _scoped_ref(tmp_path)
    for name, producer in (
        ("pixel-perfect-diff.json", "ui_clone.scoped_diff"),
        ("frames/impl/capture-manifest.json", "element-state-capture.sh"),
        (".scoped-check-cache.json", "ui_clone.scoped_check"),
    ):
        decision = _pre_generate(tmp_path, ref / name)
        assert decision is not None, name
        assert producer in str(decision["reason"]), name


def test_direct_recorder_invocation_is_denied(tmp_path: Path) -> None:
    _scoped_ref(tmp_path)
    out = _pre_bash(
        tmp_path,
        "python -m ui_clone.element_capture record-clip tmp/ref/hero impl idle "
        "--full /tmp/v.png --session s < /tmp/env.json",
    )
    assert '"deny"' in out and "element-state-capture.sh" in out
    assert '"deny"' not in _pre_bash(tmp_path, "python -m ui_clone.scoped_diff tmp/ref/hero")
    assert '"deny"' not in _pre_bash(
        tmp_path,
        "bash scripts/extract/element-state-capture.sh clip s http://localhost:5173/ "
        '"section.hero" tmp/ref/hero impl idle',
    )


DENIED_PRODUCER_IMPORTS = (
    "python3 -c 'from ui_clone import scoped_diff; scoped_diff.write(p, {\"result\": \"pass\"})'",
    'python3 -c "from ui_clone.scoped_diff import build, write; write(ref, build(ref))"',
    "python3 - <<'EOF'\nfrom ui_clone.element_capture import _save_manifest\n_save_manifest(d, m)\nEOF",
    "python3 -c 'import ui_clone.scoped_frames as f; print(f.pixel_ae(a, b))'",
    "python3 -c \"import importlib; importlib.import_module('ui_clone.element_capture').record_clip()\"",
    "python3 -c \"import runpy; runpy.run_module('ui_clone.scoped_diff', run_name='__main__')\"",
    "python3 $PLUGIN_ROOT/ui_clone/scoped_diff.py tmp/ref/hero",
    "cd tmp && python3 ../ui_clone/element_capture.py record-clip . impl idle",
)
ALLOWED_PRODUCER_COMMANDS = (
    "python -m ui_clone.scoped_diff tmp/ref/hero --impl-files src/components/Hero.tsx",
    "python3 -m ui_clone.scoped_check tmp/ref/hero --json",
    "python3 -c 'from ui_clone import scoped_check; print(scoped_check.check(p))'",
    "bash scripts/extract/element-state-capture.sh clip s http://localhost:5173/ \"section.hero\" tmp/ref/hero impl idle",
    "grep -rn 'from ui_clone.scoped_diff import' tests/",
    "rg 'import ui_clone.element_capture' ui_clone",
    "git log --oneline -3 -- ui_clone/scoped_diff.py",
    "git log --grep 'from ui_clone import scoped_diff'",
    "jq .noCheat tmp/ref/hero/pixel-perfect-diff.json",
)


def test_producer_import_from_shell_is_denied(tmp_path: Path) -> None:
    """Importing a scoped evidence producer from an inline program, a heredoc,
    or the module file is denied; the CLIs, the checker, and searches stay
    allowed."""
    _scoped_ref(tmp_path)
    for cmd in DENIED_PRODUCER_IMPORTS:
        out = _pre_bash(tmp_path, cmd)
        assert '"deny"' in out, cmd
        assert "python -m ui_clone.scoped_diff <ref-dir>" in out, cmd
    for cmd in ALLOWED_PRODUCER_COMMANDS:
        assert '"deny"' not in _pre_bash(tmp_path, cmd), cmd


def test_regenerating_producer_manifest_is_denied_outside_the_plugin(tmp_path: Path) -> None:
    """`scoped_producers --write` would launder an edited producer; `--check`
    and a maintainer checkout (ui_clone/scoped_producers.py present) are free."""
    _scoped_ref(tmp_path)
    for cmd in (
        "python -m ui_clone.scoped_producers --write",
        "cd $PLUGIN_ROOT && python3 -m ui_clone.scoped_producers --write",
        "uv run python ui_clone/scoped_producers.py --write",
    ):
        out = _pre_bash(tmp_path, cmd)
        assert '"deny"' in out and "release hash manifest" in out, cmd
    for cmd in (
        "python -m ui_clone.scoped_producers --check",
        "grep -rn 'scoped_producers --write' docs/",
        "cat ui_clone/scoped_producers.sha256.json",
    ):
        assert '"deny"' not in _pre_bash(tmp_path, cmd), cmd
    (tmp_path / "ui_clone").mkdir()
    (tmp_path / "ui_clone" / "scoped_producers.py").write_text("", encoding="utf-8")
    assert '"deny"' not in _pre_bash(tmp_path, "python -m ui_clone.scoped_producers --write")


def test_evidence_writes_from_python_programs_are_denied(tmp_path: Path) -> None:
    """Writing the evidence files through Python file APIs, inline or in a
    heredoc, is enforcement-state tampering like a shell redirect."""
    _scoped_ref(tmp_path)
    for cmd in (
        "python3 -c \"open('tmp/ref/hero/pixel-perfect-diff.json','w').write('{}')\"",
        "python3 - <<'EOF'\nimport json\njson.dump({}, open('tmp/ref/hero/frames/impl/capture-manifest.json', 'w'))\nEOF",
        "python3 -c \"from pathlib import Path; Path('tmp/ref/hero/pixel-perfect-diff.json').write_text('{}')\"",
    ):
        assert '"deny"' in _pre_bash(tmp_path, cmd), cmd


def _write_hook(root: Path, file_path: Path, content: str) -> str:
    result = run_hook(
        PRE_GENERATE,
        stdin_data=json.dumps(
            {"tool_name": "Write", "tool_input": {"file_path": str(file_path), "content": content}, "session_id": SESSION}
        ),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    assert result.returncode == 0, result.stderr
    return str(result.stdout).strip()


def test_writing_a_forge_script_is_denied_outside_the_plugin(tmp_path: Path) -> None:
    _scoped_ref(tmp_path)
    forge = tmp_path / "scripts" / "forge.py"
    content = "from ui_clone.scoped_diff import build, write\nwrite(ref, build(ref))\n"
    decision = json.loads(_write_hook(tmp_path, forge, content))
    assert "scoped evidence producer" in str(decision["reason"])
    assert "python -m ui_clone.scoped_diff <ref-dir>" in str(decision["reason"])
    # The checker may be imported (read-only), and unrelated content is untouched.
    for text in ("from ui_clone import scoped_check\n", "from ui_clone.scoped_diff_reader import x\n", "print(1)\n"):
        assert not _write_hook(tmp_path, forge, text), text
    # Inside a checkout of the plugin itself (ui_clone/scoped_diff.py above the file) it is maintainer work.
    plugin = tmp_path / "plugin"
    (plugin / "ui_clone").mkdir(parents=True)
    (plugin / "ui_clone" / "scoped_diff.py").write_text("")
    assert not _write_hook(tmp_path, plugin / "tests" / "test_x.py", content)
