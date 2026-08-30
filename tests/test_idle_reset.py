"""P0·1 — idle-state reset preamble + capturedIdle provenance.

Covers the keystone contract: ONE sourced shared helper
(skills/visual-debug/scripts/lib/idle-reset.sh) is used by BOTH ground-truth
producers (extract-section-map.sh, extract-dom.sh) so they cannot drift, the
helper resets the page to idle before any rect/style read, and both producers
record a `capturedIdle` provenance object on their artifact. section-compare.sh
advisory-warns when the frozen-ref section-map.json it reuses was not idle.

The behavioural tests stub `agent-browser` with a fake on PATH so they exercise
the real shell helper + python provenance builder without a browser.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "visual-debug" / "scripts"
HELPER = SCRIPTS / "lib" / "idle-reset.sh"
EXTRACT_SECTION_MAP = SCRIPTS / "extract-section-map.sh"
EXTRACT_DOM = SCRIPTS / "extract-dom.sh"
SECTION_COMPARE = SCRIPTS / "section-compare.sh"

# These scripts embed JS heredocs that macOS /bin/bash 3.2 mis-lexes; the repo
# runs them under bash 4+ (see review.sh shell-syntax check). Resolve a 4+ bash.
_BASH4_CANDIDATES = ["/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash") or "bash"]


def _bash4() -> str | None:
    for cand in _BASH4_CANDIDATES:
        if not cand or not os.path.exists(cand) and not shutil.which(cand):
            continue
        try:
            out = subprocess.run(
                [cand, "-c", "echo ${BASH_VERSINFO[0]}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.stdout.strip().isdigit() and int(out.stdout.strip()) >= 4:
            return cand
    return None


BASH4 = _bash4()
requires_bash4 = pytest.mark.skipif(BASH4 is None, reason="bash 4+ not available")


def _write_fake_agent_browser(bindir: Path) -> None:
    """A fake `agent-browser` that answers the idle-reset eval, the section-map
    enumeration eval, and the extract-dom eval by sniffing the eval JS."""
    script = bindir / "agent-browser"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'stdin="$(cat 2>/dev/null || true)"\n'
        'args="$* $stdin"\n'
        "if printf '%s' \"$args\" | grep -q 'openStateMatches'; then\n"
        # double-encoded JSON string, mirroring real agent-browser
        '  printf \'%s\' \'"{\\"scrollY\\":0,\\"openStateMatches\\":[],\\"idle\\":true}"\'\n'
        "elif printf '%s' \"$args\" | grep -q 'semanticTags'; then\n"
        '  printf \'%s\' \'"{\\"totalCount\\":2,\\"sections\\":[{\\"index\\":0,\\"tag\\":\\"section\\"}],\\"hasFooter\\":false}"\'\n'
        "elif printf '%s' \"$args\" | grep -q 'querySelector'; then\n"
        '  printf \'%s\' \'"{\\"tag\\":\\"main\\",\\"class\\":\\"\\",\\"children\\":[]}"\'\n'
        "else\n"
        "  printf '%s' '{}'\n"
        "fi\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _env_with_fake(bindir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    return env


# ── Contract: single sourced helper, no drift ─────────────────────────────


def test_helper_exists_and_defines_contract() -> None:
    text = HELPER.read_text()
    assert "ab_idle_reset()" in text, "shared helper must define ab_idle_reset"
    assert "IDLE_RESET_JS" in text
    # The reset: scroll top + close hover/open states + rAF settle + open assert.
    assert "scrollTo(0, 0)" in text
    assert "mouseleave" in text
    assert "requestAnimationFrame" in text
    assert "openStateMatches" in text
    assert "aria-expanded" in text


@pytest.mark.parametrize("producer", [EXTRACT_SECTION_MAP, EXTRACT_DOM])
def test_both_producers_source_and_call_the_one_helper(producer: Path) -> None:
    text = producer.read_text()
    assert "lib/idle-reset.sh" in text, f"{producer.name} must source the shared helper"
    assert "ab_idle_reset" in text, f"{producer.name} must call ab_idle_reset before reads"
    assert "UI_CLONE_CAPTURED_IDLE" in text, f"{producer.name} must embed capturedIdle provenance"


def test_section_compare_checks_provenance() -> None:
    text = SECTION_COMPARE.read_text()
    assert "lib/idle-reset.sh" in text
    assert "capturedIdle" in text
    assert "NOT captured idle" in text


# ── Behaviour: ab_idle_reset provenance + advisory warn ───────────────────


@requires_bash4
def test_ab_idle_reset_idle(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_fake_agent_browser(bindir)
    assert BASH4 is not None
    res = subprocess.run(
        [BASH4, "-c", f'. "{HELPER}"; ab_idle_reset sess'],
        capture_output=True,
        text=True,
        env=_env_with_fake(bindir),
        timeout=30,
    )
    prov = json.loads(res.stdout)
    assert prov["reset"] is True
    assert prov["idle"] is True
    assert prov["openStateMatches"] == []
    assert "ADVISORY" not in res.stderr  # idle → no warning


@requires_bash4
def test_ab_idle_reset_non_idle_warns(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # fake that reports a residual open megamenu + non-top scroll
    (bindir / "agent-browser").write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\' \'"{\\"scrollY\\":340,\\"openStateMatches\\":[{\\"selector\\":\\".is-open\\",\\"count\\":1}],\\"idle\\":false}"\'\n'
    )
    (bindir / "agent-browser").chmod(0o755)
    assert BASH4 is not None
    res = subprocess.run(
        [BASH4, "-c", f'. "{HELPER}"; ab_idle_reset sess'],
        capture_output=True,
        text=True,
        env=_env_with_fake(bindir),
        timeout=30,
    )
    prov = json.loads(res.stdout)
    assert prov["reset"] is True
    assert prov["idle"] is False
    assert "ADVISORY" in res.stderr  # non-idle → advisory warn (never aborts)
    assert res.returncode == 0


@requires_bash4
def test_ab_idle_reset_unreachable_records_reset_false(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "agent-browser").write_text("#!/usr/bin/env bash\nexit 1\n")
    (bindir / "agent-browser").chmod(0o755)
    assert BASH4 is not None
    res = subprocess.run(
        [BASH4, "-c", f'. "{HELPER}"; ab_idle_reset sess'],
        capture_output=True,
        text=True,
        env=_env_with_fake(bindir),
        timeout=30,
    )
    prov = json.loads(res.stdout)
    assert prov["reset"] is False
    assert prov["idle"] is None
    assert res.returncode == 0


# ── End-to-end: producers embed capturedIdle on their artifacts ───────────


@requires_bash4
def test_extract_section_map_embeds_captured_idle(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_fake_agent_browser(bindir)
    ref = tmp_path / "ref"
    ref.mkdir()
    assert BASH4 is not None
    res = subprocess.run(
        [BASH4, str(EXTRACT_SECTION_MAP), str(ref), "sess"],
        capture_output=True,
        text=True,
        env=_env_with_fake(bindir),
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads((ref / "section-map.json").read_text())
    assert data["totalCount"] == 2  # producer output preserved
    assert data["capturedIdle"]["idle"] is True
    assert data["capturedIdle"]["helper"] == "idle-reset.sh"


@requires_bash4
def test_extract_dom_embeds_captured_idle(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_fake_agent_browser(bindir)
    ref = tmp_path / "ref"
    ref.mkdir()
    assert BASH4 is not None
    res = subprocess.run(
        [BASH4, str(EXTRACT_DOM), str(ref), "sess", "main"],
        capture_output=True,
        text=True,
        env=_env_with_fake(bindir),
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads((ref / "structure.json").read_text())
    assert data["tag"] == "main"  # producer output preserved
    assert data["capturedIdle"]["idle"] is True
    assert data["capturedIdle"]["helper"] == "idle-reset.sh"
