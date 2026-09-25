"""Focused tests for the Python-backed review checks."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS_PATH = ROOT / "scripts" / "ci" / "review_checks.py"
SPEC = importlib.util.spec_from_file_location("review_checks", CHECKS_PATH)
assert SPEC is not None and SPEC.loader is not None
review_checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_checks)

UNIVERSALITY_PATH = ROOT / "scripts" / "ci" / "check_universality.py"
UNIVERSALITY_SPEC = importlib.util.spec_from_file_location("check_universality", UNIVERSALITY_PATH)
assert UNIVERSALITY_SPEC is not None and UNIVERSALITY_SPEC.loader is not None
check_universality = importlib.util.module_from_spec(UNIVERSALITY_SPEC)
# Register before exec: its dataclasses resolve string annotations via sys.modules.
sys.modules[UNIVERSALITY_SPEC.name] = check_universality
UNIVERSALITY_SPEC.loader.exec_module(check_universality)


def _scan(root: Path, relative: str, content: str) -> dict[str, list[str]]:
    """Write one file into a fresh tree under root and return the universality hits by rule label."""
    tree = Path(tempfile.mkdtemp(prefix="tree-", dir=root))
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {label: hits for label, hits in check_universality.find_hits(tree).items() if hits}


def _labels(hits: dict[str, list[str]]) -> set[str]:
    return set(hits)


def _label(prefix: str) -> str:
    matches = [rule.label for rule in check_universality.RULES if rule.label.startswith(prefix)]
    assert len(matches) == 1, f"rule prefix {prefix!r} matched {matches}"
    return str(matches[0])


def _write_public_skills(root: Path, contents: dict[str, str]) -> None:
    for skill in review_checks.PUBLIC_SKILLS:
        skill_dir = root / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(contents.get(skill, "entry\n"), encoding="utf-8")


def test_public_skill_sizes_report_words_lines_and_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert review_checks.COMMANDS["skill-context"] is review_checks.report_public_skill_sizes
    _write_public_skills(
        tmp_path,
        {
            "ui-capture": "one two\nthree\n",
            "ui-reverse-engineering": "four\n",
            "visual-debug": "five six\n",
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_checks, "PUBLIC_SKILL_WORD_ADVISORY", 5)

    assert review_checks.report_public_skill_sizes() == 0
    output = capsys.readouterr().out
    assert "ui-capture: 3 words, 2 lines" in output
    assert "TOTAL: 6 words, 4 lines" in output
    assert "WARNING: public skill entrypoints total 6 words (advisory 5)" in output


def test_public_skill_link_check_accepts_existing_relative_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_public_skills(
        tmp_path,
        {"ui-capture": "Read [setup](setup.md#browser) and [peer](../visual-debug/SKILL.md).\n"},
    )
    (tmp_path / "skills" / "ui-capture" / "setup.md").write_text("ok\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert review_checks.check_public_skill_links() == 0


def test_public_skill_link_check_rejects_missing_literal_markdown_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_public_skills(
        tmp_path,
        {"visual-debug": "Read [missing](guides/missing.md#details).\n"},
    )
    monkeypatch.chdir(tmp_path)

    assert review_checks.check_public_skill_links() == 1
    assert (
        "skills/visual-debug/SKILL.md: broken local Markdown link: guides/missing.md"
        in capsys.readouterr().err
    )


# ─── check_universality.py: maintainer-bias rules ───────────────────────────


def test_universality_flags_personal_documents_folder(tmp_path: Path) -> None:
    hits = _scan(tmp_path, "skills/x/SKILL.md", "cd ~/Documents/my-monorepo/apps/web\n")
    assert _label("Personal home folders") in _labels(hits)
    hits = _scan(tmp_path, "scripts/y.sh", 'ls "$HOME/Documents/side-project"\n')
    assert _label("Personal home folders") in _labels(hits)


def test_universality_flags_multiplexer_and_workspace_ids(tmp_path: Path) -> None:
    label = _label("Terminal multiplexer")
    assert label in _labels(_scan(tmp_path, "scripts/a.sh", "purplemux tab create -w x\n"))
    assert label in _labels(_scan(tmp_path, "scripts/b.sh", "# open a cmux pane\n"))
    assert label in _labels(_scan(tmp_path, "scripts/c.sh", "WS=ws-AbC123\n"))
    assert label in _labels(_scan(tmp_path, "scripts/c2.sh", "# tab in ws-9ZkQ1a\n"))
    # Ordinary words / longer tokens are not workspace ids.
    assert label not in _labels(_scan(tmp_path, "scripts/d.sh", "rows-abcdef ws-abc ws-toolong1\n"))
    # Six-letter hyphenated vocabulary has no digit / no uppercase: not an id.
    for ok in ("import ws-client\n", "ws-server restart\n", "ws-socket ws-broker ws-abc123 ws-ABCDEF\n"):
        assert label not in _labels(_scan(tmp_path, "scripts/e.sh", ok)), ok


def test_universality_flags_personal_project_name_outside_internal(tmp_path: Path) -> None:
    label = _label("Personal project names")
    assert label in _labels(_scan(tmp_path, "ui_clone/loop.py", "# default: the OnPixel showcase\n"))
    # internal/ is gitignored maintainer scratch and excluded from the scan.
    assert label not in _labels(_scan(tmp_path, "internal/onpixel/loop.py", "# onpixel loop\n"))
    # No path allowlist: CI wiring must not reference the personal project either.
    assert label in _labels(
        _scan(tmp_path, "pyproject.toml", 'testpaths = ["tests", "internal/onpixel/tests"]\n')
    )
    assert label in _labels(
        _scan(tmp_path, "ui_clone/bypass.py", "# the OnPixel loop lives in internal/onpixel\n")
    )


def test_universality_flags_lab_batch_labels(tmp_path: Path) -> None:
    label = _label("Lab batch labels")
    assert label in _labels(_scan(tmp_path, "ui_clone/a.py", "# fix from batch-4 item 1\n"))
    assert label in _labels(_scan(tmp_path, "scripts/b.sh", "# tools-batch-11 ITEM 5\n"))
    assert label not in _labels(_scan(tmp_path, "ui_clone/c.py", "# batch size 4, batch-11 hardening\n"))


def test_universality_flags_benchmark_site_name_in_any_form(tmp_path: Path) -> None:
    label = _label("Benchmark site names")
    for text in (
        "# realfood's card_bg collapsed into its sibling\n",
        "# measured on realfood-v2 / RealFood hero\n",
        'url = "https://realfood.gov/"\n',
        "# ref dir: tmp/ref/realfood-e2e-9\n",
    ):
        assert label in _labels(_scan(tmp_path, "ui_clone/a.py", text)), text
    assert label not in _labels(_scan(tmp_path, "ui_clone/b.py", "# one observed site's card_bg\n"))
    # Fixtures under tests/ are exempt.
    assert label not in _labels(_scan(tmp_path, "tests/test_x.py", 'ref = "tmp/ref/realfood"\n'))


def test_universality_flags_end_to_end_run_identifiers(tmp_path: Path) -> None:
    loop_label = _label("Maintainer end-to-end run identifiers")
    bare_label = _label("Maintainer end-to-end run labels")
    hits = _labels(_scan(tmp_path, "ui_clone/a.py", "# Fix M (loop-e2e-6): bar-grow reveals\n"))
    assert loop_label in hits
    assert bare_label not in hits  # the prefixed form is reported once, not twice
    hits = _labels(_scan(tmp_path, "skills/x/scripts/y.sh", "# e2e-12: no stickyRangeH field\n"))
    assert bare_label in hits
    assert loop_label not in hits
    assert bare_label in _labels(_scan(tmp_path, "docs/design.md", "seen on site-e2e-7 corpus\n"))
    assert bare_label in _labels(_scan(tmp_path, "docs/design.md", "run e2e-3 again\n"))
    for ok in (
        "# an end-to-end run exposed this\n",
        "# e2e tests / e2e-suite / loop-e2e\n",
        # Generic Playwright / E2E vocabulary: path segments, long ids, chains.
        "specs live in tests/e2e-3/ and tests/e2e-3/smoke\n",
        "the e2e-2024 runner and e2e-3-runner\n",
        "see ./e2e-3 or e2e-3.config.ts\n",
    ):
        assert not (_labels(_scan(tmp_path, "ui_clone/c.py", ok)) & {loop_label, bare_label}), ok


def test_universality_flags_brand_corpus_and_dated_lab_notes(tmp_path: Path) -> None:
    brand = _label("Brand / company leakage")
    corpus = _label("Benchmark site names")
    dated = _label("Dated lab notes")
    assert brand in _labels(_scan(tmp_path, "ui_clone/a.py", "# never ships it (navercorp).\n"))
    assert brand in _labels(_scan(tmp_path, "scripts/a.sh", "# NaverCorp esg-sustainability run\n"))
    assert corpus in _labels(_scan(tmp_path, "skills/x/scripts/a.sh", "// ebay-playbook mounts 6 divs\n"))
    for text in (
        "# Sourced from the 26-site loop observation\n",
        '"complete-but-feels-dead clones in the 26-site loop."\n',
        "# Fix 2 (review 2026-05-27): partial-capture detector\n",
        "# (F — claude fidelity analysis 2026-05-25)\n",
        "# Attestation contract (audit 2026-05-25 item [2])\n",
        "# fable-20260910 follow-up review (LOW): a declared input\n",
    ):
        assert dated in _labels(_scan(tmp_path, "ui_clone/b.py", text)), text
    for ok in (
        "# clones across observed sites\n",
        "# Fix 2 (review): partial-capture detector\n",
        "# reviewed on 2026-05-27 by the site loop\n",
        "# fable condition 2 (2026-07-18)\n",
    ):
        assert not (_labels(_scan(tmp_path, "ui_clone/c.py", ok)) & {brand, corpus, dated}), ok
    # Historical record and maintainer-only trees stay exempt.
    for relative in ("CHANGELOG.md", "internal/x/a.py", "benchmark/a.md", "tests/test_a.py"):
        assert not _labels(_scan(tmp_path, relative, "# fable-20260910: 26-site loop navercorp\n")), relative


def test_universality_flags_bare_date_stamps_in_code(tmp_path: Path) -> None:
    dated_code = _label("Bare date stamps in code")
    for text in (
        "# 2026-05-22 SKILL.md Tier 3 rule:\n",
        "# Review follow-up 2026-05-22 (Q1): per-node styles win\n",
        "# orchestrator session in live use (2026-06-12)\n",
        "# every run since 2026-0X-XX read all saturated\n",
        "# ROOT CAUSE (2026-07, empirical): QuantumRange inflation\n",
        '    """2026-05-22 user request: state machine extends beyond <header>.\n',
    ):
        for relative in ("ui_clone/a.py", "scripts/a.sh", "skills/x/scripts/a.sh", "hooks/a.json"):
            assert dated_code in _labels(_scan(tmp_path, relative, text)), (relative, text)
    for ok in (
        "# version 7.1.2-27 returns AE as count * 65535\n",
        "# the port is 2026-5\n",
        "# ISO timestamp fixtures look like 2026-05-14T00:00:00Z\n",
        "# id sha-2026-05-2a\n",
        "# range 12026-05-22\n",
    ):
        assert dated_code not in _labels(_scan(tmp_path, "ui_clone/c.py", ok)), ok
    # Markdown keeps only the narrower dated rule; historical / local trees are exempt.
    assert dated_code not in _labels(_scan(tmp_path, "docs/design.md", "Reviewed 2026-05-22.\n"))
    for relative in ("CHANGELOG.md", "internal/x/a.py", "benchmark/a.sh", "tests/test_a.py"):
        assert not _labels(_scan(tmp_path, relative, "# measured 2026-05-22\n")), relative
    # Data dates in eval / fixture / manifest data are not lab notes.
    data = '{"id": 1, "captured": "2026-09-24", "since": "2026-09"}\n'
    for relative in (
        "skills/x/evals/evals.json",
        "skills/x/evals/fixtures/run.json",
        ".claude-plugin/plugin.json",
        "ui_clone/data/fixture.json",
    ):
        assert dated_code not in _labels(_scan(tmp_path, relative, data)), relative
    for relative in ("pyproject.toml", ".github/workflows/ci.yml"):
        assert dated_code not in _labels(_scan(tmp_path, relative, "date = 2026-09-24\n"))


