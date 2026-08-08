"""F8: transition-trajectory-compare structural-motion selector must come from
config, not be hardcoded to one site's DOM (.patch[parallax="patch"], ebpb only).

Structural mode is a GENERAL mechanism — any ref whose asset-substitution.json
declares structuralOnlySections (with sections passing) triggers it. With the
selector hardcoded, every OTHER site that opted in sampled zero targets, the
probe declared itself vacuous and exited 1, and video-motion-compare early-exited
so the authoritative 60fps SSIM pass never ran. The selector must be resolvable
from an env override or asset-substitution.json, defaulting to the ebpb selector.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "transition-trajectory-compare.sh"

FAKE_AB = """#!/usr/bin/env bash
# Minimal agent-browser stub: signature probes -> empty set; height -> scrollable;
# viewport assert -> requested dims. querySelectorAll is matched FIRST because the
# signature JS also references window.innerHeight.
case "$*" in
  *querySelectorAll*) echo "[]" ;;
  *scrollHeight*) echo "5000" ;;
  *innerWidth*) echo "1440" ;;
  *innerHeight*) echo "900" ;;
  *) echo "0" ;;
esac
exit 0
"""

FAKE_MAGICK = "#!/usr/bin/env bash\nexit 0\n"

RESULT_TXT = "**Result: 3 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**\n"


def _run(tmp_path: Path, asset_sub: str, env_selector: str | None = None) -> str:
    ref = tmp_path / "ref"
    (ref / "sections").mkdir(parents=True)
    (ref / "asset-substitution.json").write_text(asset_sub, encoding="utf-8")
    (ref / "sections" / "result.txt").write_text(RESULT_TXT, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "agent-browser").write_text(FAKE_AB, encoding="utf-8")
    (bin_dir / "magick").write_text(FAKE_MAGICK, encoding="utf-8")
    (bin_dir / "agent-browser").chmod(0o755)
    (bin_dir / "magick").chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["WAIT_REF"] = "0"
    env["WAIT_IMPL"] = "0"
    env["WAIT_SCROLL_SETTLE_MS"] = "0"
    if env_selector is not None:
        env["STRUCTURAL_TRAJECTORY_SELECTOR"] = env_selector

    subprocess.run(
        ["bash", str(SCRIPT), "http://ref.invalid", "http://impl.invalid", "sess", str(ref)],
        capture_output=True, text=True, timeout=60, env=env, check=False,
    )
    report = ref / "transitions" / "trajectory-result.txt"
    return report.read_text(encoding="utf-8") if report.is_file() else ""


def test_selector_read_from_asset_substitution(tmp_path: Path) -> None:
    out = _run(tmp_path, '{"structuralOnlySections": ["hero"], '
                         '"structuralMotionSelector": "[data-parallax]"}')
    assert "# mode: structural-motion" in out, out
    assert "structural-motion selector: [data-parallax]" in out, (
        "structural probe must use the configured selector, not the ebpb default; "
        f"report:\n{out}"
    )


def test_env_override_wins(tmp_path: Path) -> None:
    out = _run(tmp_path,
               '{"structuralOnlySections": ["hero"], "structuralMotionSelector": "[data-parallax]"}',
               env_selector=".reveal-block")
    assert "structural-motion selector: .reveal-block" in out, out


def test_defaults_to_ebpb_selector_for_backcompat(tmp_path: Path) -> None:
    out = _run(tmp_path, '{"structuralOnlySections": ["hero"]}')
    assert 'structural-motion selector: .patch[parallax="patch"]' in out, (
        f"absent config must preserve the historical ebpb selector; report:\n{out}"
    )
