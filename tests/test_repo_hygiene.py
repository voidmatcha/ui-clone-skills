from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_github_ci_uses_ci_local_as_single_source_of_truth() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "bash scripts/ci/ci-local.sh --quiet" in workflow, (
        "GitHub CI must call ci-local.sh so local and remote gates cannot drift "
        "when ci-local adds checks such as universality or drift-parity."
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
