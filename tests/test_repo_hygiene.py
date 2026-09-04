from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_distributed_readme_links_and_capture_contracts_are_packaged_consistently() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "README_detail/" in package["files"]

    skill = (ROOT / "skills" / "ui-capture" / "SKILL.md").read_text(encoding="utf-8")
    assert '"**/tmp/ref/**/clip/**"' in skill
    assert '"**/tmp/ref/**/clips/**"' not in skill

    evals = json.loads(
        (ROOT / "skills" / "ui-capture" / "evals" / "evals.json").read_text(
            encoding="utf-8"
        )
    )["evals"]
    static_state_evals = {entry["id"]: entry for entry in evals if entry["id"] in {3, 12, 13}}
    assert set(static_state_evals) == {3, 12, 13}
    for entry in static_state_evals.values():
        contract = " ".join([entry["expected_output"], *entry["expectations"]]).lower()
        assert "png" in contract
        assert "video" not in contract


def test_node_runtime_contract_matches_playwright_requirement() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    install = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert package["engines"]["node"] == ">=20"
    assert lock["packages"][""]["engines"]["node"] == ">=20"
    assert lock["packages"]["node_modules/playwright-core"]["engines"]["node"] == ">=20"
    assert "Node.js 20+ and npm are required" in install
    assert 'node_major" -lt 20' in install


def test_github_ci_uses_ci_local_as_single_source_of_truth() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "bash scripts/ci/ci-local.sh --quiet" in workflow, (
        "GitHub CI must call ci-local.sh so local and remote gates cannot drift "
        "when ci-local adds checks such as universality or drift-parity."
    )


def test_github_ci_bootstraps_media_tools_before_running_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count("- name: Install media tools") == 1
    install_at = workflow.index("sudo apt-get install")
    test_at = workflow.index("bash scripts/ci/ci-local.sh --quiet")
    assert install_at < test_at
    assert "ffmpeg" in workflow[install_at:test_at]
    assert "imagemagick" in workflow[install_at:test_at]
    assert "command -v magick" in workflow[install_at:test_at], (
        "Ubuntu may package ImageMagick 6 without the ImageMagick 7 `magick` entrypoint; "
        "CI must provide the same command surface exercised by the suite."
    )


def test_github_ci_uses_node24_action_majors() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v4" not in workflow
    assert "astral-sh/setup-uv@v4" not in workflow
    # setup-node v4 runs on the node20 runtime; v7 is the node24 line.
    assert "actions/setup-node@v4" not in workflow

    # Assert the intent — every usage is pinned — rather than a job count, so
    # adding a job does not require editing a magic number here.
    for action, pinned in (
        ("actions/checkout@", "actions/checkout@v7.0.1"),
        ("astral-sh/setup-uv@", "astral-sh/setup-uv@v10.0.1"),
        ("actions/setup-node@", "actions/setup-node@v7.0.0"),
    ):
        total = workflow.count(action)
        assert total > 0, f"{action} disappeared from the workflow"
        assert workflow.count(pinned) == total, (
            f"every {action} usage must be pinned to {pinned}: "
            f"{total} usages, {workflow.count(pinned)} pinned"
        )


def test_docs_directory_is_not_globally_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    active_lines = [line.strip() for line in gitignore.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    assert "docs/" not in active_lines and "/docs/" not in active_lines, (
        "docs/ contains tracked project docs; ignoring the whole directory silently "
        "drops future docs from review/commit visibility. Ignore only generated subpaths."
    )


def test_install_script_does_not_pipe_remote_installer_directly_to_shell() -> None:
    install = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert not re.search(r"curl\b[^\n|]*\|\s*(?:sh|bash)\b", install), (
        "install.sh should download remote installers to a temp file first so the "
        "operator can see the source URL and the script avoids opaque curl|sh execution."
    )


def test_local_docs_artifact_patterns_are_ignored_without_hiding_all_docs() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    active_lines = [line.strip() for line in gitignore.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    assert "docs/" not in active_lines and "/docs/" not in active_lines
    assert "docs/clone-reviews/" in active_lines
    assert "docs/*-E1.md" in active_lines


def test_tokensave_runtime_state_is_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    active_lines = [
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert ".tokensave/" in active_lines


def test_runtime_install_guidance_avoids_curl_pipe_shell() -> None:
    checked_paths = [
        ROOT / "README.md",
        ROOT / "README_detail" / "install.md",
        ROOT / "install.sh",
        ROOT / "hooks" / "shim.sh",
        ROOT / "skills" / "ui-reverse-engineering" / "SKILL.md",
        ROOT / "skills" / "ui-capture" / "SKILL.md",
        ROOT / "skills" / "visual-debug" / "SKILL.md",
    ]
    offenders = []
    pattern = re.compile(r"curl\b[^\n|]*\|\s*(?:sh|bash)\b")
    for path in checked_paths:
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, "curl|shell installer guidance found in: " + ", ".join(offenders)


def test_shell_entrypoints_parse() -> None:
    # A hooks/shim.sh that does not parse is not a local annoyance. Codex blocks a
    # tool call when a hook exits 2 with non-empty stderr, and a bash syntax error
    # is exactly that, so every tool call in every Codex session in every project
    # fails with the parse error as its reason until the file is fixed. Nothing
    # else in the suite or in ci-local.sh checks shell syntax, so a broken save
    # reached the working tree with no gate in front of it.
    targets = sorted((ROOT / "hooks").glob("*.sh")) + [ROOT / "install.sh"]
    broken = []
    for path in targets:
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            broken.append(f"{path.relative_to(ROOT)}: {result.stderr.strip()}")

    assert not broken, "shell files fail to parse: " + "; ".join(broken)