def test_universality_codex_state_allows_host_config_paths_only(tmp_path: Path) -> None:
    label = _label("Personal Codex state")
    for allowed in check_universality.CODEX_HOST_CONFIG_PATHS:
        assert label not in _labels(_scan(tmp_path, "scripts/ok.sh", f"# see {allowed}\n")), allowed
    assert label in _labels(_scan(tmp_path, "scripts/bad.sh", "# see ~/.codex/sessions/2026/abc.jsonl\n"))
    # An allowed path appended to the line does not launder the personal one.
    for bypass in (
        "cat ~/.codex/sessions/2026/abc.jsonl  # ~/.codex/config.toml\n",
        "# ~/.codex/config.toml and ~/.codex/sessions/2026/abc.jsonl\n",
    ):
        assert label in _labels(_scan(tmp_path, "scripts/bypass.sh", bypass)), bypass


def test_universality_owner_url_is_behavior_only_in_hooks_scripts_package(tmp_path: Path) -> None:
    label = _label("Repository owner used as behavior")
    url = "https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh"
    assert label in _labels(_scan(tmp_path, "scripts/hooks/refresh.sh", f"curl -LsSf {url}\n"))
    assert label in _labels(_scan(tmp_path, "hooks/x.sh", f"curl {url}\n"))
    assert label in _labels(
        _scan(tmp_path, "ui_clone/y.py", 'URL = "https://github.com/voidmatcha/ui-clone-skills"\n')
    )
    # The documented env default is the one permitted occurrence, in either form.
    for ok in (
        'REPO="${UI_CLONE_REPO:-https://github.com/voidmatcha/ui-clone-skills.git}"\n',
        'UI_CLONE_REPO_DEFAULT="https://github.com/voidmatcha/ui-clone-skills.git"  # UI_CLONE_REPO overrides\n',
    ):
        assert label not in _labels(_scan(tmp_path, "scripts/hooks/ok.sh", ok)), ok
    # Naming the env var elsewhere on the line is not the env default: the
    # allow covers only a URL inside the default expression itself.
    for bypass in (
        f"curl -LsSf {url}  # UI_CLONE_REPO\n",
        f'REPO="${{UI_CLONE_REPO:-}}"; curl {url}\n',
        f'UI_CLONE_REPO_DEFAULT="x"; curl {url}\n',
    ):
        assert label in _labels(_scan(tmp_path, "scripts/hooks/bypass.sh", bypass)), bypass
    # Attribution surfaces (README, manifests, install docs, skills) are out of scope.
    for relative in ("README.md", "install.sh", ".codex-plugin/plugin.json", "skills/x/SKILL.md"):
        assert label not in _labels(_scan(tmp_path, relative, f"{url}\n")), relative


def test_universality_scans_host_manifest_dirs_and_docs(tmp_path: Path) -> None:
    label = _label("Personal home folders")
    for relative in (
        ".claude-plugin/agents/helper.md",
        ".codex/agents/helper.toml",
        ".codex-plugin/plugin.json",
        "docs/design.md",
    ):
        assert label in _labels(_scan(tmp_path, relative, "path: ~/Documents/private/\n")), relative
    for excluded in ("internal", "outbox", ".claude", "tests"):
        assert excluded in check_universality.EXCLUDED_DIRS
    for scanned in (".claude-plugin", ".codex", ".codex-plugin", "docs"):
        assert scanned not in check_universality.EXCLUDED_DIRS


def test_universality_repo_passes_current_rules() -> None:
    """The live tree must satisfy every rule; a new rule is only added once it does."""
    hits = {label: h for label, h in check_universality.find_hits(ROOT).items() if h}
    assert hits == {}, hits
