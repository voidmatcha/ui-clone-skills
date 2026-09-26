"""Evidence ledger (ui_clone.scoped_ledger): the PostToolUse hook records what
the canonical producer commands wrote; scoped_check accepts nothing else; the
pre_bash hook denies running an agent-written script that names evidence."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from ui_clone import scoped_check, scoped_ledger, scoped_producers
from ui_clone.hooks.pre_bash_rules import agent_script
from ui_clone.hooks.pre_bash_rules.agent_script import _agent_script_target
from ui_clone.scoped_provenance import REPO_ROOT, file_sha256

from ._helpers import run_hook
from ._scoped_fixtures import build_scoped_evidence, produce_diff, write_diff

CAPTURE = REPO_ROOT / "scripts" / "extract" / "element-state-capture.sh"
EVIDENCE = REPO_ROOT / "scripts" / "extract" / "element-evidence.sh"
SESSION = "ledger-session"


def _parse(cmd: str, root: Path) -> scoped_ledger.ProducerRun | None:
    return scoped_ledger.parse_producer_command(cmd, base=root, project_root=root)


# -- what counts as a producer command ----------------------------------------


def test_canonical_producer_commands_are_recognized(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    clip = f'bash {CAPTURE} clip s http://localhost:5173/ "section.hero" tmp/ref/hero impl idle'
    run = _parse(clip, tmp_path)
    assert run is not None
    assert run.producer == scoped_ledger.PRODUCER_CAPTURE
    assert run.ref_dir == ref.resolve() and run.files == ("frames/impl/capture-manifest.json",)
    video = (
        f"bash {CAPTURE} video s https://example.org/ tmp/ref/hero ref tmp/ref/hero/open.webm open"
    )
    run = _parse(video, tmp_path)
    assert run is not None and run.files == ("frames/ref/capture-manifest.json",)
    # Line continuations as element-capture.md prints them, a receipt arg, and no `bash` word.
    run = _parse(
        f"{CAPTURE} clip s https://example.org/ \\\n  'section.hero' tmp/ref/hero ref idle tmp/ref/hero/nav.json",
        tmp_path,
    )
    assert run is not None and run.files == ("frames/ref/capture-manifest.json",)
    run = _parse(
        f"bash {EVIDENCE} s https://example.org/ 'section.hero' tmp/ref/hero/element-target.json",
        tmp_path,
    )
    assert run is not None
    assert run.producer == scoped_ledger.PRODUCER_TARGET and run.files == ("element-target.json",)
    for cmd in (
        "python -m ui_clone.scoped_diff tmp/ref/hero",
        "python3 -m ui_clone.scoped_diff tmp/ref/hero --json",
        "uv run python -m ui_clone.scoped_diff tmp/ref/hero --impl-root impl",
        "UI_CLONE_VERIFY_TIER=quick python -m ui_clone.scoped_diff tmp/ref/hero",
    ):
        run = _parse(cmd, tmp_path)
        assert run is not None, cmd
        assert run.producer == scoped_ledger.PRODUCER_DIFF and run.files == (
            "pixel-perfect-diff.json",
        )


def test_non_canonical_shapes_are_not_producer_commands(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref" / "hero").mkdir(parents=True)
    forged = tmp_path / "element-state-capture.sh"
    forged.write_text(CAPTURE.read_text() + "\n# edited\n", encoding="utf-8")
    for cmd in (
        # chained, piped, redirected, or subshelled: the text no longer proves what ran
        "python -m ui_clone.scoped_diff tmp/ref/hero && bash forge.sh",
        "bash forge.sh; python -m ui_clone.scoped_diff tmp/ref/hero",
        "python -m ui_clone.scoped_diff tmp/ref/hero | tee log.txt",
        "python -m ui_clone.scoped_diff tmp/ref/hero > out.txt",
        "(python -m ui_clone.scoped_diff tmp/ref/hero)",
        # interpreter / module / script overrides
        "PYTHONPATH=./fake python -m ui_clone.scoped_diff tmp/ref/hero",
        "PATH=./bin:$PATH python -m ui_clone.scoped_diff tmp/ref/hero",
        "UI_CLONE_CAPTURE_DRIVER=x bash "
        + str(CAPTURE)
        + " clip s http://l/ 'a' tmp/ref/hero impl idle",
        "python -c 'import ui_clone.scoped_diff' tmp/ref/hero",
        "python -m ui_clone.scoped_check tmp/ref/hero",
        # a same-named script that is not the shipped one
        f"bash {forged} clip s http://l/ 'a' tmp/ref/hero impl idle",
        "bash element-state-capture.sh clip s http://l/ 'a' tmp/ref/hero impl idle",
        # wrong arity / side
        f"bash {CAPTURE} clip s http://l/ tmp/ref/hero impl idle",
        f"bash {CAPTURE} clip s http://l/ 'a' tmp/ref/hero mine idle",
        "bash forge.sh",
        "",
    ):
        assert _parse(cmd, tmp_path) is None, cmd


def test_shadowed_ui_clone_package_is_not_a_producer(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref" / "hero").mkdir(parents=True)
    assert _parse("python -m ui_clone.scoped_diff tmp/ref/hero", tmp_path) is not None
    (tmp_path / "ui_clone").mkdir()
    (tmp_path / "ui_clone" / "scoped_diff.py").write_text("print('forged')\n", encoding="utf-8")
    assert _parse("python -m ui_clone.scoped_diff tmp/ref/hero", tmp_path) is None
    # The plugin checkout itself is the installed package, not a shadow.
    assert (
        scoped_ledger.parse_producer_command(
            "python -m ui_clone.scoped_diff tmp/ref/hero", base=REPO_ROOT, project_root=REPO_ROOT
        )
        is not None
    )


_ROOT_VARS = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT")


@pytest.fixture
def clean_root_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in _ROOT_VARS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_plugin_root_and_pwd_expansions_are_recognized(
    tmp_path: Path, clean_root_env: pytest.MonkeyPatch
) -> None:
    """The clone-project forms: the script lives under the plugin root, the ref
    dir is `$(pwd)/tmp/ref/<c>`, the diff runs through the plugin's CLI."""
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    cases = {
        'bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip s http://l/ "section.hero" "$(pwd)/tmp/ref/hero" impl idle': scoped_ledger.PRODUCER_CAPTURE,
        'bash "${CLAUDE_PLUGIN_ROOT}/scripts/extract/element-state-capture.sh" video s http://l/ "${PWD}/tmp/ref/hero" ref "$PWD/tmp/ref/hero/open.webm" open': scoped_ledger.PRODUCER_CAPTURE,
        "bash $CODEX_PLUGIN_ROOT/scripts/extract/element-evidence.sh s http://l/ 'section.hero' $(pwd)/tmp/ref/hero/element-target.json": scoped_ledger.PRODUCER_TARGET,
        'node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(pwd)/tmp/ref/hero"': scoped_ledger.PRODUCER_DIFF,
        'node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff tmp/ref/hero --impl-files src/Hero.tsx --json': scoped_ledger.PRODUCER_DIFF,
        'uv run --project "$PLUGIN_ROOT" --no-dev --frozen python -m ui_clone.scoped_diff "$(pwd)/tmp/ref/hero"': scoped_ledger.PRODUCER_DIFF,
        f"uv run --project={REPO_ROOT} python -m ui_clone.scoped_diff tmp/ref/hero": scoped_ledger.PRODUCER_DIFF,
        f"uv run --directory {tmp_path} python3 -m ui_clone.scoped_diff tmp/ref/hero": scoped_ledger.PRODUCER_DIFF,
    }
    for cmd, producer in cases.items():
        run = _parse(cmd, tmp_path)
        assert run is not None, cmd
        assert run.producer == producer and run.ref_dir == ref.resolve(), cmd
    # A root variable the hook's own environment sets to the same root is fine.
    clean_root_env.setenv("PLUGIN_ROOT", str(REPO_ROOT))
    assert (
        _parse('node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(pwd)/tmp/ref/hero"', tmp_path)
        is not None
    )


