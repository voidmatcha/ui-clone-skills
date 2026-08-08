"""D4 (loop-nvti-0): find-impl-root.sh must honor the `.ref-dir` backlink
handshake on convention/heuristic candidates — an impl tree whose backlink
resolves to a DIFFERENT ref dir belongs to another site's run and must never
resolve for this ref (the false state-coverage PASS against stale eBay
leftovers, followed by the new run clobbering that tree)."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "find-impl-root.sh"


def _mk_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    ref = repo / "tmp" / "ref" / "nvti"
    ref.mkdir(parents=True)
    impl = repo / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text("{}", encoding="utf-8")
    (impl / "src" / "App.tsx").write_text("export default () => null\n", encoding="utf-8")
    return repo, ref, impl


def _resolve(ref: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(ref.parents[3])},
    )




def test_find_impl_root_shell_wrapper_avoids_large_python_heredoc() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "<<'PY'" not in text
    assert (SCRIPT.with_name("find_impl_root.py")).is_file()
def test_foreign_backlink_impl_never_resolves(tmp_path: Path) -> None:
    repo, ref, impl = _mk_repo(tmp_path)
    other_ref = repo / "tmp" / "ref" / "ebay"
    other_ref.mkdir(parents=True)
    (impl / ".ref-dir").write_text(str(other_ref) + "\n", encoding="utf-8")
    proc = _resolve(ref)
    first = (proc.stdout.splitlines() or [""])[0].strip()
    assert first != str(impl.resolve()), (
        "impl/ with a foreign .ref-dir backlink is another run's tree; "
        f"resolver returned it anyway (stdout={proc.stdout!r})"
    )


def test_matching_backlink_impl_resolves(tmp_path: Path) -> None:
    repo, ref, impl = _mk_repo(tmp_path)
    (impl / ".ref-dir").write_text(str(ref) + "\n", encoding="utf-8")
    proc = _resolve(ref)
    first = (proc.stdout.splitlines() or [""])[0].strip()
    assert proc.returncode == 0, proc.stderr
    assert first == str(impl.resolve()), proc.stdout


def test_backlinkless_impl_keeps_legacy_resolution(tmp_path: Path) -> None:
    repo, ref, impl = _mk_repo(tmp_path)
    proc = _resolve(ref)
    first = (proc.stdout.splitlines() or [""])[0].strip()
    assert proc.returncode == 0, proc.stderr
    assert first == str(impl.resolve()), proc.stdout
