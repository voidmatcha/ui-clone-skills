"""PreToolUse Bash fail-open regressions (declaration, writes, mirrors, scaffold,
section-compare matcher, multi-ref enforcement)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui_clone.hooks.pre_bash_rules import dispatcher
from ui_clone.hooks.pre_bash_rules.bash_write import _bash_write_target
from ui_clone.hooks.pre_bash_rules.declaration import _is_declaration_command
from ui_clone.hooks.pre_bash_rules.impl_scaffold import _IMPL_SCAFFOLD_PATTERNS
from ui_clone.hooks.pre_bash_rules.section_compare import _is_section_compare_command
from ui_clone.hooks.pre_bash_rules.static_mirror import _static_mirror_download_violation


@pytest.mark.parametrize(
    "cmd",
    [
        "git -C impl push",
        "git -c user.name=x commit -m msg",
        "git --no-pager push origin main",
        "if true; then git push; fi",
        "{ git push; }",
        "echo `git push`",
        "command git push",
        "exec git commit -m x",
        "time git push",
        "nohup git push &",
    ],
)
def test_declaration_wrappers_and_global_options_detected(cmd: str) -> None:
    assert _is_declaration_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    ["git status", "git log --oneline", 'echo "git push later"', "gh pr view"],
)
def test_declaration_non_matches(cmd: str) -> None:
    assert not _is_declaration_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "echo x >| impl/src/components/Hero.tsx",
        "cp /tmp/Hero.tsx impl/src/components/Hero.tsx",
        "install -m 644 /tmp/Hero.tsx impl/src/components/Hero.tsx",
        "rsync -a /tmp/Hero.tsx impl/src/components/Hero.tsx",
    ],
)
def test_bash_write_component_targets(cmd: str) -> None:
    assert _bash_write_target(cmd) == "impl/src/components/Hero.tsx"


@pytest.mark.parametrize(
    "cmd",
    [
        "curl https://example.com > impl/index.html",
        "curl -o impl/index.html https://example.com",
        "wget -O impl/index.html https://example.com",
        "wget -p -P impl https://example.com",
    ],
)
def test_static_mirror_download_outside_public(cmd: str) -> None:
    assert _static_mirror_download_violation(cmd)


def test_static_mirror_asset_download_still_allowed() -> None:
    assert not _static_mirror_download_violation(
        "curl -o impl/public/fonts/a.woff2 https://example.com/a.woff2"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "npx -y create-vite impl --template react-ts",
        "pnpm dlx create-next-app impl",
        "yarn dlx create-vite impl",
        "bunx create-vite impl",
        "git clone https://github.com/x/tpl impl",
        "git clone https://github.com/x/tpl ./impl",
    ],
)
def test_impl_scaffold_variants_detected(cmd: str) -> None:
    assert _IMPL_SCAFFOLD_PATTERNS.search(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        'bash "$PLUGIN_ROOT/skills/visual-debug/scripts/section-compare.sh" ref impl',
        "uv run python -m ui_clone.measure section-compare ref",
        "uv run --with pillow python3 -m ui_clone.measure section-compare ref",
        "cd x && bash skills/visual-debug/scripts/section-compare.sh ref",
    ],
)
def test_section_compare_invocations_match(cmd: str) -> None:
    assert _is_section_compare_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        'pgrep -f "bash skills/visual-debug/scripts/section-compare.sh"',
        'echo "; bash skills/visual-debug/scripts/section-compare.sh"',
        "grep -n x skills/visual-debug/scripts/section-compare.sh",
    ],
)
def test_section_compare_data_mentions_do_not_match(cmd: str) -> None:
    assert not _is_section_compare_command(cmd)


def _active_ref(root: Path, name: str) -> Path:
    ref = root / "tmp" / "ref" / name
    ref.mkdir(parents=True)
    (ref / ".ui-re-active").write_text("x")
    return ref


def test_section_compare_guard_checks_every_active_ref(tmp_path: Path) -> None:
    _active_ref(tmp_path, "a-clean")
    second = _active_ref(tmp_path, "b-dirty")
    (second / "verification-plan.json").write_text(json.dumps({
        "requiredChecks": [{"id": "dom-mirror-check", "severity": "block"}],
    }))
    reason = dispatcher._guard_section_compare(
        "bash skills/visual-debug/scripts/section-compare.sh x", tmp_path
    )
    assert reason is not None and "dom-mirror-check" in reason


def test_declaration_cascade_checks_every_active_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ui_clone.state import PipelineState

    done = _active_ref(tmp_path, "a-done")
    state = PipelineState.load(done)
    state.current_gate = "done"
    state.save(done)
    (done / "sections").mkdir()
    (done / "sections" / "result.txt").write_text("| hero | 0 | 0 | ok | ✅ |\n")
    failing = _active_ref(tmp_path, "b-failing")
    (failing / "sections").mkdir()
    (failing / "sections" / "result.txt").write_text("| hero | 9 | 9 | critical | ❌ |\n")

    blocks: list[str] = []
    monkeypatch.setattr(dispatcher, "_emit_block", blocks.append)
    monkeypatch.setattr(dispatcher, "should_enforce_ref_for_session", lambda ref, sid: True)
    with pytest.raises(SystemExit):
        dispatcher._run_declaration_cascade("git push", tmp_path, "sess")
    assert blocks and "b-failing" in blocks[0]
