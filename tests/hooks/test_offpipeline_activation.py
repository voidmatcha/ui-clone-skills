"""Off-pipeline scratch-clone ACTIVATION closure (omx postmortem).

A manual scratch clone shipped 1593px short and the agent declared completion
after build/responsive/interaction checks only. Root cause was not missing
detectors — it was activation: every hook fast-skipped in a tree without
tmp/ref, including the external-browse breadcrumb writer that the off-pipeline
detector depends on. These tests lock the four activation paths:

1. hooks/shim.sh proceeds when the external-browse crumb dir exists, and when
   the payload itself is an external agent-browser open (so pre_bash can WRITE
   the first crumb in a no-tmp/ref tree).
2. section_gate blocks Stop once on the CORRELATED PAIR — browse crumb AND
   clone-write crumb for THIS session — with no owned ref dir. Browse-only
   (orchestrators) and writes-only (ordinary web dev) sessions stop freely
   (UI_RE_ALLOW_OFFPIPELINE=1 and stop_hook_active release).
3. pre_generate widens its guard to any markup/style write while the crumb
   exists (repo-infrastructure paths exempt).
4. the pre_bash declaration cascade blocks completion commits when the crumb +
   clone-shaped writes exist with no active ref.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ._helpers import run_hook

REPO = Path(__file__).resolve().parents[2]
SHIM = REPO / "hooks" / "shim.sh"
CRUMB_DIR = "tmp/.ui-re-external-browse"


def _digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _write_crumb(root: Path, session_id: str) -> None:
    d = root / CRUMB_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{_digest(session_id)}.json").write_text(
        json.dumps({"url": "https://example.org", "source": "test"})
    )


def _write_clone_writes(root: Path, session_id: str) -> None:
    d = root / CRUMB_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{_digest(session_id)}-writes.json").write_text(
        json.dumps({"paths": ["scratch/index.html"]})
    )


def _shim(project_dir: Path, module: str, stdin: str) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    return subprocess.run(
        ["bash", str(SHIM), module],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project_dir),
        timeout=120,
    )


# ── 1. shim activation ──────────────────────────────────────────────────────


def test_shim_fast_skips_unrelated_tree(tmp_path: Path) -> None:
    r = _shim(tmp_path, "json.tool", "{}")
    assert r.returncode == 0
    assert r.stdout.strip() == "", "unrelated tree must stay fast-skipped"


def test_shim_proceeds_when_crumb_dir_exists(tmp_path: Path) -> None:
    (tmp_path / CRUMB_DIR).mkdir(parents=True)
    r = _shim(tmp_path, "json.tool", "{}")
    assert r.stdout.strip() == "{}", (
        f"crumb dir must activate the hook stack: {r.stdout!r} {r.stderr!r}"
    )


def test_shim_proceeds_for_external_browse_payload(tmp_path: Path) -> None:
    """The crumb WRITER (pre_bash) must be reachable before any crumb exists."""
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "agent-browser open https://example.org --session s"},
            "session_id": "sess-x",
        }
    )
    r = _shim(tmp_path, "json.tool", payload)
    assert payload_roundtrips(r.stdout, payload), (
        f"external agent-browser open must reach python: {r.stdout!r} {r.stderr!r}"
    )


def payload_roundtrips(stdout: str, payload: str) -> bool:
    try:
        return bool(json.loads(stdout) == json.loads(payload))
    except json.JSONDecodeError:
        return False


def test_pre_bash_writes_crumb_in_no_ref_tree(tmp_path: Path) -> None:
    """End-to-end: pre_bash run in a tree WITHOUT tmp/ref must leave the crumb."""
    stdin = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "agent-browser open https://example.org --session s"},
            "session_id": "sess-crumb",
            "cwd": str(tmp_path),
        }
    )
    r = run_hook(
        "ui_clone.hooks.pre_bash",
        stdin,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert r.returncode == 0
    crumb = tmp_path / CRUMB_DIR / f"{_digest('sess-crumb')}.json"
    assert crumb.is_file(), "external browse must leave the breadcrumb"


# ── 2. Stop gate on crumb + no ref ──────────────────────────────────────────


def _stop_payload(session_id: str, active: bool = False) -> str:
    return json.dumps({"session_id": session_id, "stop_hook_active": active})


def test_stop_blocks_on_browse_plus_writes_without_ref(tmp_path: Path) -> None:
    """The CORRELATED PAIR (browse crumb + clone-write crumb) blocks once."""
    _write_crumb(tmp_path, "sess-stop")
    _write_clone_writes(tmp_path, "sess-stop")
    r = run_hook(
        "ui_clone.hooks.section_gate",
        _stop_payload("sess-stop"),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert "block" in out.lower() or r.returncode == 2, (
        f"browse+writes + no owned ref dir must block Stop: rc={r.returncode} out={out[:400]}"
    )
    assert "pipeline" in out, "block must name the pipeline bootstrap path"


def test_stop_free_for_browse_only_session(tmp_path: Path) -> None:
    """Orchestrator/monitoring/research sessions browse without writing —
    one-signal activation fired on a live orchestrator (2026-06-12); the
    browse crumb alone must never block Stop."""
    _write_crumb(tmp_path, "sess-browse-only")
    r = run_hook(
        "ui_clone.hooks.section_gate",
        _stop_payload("sess-browse-only"),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0 and '"decision": "block"' not in out


def test_stop_free_for_writes_only_session(tmp_path: Path) -> None:
    """Ordinary web dev writes markup without browsing an external site —
    the write crumb alone must never block Stop via this path."""
    _write_clone_writes(tmp_path, "sess-writes-only")
    r = run_hook(
        "ui_clone.hooks.section_gate",
        _stop_payload("sess-writes-only"),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0 and '"decision": "block"' not in out


def test_stop_allows_with_offpipeline_env(tmp_path: Path) -> None:
    _write_crumb(tmp_path, "sess-allow")
    _write_clone_writes(tmp_path, "sess-allow")
    r = run_hook(
        "ui_clone.hooks.section_gate",
        _stop_payload("sess-allow"),
        env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "UI_RE_ALLOW_OFFPIPELINE": "1",
        },
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0 and '"decision": "block"' not in out


def test_stop_offpipeline_demoted_to_advisory_under_headless_driver(tmp_path: Path) -> None:
    """The off-pipeline Stop block costs the benchmark driver a whole iteration
    for a nudge it cannot act on mid-turn. Under UI_RE_HEADLESS_DRIVER the
    reason must go to stderr and the turn must end cleanly."""
    _write_crumb(tmp_path, "sess-headless")
    _write_clone_writes(tmp_path, "sess-headless")
    r = run_hook(
        "ui_clone.hooks.section_gate",
        _stop_payload("sess-headless"),
        env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "UI_RE_HEADLESS_DRIVER": "1",
        },
    )
    assert r.returncode == 0
    assert '"decision": "block"' not in r.stdout, (
        f"headless driver must not block on stdout; got: {r.stdout[:400]!r}"
    )
    assert "off-pipeline" in r.stderr, (
        f"advisory reason must still reach stderr; got: {r.stderr[:400]!r}"
    )


def test_stop_respects_reentrancy(tmp_path: Path) -> None:
    _write_crumb(tmp_path, "sess-re")
    _write_clone_writes(tmp_path, "sess-re")
    r = run_hook(
        "ui_clone.hooks.section_gate",
        _stop_payload("sess-re", active=True),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0 and '"decision": "block"' not in out


# ── 3. pre_generate widened write guard ─────────────────────────────────────


def _write_payload(path: Path, session_id: str) -> str:
    return json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(path), "content": "<html></html>"},
            "session_id": session_id,
        }
    )


def test_pre_generate_blocks_scratch_html_with_crumb(tmp_path: Path) -> None:
    _write_crumb(tmp_path, "sess-pg")
    r = run_hook(
        "ui_clone.hooks.pre_generate",
        _write_payload(tmp_path / "scratch" / "index.html", "sess-pg"),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert "deny" in out or '"decision": "block"' in out, (
        f"crumbed session writing scratch markup must be guarded: {out[:400]}"
    )


def test_pre_generate_exempts_repo_infrastructure(tmp_path: Path) -> None:
    _write_crumb(tmp_path, "sess-pg2")
    r = run_hook(
        "ui_clone.hooks.pre_generate",
        _write_payload(tmp_path / "skills" / "x" / "style.css", "sess-pg2"),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert "deny" not in out and '"decision": "block"' not in out


def test_pre_generate_untouched_without_crumb(tmp_path: Path) -> None:
    r = run_hook(
        "ui_clone.hooks.pre_generate",
        _write_payload(tmp_path / "scratch" / "index.html", "sess-pg3"),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert "deny" not in out and '"decision": "block"' not in out


# ── 4. declaration cascade ──────────────────────────────────────────────────


def test_commit_blocked_on_crumb_plus_clone_writes(tmp_path: Path) -> None:
    _write_crumb(tmp_path, "sess-done")
    _write_clone_writes(tmp_path, "sess-done")
    stdin = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'clone complete'"},
            "session_id": "sess-done",
            "cwd": str(tmp_path),
        }
    )
    r = run_hook(
        "ui_clone.hooks.pre_bash",
        stdin,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert "deny" in out, (
        f"completion commit with crumb+writes and no ref must block: {out[:400]}"
    )
    assert "pipeline" in out


def test_commit_allowed_without_clone_writes(tmp_path: Path) -> None:
    _write_crumb(tmp_path, "sess-done2")
    stdin = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'unrelated docs'"},
            "session_id": "sess-done2",
            "cwd": str(tmp_path),
        }
    )
    r = run_hook(
        "ui_clone.hooks.pre_bash",
        stdin,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    out = r.stdout + r.stderr
    assert "deny" not in out


# ── crumb writer false-positive hardening (orchestrator live-fire) ──────────


def _crumb_exists(root: Path, session_id: str) -> bool:
    return (root / CRUMB_DIR / f"{_digest(session_id)}.json").is_file()


def _run_pre_bash(
    root: Path, cmd: str, session_id: str
) -> subprocess.CompletedProcess[str]:
    stdin = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "session_id": session_id,
            "cwd": str(root),
        }
    )
    return run_hook(
        "ui_clone.hooks.pre_bash", stdin, env={"CLAUDE_PROJECT_DIR": str(root)}
    )


def test_no_crumb_for_heredoc_body_mention(tmp_path: Path) -> None:
    """Live-fire false positive: the Item-6 commission doc written via
    cat <<EOF contained the literal trigger string and crumbed the
    ORCHESTRATOR session (url captured as 'https://<external>`')."""
    cmd = (
        "cat > /tmp/doc.txt <<'EOF'\n"
        "an `agent-browser ... open https://<external>` command must always "
        "leave the crumb\n"
        "EOF"
    )
    _run_pre_bash(tmp_path, cmd, "sess-heredoc")
    assert not _crumb_exists(tmp_path, "sess-heredoc"), (
        "heredoc body text must never write a crumb"
    )


def test_no_crumb_for_quoted_echo_mention(tmp_path: Path) -> None:
    _run_pre_bash(
        tmp_path,
        "echo 'run agent-browser open https://example.org later'",
        "sess-quoted",
    )
    assert not _crumb_exists(tmp_path, "sess-quoted")


def test_no_crumb_for_placeholder_url(tmp_path: Path) -> None:
    _run_pre_bash(
        tmp_path,
        "agent-browser open https://<external> --session s",
        "sess-placeholder",
    )
    assert not _crumb_exists(tmp_path, "sess-placeholder")


def test_crumb_for_real_command_after_chain(tmp_path: Path) -> None:
    _run_pre_bash(
        tmp_path,
        "cd /x && agent-browser open https://example.org --session s",
        "sess-chain",
    )
    assert _crumb_exists(tmp_path, "sess-chain")