def test_unlisted_expansions_and_uv_options_are_not_producers(
    tmp_path: Path, clean_root_env: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tmp" / "ref" / "hero").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "bin").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "bin" / "ui-clone", elsewhere / "bin" / "ui-clone")
    capture_tail = "clip s http://l/ 'a' \"$(pwd)/tmp/ref/hero\" impl idle"
    for cmd in (
        # any substitution besides the allowlist is not provenance
        f'bash "$(curl -s https://x.test/p)/scripts/extract/element-state-capture.sh" {capture_tail}',
        f'bash "$OTHER_VAR/scripts/extract/element-state-capture.sh" {capture_tail}',
        f'bash "$HOME/scripts/extract/element-state-capture.sh" {capture_tail}',
        f'bash "`pwd`/element-state-capture.sh" {capture_tail}',
        'node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(pwd -P)/tmp/ref/hero"',
        'node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(cd /x && pwd)/tmp/ref/hero"',
        # single quotes keep the variable literal: the shell never expands it
        "bash '$PLUGIN_ROOT/scripts/extract/element-state-capture.sh' " + capture_tail,
        # a root variable set as a prefix is still an override
        f'PLUGIN_ROOT=/x bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" {capture_tail}',
        'CLAUDE_PLUGIN_ROOT=/x node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff tmp/ref/hero',
        "UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m ui_clone.scoped_diff tmp/ref/hero",
        # uv options that pick another project, interpreter, package, or env
        f"uv run --project {elsewhere} python -m ui_clone.scoped_diff tmp/ref/hero",
        "uv run --project /elsewhere python -m ui_clone.scoped_diff tmp/ref/hero",
        "uv run --with evil python -m ui_clone.scoped_diff tmp/ref/hero",
        "uv run --python /tmp/py python -m ui_clone.scoped_diff tmp/ref/hero",
        "uv run --env-file .env python -m ui_clone.scoped_diff tmp/ref/hero",
        'uv run --project "$PLUGIN_ROOT" bash forge.sh tmp/ref/hero',
        # a copy of the CLI outside the plugin, node flags, a bare `ui-clone`
        f"node {elsewhere}/bin/ui-clone scoped-diff tmp/ref/hero",
        'node --require ./x.js "$PLUGIN_ROOT/bin/ui-clone" scoped-diff tmp/ref/hero',
        'node "$PLUGIN_ROOT/bin/ui-clone" scoped-check tmp/ref/hero',
        "ui-clone scoped-diff tmp/ref/hero",
        'bash node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff tmp/ref/hero',
        # a newline is a second command
        "python -m ui_clone.scoped_diff tmp/ref/hero\nbash forge.sh",
    ):
        assert _parse(cmd, tmp_path) is None, cmd
    # The hook's own environment naming another root: the command's shell
    # would expand the variable there, so it is not the shipped tree.
    clean_root_env.setenv("PLUGIN_ROOT", str(elsewhere))
    assert _parse('node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff tmp/ref/hero', tmp_path) is None
    assert (
        _parse(
            f'bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" {capture_tail}', tmp_path
        )
        is None
    )
    # ...while another allowlisted variable the hook does not carry still expands.
    assert (
        _parse('node "$CODEX_PLUGIN_ROOT/bin/ui-clone" scoped-diff tmp/ref/hero', tmp_path)
        is not None
    )


def test_node_cli_honors_module_shadowing(
    tmp_path: Path, clean_root_env: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tmp" / "ref" / "hero").mkdir(parents=True)
    cmd = 'node "$PLUGIN_ROOT/bin/ui-clone" scoped-diff "$(pwd)/tmp/ref/hero"'
    assert _parse(cmd, tmp_path) is not None
    (tmp_path / "ui_clone").mkdir()
    assert _parse(cmd, tmp_path) is None


def test_unshipped_manifest_records_nothing(tmp_path: Path) -> None:
    (tmp_path / "tmp" / "ref" / "hero").mkdir(parents=True)
    capture = f"bash {CAPTURE} clip s http://l/ 'a' tmp/ref/hero impl idle"
    assert (
        scoped_ledger.parse_producer_command(
            capture, base=tmp_path, project_root=tmp_path, manifest={}
        )
        is None
    )
    manifest = scoped_producers.load_manifest()
    assert manifest is not None
    assert (
        scoped_ledger.parse_producer_command(
            capture,
            base=tmp_path,
            project_root=tmp_path,
            manifest={**manifest, scoped_producers.DRIVER_SCRIPT: "0" * 64},
        )
        is None
    )


# -- record / check -----------------------------------------------------------


def test_record_and_problems(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    assert scoped_ledger.problems(ref) == []  # nothing to ledger yet
    diff = ref / "pixel-perfect-diff.json"
    diff.write_text("{}", encoding="utf-8")
    codes = [c for c, _ in scoped_ledger.problems(ref)]
    assert codes == ["evidence-ledger-missing"]
    assert scoped_ledger.record(
        ref, scoped_ledger.PRODUCER_DIFF, ("pixel-perfect-diff.json", "missing.json"), "cmd"
    )
    data = scoped_ledger.load(ref)
    assert data is not None and data["entries"][0]["files"] == {
        "pixel-perfect-diff.json": file_sha256(diff)
    }
    assert "missing.json" not in data["entries"][0]["files"]
    assert scoped_ledger.problems(ref) == []
    # the same bytes under another producer do not count
    diff.write_text('{"a":1}', encoding="utf-8")
    scoped_ledger.record(ref, scoped_ledger.PRODUCER_CAPTURE, ("pixel-perfect-diff.json",), "cmd")
    problems = scoped_ledger.problems(ref)
    assert [c for c, _ in problems] == ["evidence-unledgered"]
    assert "scoped-diff" in problems[0][1]
    # an edit after the record is unledgered; a malformed ledger is missing
    scoped_ledger.record(ref, scoped_ledger.PRODUCER_DIFF, ("pixel-perfect-diff.json",), "cmd")
    assert scoped_ledger.problems(ref) == []
    diff.write_text('{"a":2}', encoding="utf-8")
    assert [c for c, _ in scoped_ledger.problems(ref)] == ["evidence-unledgered"]
    scoped_ledger.ledger_path(ref).write_text("[]", encoding="utf-8")
    assert [c for c, _ in scoped_ledger.problems(ref)] == ["evidence-ledger-missing"]
    assert scoped_ledger.record(ref, scoped_ledger.PRODUCER_DIFF, ("nothing.json",), "cmd") is None


def test_record_command_writes_the_ledger_only_for_producers(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    cmd = "python -m ui_clone.scoped_diff tmp/ref/hero"
    assert not scoped_ledger.mark_started("bash forge.sh", base=tmp_path, project_root=tmp_path)
    assert scoped_ledger.mark_started(cmd, base=tmp_path, project_root=tmp_path)
    (ref / "pixel-perfect-diff.json").write_text("{}", encoding="utf-8")
    assert (
        scoped_ledger.record_command("bash forge.sh", base=tmp_path, project_root=tmp_path) is None
    )
    assert not scoped_ledger.ledger_path(ref).exists()
    assert scoped_ledger.record_command(
        cmd, base=tmp_path, project_root=tmp_path
    ) == scoped_ledger.ledger_path(ref)
    assert (
        scoped_ledger.record_command(
            "python -m ui_clone.scoped_diff tmp/ref/missing", base=tmp_path, project_root=tmp_path
        )
        is None
    )


def _post_verify(root: Path, cmd: str) -> str:
    result = run_hook(
        "ui_clone.hooks.post_verify",
        stdin_data=json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
                "tool_response": {"stdout": "", "exit_code": 0},
                "session_id": SESSION,
                "cwd": str(root),
            }
        ),
        env={"CLAUDE_PROJECT_DIR": str(root)},
    )
    assert result.returncode == 0, result.stderr
    return str(result.stdout)


def test_post_verify_hook_records_producer_commands(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    (ref / "pixel-perfect-diff.json").write_text('{"forged": true}', encoding="utf-8")
    _post_verify(tmp_path, "bash forge.sh")
    _post_verify(tmp_path, "python -m ui_clone.scoped_diff tmp/ref/hero && bash forge.sh")
    assert not scoped_ledger.ledger_path(ref).exists()
    scoped_ledger.mark_started(
        "python -m ui_clone.scoped_diff tmp/ref/hero", base=tmp_path, project_root=tmp_path
    )
    (ref / "pixel-perfect-diff.json").write_text('{"produced": true}', encoding="utf-8")
    _post_verify(tmp_path, "python -m ui_clone.scoped_diff tmp/ref/hero")
    data = scoped_ledger.load(ref)
    assert data is not None
    assert data["entries"][0]["producer"] == scoped_ledger.PRODUCER_DIFF
    assert data["entries"][0]["command"] == "python -m ui_clone.scoped_diff tmp/ref/hero"
    # Codex payload shape (top-level command) is recorded the same way.
    scoped_ledger.mark_started(
        f"bash {CAPTURE} clip s http://localhost:5173/ 'section.hero' tmp/ref/hero impl idle",
        base=tmp_path,
        project_root=tmp_path,
    )
    (ref / "frames" / "impl").mkdir(parents=True)
    (ref / "frames" / "impl" / "capture-manifest.json").write_text("{}", encoding="utf-8")
    result = run_hook(
        "ui_clone.hooks.post_verify",
        stdin_data=json.dumps(
            {
                "command": f"bash {CAPTURE} clip s http://localhost:5173/ 'section.hero' tmp/ref/hero impl idle",
                "cwd": str(tmp_path),
            }
        ),
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    data = scoped_ledger.load(ref)
    assert data is not None and data["entries"][-1]["producer"] == scoped_ledger.PRODUCER_CAPTURE
    assert list(data["entries"][-1]["files"]) == ["frames/impl/capture-manifest.json"]


def test_scoped_check_requires_the_ledger(tmp_path: Path) -> None:
    ref = build_scoped_evidence(tmp_path)
    assert scoped_check.check(ref)["failures"] == []
    ledger = scoped_ledger.ledger_path(ref)
    saved = ledger.read_text(encoding="utf-8")
    ledger.unlink()
    result = scoped_check.check(ref)
    assert [f["code"] for f in result["failures"]] == ["evidence-ledger-missing"]
    assert "hooks" in result["failures"][0]["reason"]
    ledger.write_text(saved, encoding="utf-8")
    assert scoped_check.check(ref)["failures"] == []
    # A forge that reproduces the producer's own output byte for byte still
    # fails when no producer command was seen writing it.
    data = produce_diff(ref)
    ledger.write_text(saved, encoding="utf-8")
    write_diff(ref, json.dumps(data, indent=2))
    codes = [f["code"] for f in scoped_check.check(ref)["failures"]]
    assert "evidence-unledgered" in codes


# -- agent-written scripts --------------------------------------------------------


@pytest.mark.parametrize(
    "name, body",
    [
        ("forge.sh", "echo '{}' > tmp/ref/hero/pixel-perfect-diff.json\n"),
        (
            "forge.py",
            "import json\njson.dump({}, open('tmp/ref/hero/frames/impl/capture-manifest.json', 'w'))\n",
        ),
        ("forge.js", "require('fs').writeFileSync('tmp/ref/hero/element-target.json', '{}')\n"),
        ("forge.py", "from ui_clone.scoped_diff import build, write\n"),
        ("forge.py", "import runpy; runpy.run_module('ui_clone.element_capture')\n"),
        ("forge.sh", "python -m ui_clone.element_capture record-clip tmp/ref/hero impl idle\n"),
        ("ledger.sh", "rm tmp/ref/hero/.scoped-evidence-ledger.json\n"),
    ],
)
def test_agent_script_naming_evidence_is_caught(tmp_path: Path, name: str, body: str) -> None:
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    runner = {"sh": "bash", "py": "python3", "js": "node"}[name.rsplit(".", 1)[1]]
    hit = _agent_script_target(f"{runner} {name}", tmp_path, tmp_path)
    assert hit is not None and hit[0] == name
    assert _agent_script_target(f"chmod +x {name} && ./{name}", tmp_path, tmp_path) is not None
    assert _agent_script_target(f"uv run {name}", tmp_path, tmp_path) is not None
    # Reading or editing the script is not running it.
    assert _agent_script_target(f"cat {name}", tmp_path, tmp_path) is None
    assert _agent_script_target(f"grep diff {name}", tmp_path, tmp_path) is None


def test_agent_script_guard_exemptions(tmp_path: Path) -> None:
    benign = tmp_path / "build.sh"
    benign.write_text("npm run build\n", encoding="utf-8")
    assert _agent_script_target("bash build.sh", tmp_path, tmp_path) is None
    # The shipped producers name their own artifacts and stay runnable.
    assert (
        _agent_script_target(
            f"bash {CAPTURE} clip s http://l/ 'a' tmp/ref/hero impl idle", tmp_path, tmp_path
        )
        is None
    )
    assert (
        _agent_script_target(
            f"bash {EVIDENCE} s http://l/ 'a' tmp/ref/hero/element-target.json", tmp_path, tmp_path
        )
        is None
    )
    # Dependency trees are not agent scripts.
    dep = tmp_path / "node_modules" / "x" / "cli.js"
    dep.parent.mkdir(parents=True)
    dep.write_text("fs.writeFileSync('pixel-perfect-diff.json')\n", encoding="utf-8")
    assert _agent_script_target("node node_modules/x/cli.js", tmp_path, tmp_path) is None
    # A missing file, prose, and a checkout of the plugin as the project are untouched.
    assert _agent_script_target("bash missing.sh", tmp_path, tmp_path) is None
    assert (
        _agent_script_target(
            'git commit -m "bash forge.sh wrote pixel-perfect-diff.json"', tmp_path, tmp_path
        )
        is None
    )
    forge = tmp_path / "forge.sh"
    forge.write_text("echo x > tmp/ref/hero/pixel-perfect-diff.json\n", encoding="utf-8")
    assert _agent_script_target(f"bash {forge}", REPO_ROOT, REPO_ROOT) is None


_CHECKOUT_TREES = ("ui_clone", "scripts", "skills", "hooks", "bin")
_RESIDUE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".DS_Store",
)
# generation-plan.sh names generation-plan.json, so a non-exempt verdict denies it.
_ENTRY = Path("scripts/extract/generation-plan.sh")


def _full_checkout(dest: Path) -> Path:
    for tree in _CHECKOUT_TREES:
        if (REPO_ROOT / tree).is_dir():
            shutil.copytree(REPO_ROOT / tree, dest / tree, ignore=_RESIDUE, symlinks=True)
    return dest


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "UI_CLONE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_shipped_script_from_identical_full_checkout_is_exempt(
    tmp_path: Path, isolated_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Skill snippets resolve scripts through the install marker (a dev
    # checkout) while the hook runs from the host's installed copy.
    project = isolated_project
    # The copied trees are seconds old; let the verdict cache treat them as settled.
    monkeypatch.setattr(agent_script, "_CACHE_FRESH_MARGIN_NS", -(10**18))
    checkout = _full_checkout(tmp_path / "dev-checkout")
    cmd = f"bash {checkout / _ENTRY} tmp/ref/hero"
    assert _agent_script_target(cmd, project, project) is None
    # Runtime residue does not break the match; the warm call reuses the cache.
    (checkout / "ui_clone" / "__pycache__").mkdir(exist_ok=True)
    (checkout / "ui_clone" / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\0")
    assert _agent_script_target(cmd, project, project) is None
    cache_file = tmp_path / "cache" / "ui-clone-skills" / "shipped-checkouts.json"
    assert [e["identical"] for e in json.loads(cache_file.read_text()).values()] == [True]

    # Any later write changes the fingerprint: the cached "identical" is not reused.
    with (checkout / _ENTRY).open("a", encoding="utf-8") as fh:
        fh.write("echo '{}' > tmp/ref/hero/generation-plan.json\n")
    assert _agent_script_target(cmd, project, project) is not None
    assert [e["identical"] for e in json.loads(cache_file.read_text()).values()] == [False]


@pytest.mark.parametrize(
    "edit",
    [
        # capture-replay-track.sh runs this sibling from its own tree
        "scripts/extract/capture-replay-track.mjs",
        # shipped scripts put their checkout root on PYTHONPATH
        "ui_clone/state.py",
    ],
)
def test_fake_or_edited_checkout_is_not_exempt(
    tmp_path: Path, isolated_project: Path, edit: str
) -> None:
    project = isolated_project

    # Marker files + a byte-identical entry script is not a trusted checkout.
    fake = tmp_path / "fake-checkout"
    for part in (Path("ui_clone/__init__.py"), Path("hooks/shim.sh"), _ENTRY):
        (fake / part).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / part, fake / part)
    assert _agent_script_target(f"bash {fake / _ENTRY} x", project, project) is not None

    # A full checkout with one edited sibling / module: trusted before, denied after.
    checkout = _full_checkout(tmp_path / "edited-checkout")
    cmd = f"bash {checkout / _ENTRY} x"
    assert _agent_script_target(cmd, project, project) is None
    with (checkout / edit).open("a", encoding="utf-8") as fh:
        fh.write("\n// edited\n" if edit.endswith(".mjs") else "\n# edited\n")
    assert _agent_script_target(cmd, project, project) is not None

    # An extra code file the install does not ship is also a mismatch.
    extra = _full_checkout(tmp_path / "extra-checkout")
    (extra / "scripts" / "extract" / "zz-extra.mjs").write_text("export {}\n", encoding="utf-8")
    assert _agent_script_target(f"bash {extra / _ENTRY} x", project, project) is not None

    # Without the plugin layout the copy is just an agent script.
    loose = tmp_path / "loose" / _ENTRY
    loose.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / _ENTRY, loose)
    assert _agent_script_target(f"bash {loose} tmp/ref/hero", project, project) is not None


def _public_skills() -> list[str]:
    """install.sh CODEX_PUBLIC_SKILLS: the only skills the install projection
    stages (internal ones such as skills/benchmark are never installed)."""
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    m = re.search(r'^CODEX_PUBLIC_SKILLS="([^"]+)"', text, re.MULTILINE)
    assert m is not None
    return m.group(1).split()


def _install_projection(dest: Path) -> Path:
    """The installed plugin tree as install.sh stages it: every shipped tree,
    skills/ holding exactly the public skills."""
    for tree in _CHECKOUT_TREES:
        if tree == "skills" or not (REPO_ROOT / tree).is_dir():
            continue
        shutil.copytree(REPO_ROOT / tree, dest / tree, ignore=_RESIDUE, symlinks=True)
    for skill in _public_skills():
        shutil.copytree(
            REPO_ROOT / "skills" / skill, dest / "skills" / skill, ignore=_RESIDUE, symlinks=True
        )
    return dest


def test_dev_checkout_with_internal_skills_matches_the_real_install(
    tmp_path: Path, isolated_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dev checkout carries internal skills (skills/benchmark) the install
    # omits; the shipped generation-plan.sh must still run from it.
    project = isolated_project
    installed = _install_projection(tmp_path / "installed")
    monkeypatch.setattr(agent_script, "REPO_ROOT", installed)
    checkout = _full_checkout(tmp_path / "dev-checkout")
    internal = sorted(
        p.name for p in (checkout / "skills").iterdir() if p.name not in _public_skills()
    )
    assert internal, "fixture must mirror a checkout with internal skills"
    cmd = f"bash {checkout / _ENTRY} tmp/ref/hero"
    assert _agent_script_target(cmd, project, project) is None
    # A script inside an unshipped skill tree is never exempt.
    forge = checkout / "skills" / internal[0] / "forge.sh"
    forge.write_text("echo x > tmp/ref/hero/generation-plan.json\n", encoding="utf-8")
    assert _agent_script_target(f"bash {forge}", project, project) is not None
    # An edited shipped file still denies.
    with (checkout / "ui_clone" / "state.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# edited\n")
    assert _agent_script_target(cmd, project, project) is not None


def test_cached_verdict_needs_files_older_than_the_margin(
    tmp_path: Path, isolated_project: Path
) -> None:
    # On a 1-second-mtime filesystem a same-second, same-size rewrite keeps the
    # stat fingerprint; a verdict cached that close to the files is not reused.
    project = isolated_project
    checkout = _full_checkout(tmp_path / "coarse-checkout")
    cmd = f"bash {checkout / _ENTRY} x"
    assert _agent_script_target(cmd, project, project) is None
    cache_file = tmp_path / "cache" / "ui-clone-skills" / "shipped-checkouts.json"
    # Freshly written trees: no verdict is stored at all.
    assert not cache_file.exists() or json.loads(cache_file.read_text()) == {}
    # A same-size edit, then an "identical" entry for the new fingerprint
    # stamped within the margin: it is ignored and the full compare denies.
    target = checkout / "ui_clone" / "state.py"
    data = bytearray(target.read_bytes())
    data[-1:] = b"#" if data[-1:] != b"#" else b"\n"
    target.write_bytes(bytes(data))
    installed_root = REPO_ROOT.resolve()
    installed = agent_script._tree_listing(installed_root)
    candidate = agent_script._tree_listing(checkout, agent_script._skill_names(installed_root))
    assert installed is not None and candidate is not None
    newest = max(max(m, c) for _r, _s, m, c in candidate + installed)
    entry = {
        "fingerprint": agent_script._fingerprint(candidate, installed),
        "identical": True,
        "checkedAtNs": newest + 1_000_000_000,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({f"{checkout}\0{installed_root}": entry}), encoding="utf-8")
    assert _agent_script_target(cmd, project, project) is not None


def test_agent_script_calling_the_decision_recorder_is_caught(tmp_path: Path) -> None:
    script = tmp_path / "d.py"
    script.write_text(
        "from pathlib import Path\n"
        "from ui_clone.clonability import record_decision\n"
        "record_decision(Path('tmp/ref/hero'), 'bot-challenge', 'proceed', 'ok',\n"
        "                provenance={'source': 'user-terminal'})\n",
        encoding="utf-8",
    )
    assert _agent_script_target("python3 d.py", tmp_path, tmp_path) == ("d.py", "record_decision")
    script.write_text(
        "import ui_clone.clonability as c\nc.apply_prompt_decisions('.', 's', 'x')\n",
        encoding="utf-8",
    )
    assert _agent_script_target("python3 d.py", tmp_path, tmp_path) is not None


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


def test_pre_bash_denies_running_a_forge_script(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    (ref / "frames" / "impl").mkdir(parents=True)
    forge = tmp_path / "forge.sh"
    forge.write_text(
        'echo \'{"result":"pass"}\' > tmp/ref/hero/pixel-perfect-diff.json\n', encoding="utf-8"
    )
    out = _pre_bash(tmp_path, "bash forge.sh")
    assert "outside the plugin" in out and "evidence-unledgered" in out
    assert "python -m ui_clone.scoped_diff <ref-dir>" in out
    assert "outside the plugin" in _pre_bash(tmp_path, "chmod +x forge.sh && ./forge.sh")
    # The shipped producer copied into the project still runs (its content is the plugin's),
    # but the ledger will not accept its output because it is not the shipped path/hash.
    local = tmp_path / "scripts" / "element-state-capture.sh"
    local.parent.mkdir()
    shutil.copy(CAPTURE, local)
    assert "outside the plugin" in _pre_bash(
        tmp_path,
        "bash scripts/element-state-capture.sh clip s http://l/ 'a' tmp/ref/hero impl idle",
    )
    assert "⛔" not in _pre_bash(
        tmp_path, f"bash {CAPTURE} clip s http://localhost:5173/ 'a' tmp/ref/hero impl idle"
    )
    assert "⛔" not in _pre_bash(tmp_path, "python -m ui_clone.scoped_diff tmp/ref/hero")


def test_failed_producer_run_does_not_ledger_a_pre_existing_file(tmp_path: Path) -> None:
    """A producer that fails before writing must not ledger whatever evidence
    file already sat at its output path (e.g. one forged earlier)."""
    import os
    import time

    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    forged = ref / "pixel-perfect-diff.json"
    forged.write_text('{"forged": true}', encoding="utf-8")
    old = time.time() - 60
    os.utime(forged, (old, old))
    cmd = "python -m ui_clone.scoped_diff tmp/ref/hero"
    _pre_bash(tmp_path, cmd)
    _post_verify(tmp_path, cmd)  # the run failed and never rewrote the file
    assert not scoped_ledger.ledger_path(ref).exists()


def test_post_without_pre_stamp_does_not_ledger(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    (ref / "pixel-perfect-diff.json").write_text("{}", encoding="utf-8")
    _post_verify(tmp_path, "python -m ui_clone.scoped_diff tmp/ref/hero")
    assert not scoped_ledger.ledger_path(ref).exists()


def test_pre_and_post_hooks_ledger_a_fresh_producer_output(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    cmd = "python -m ui_clone.scoped_diff tmp/ref/hero"
    _pre_bash(tmp_path, cmd)
    (ref / "pixel-perfect-diff.json").write_text('{"produced": true}', encoding="utf-8")
    _post_verify(tmp_path, cmd)
    data = scoped_ledger.load(ref)
    assert data is not None and data["entries"][-1]["command"] == cmd
    assert cmd not in scoped_ledger.pending_path(tmp_path).read_text(encoding="utf-8")


def test_pending_ledger_file_is_protected() -> None:
    from ui_clone.hooks.pre_bash_rules.bash_write import _bash_enforcement_state_target

    assert _bash_enforcement_state_target("echo '{}' > tmp/ref/.scoped-ledger-pending.json")
