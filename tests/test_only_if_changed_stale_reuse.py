"""ONLY_IF_CHANGED must not reuse a prior run that never converged.

`ONLY_IF_CHANGED=1` short-circuits on the impl source hash. The hash is written
regardless of the prior run's verdict, so a run that ended INCONCLUSIVE (a section
whose reference crop carried no signal) leaves both a matching hash and a
non-converged result.txt behind.

That matters specifically because the prescribed remedy for an UNMEASURED row is
capture-side — re-capture the reference — and re-capturing does not change the impl
source hash. So the documented next step lands exactly on the short-circuit, which
exited 0 while "reusing" the inconclusive result. The optimisation is about impl
edits; it must not certify a run that never measured anything.
"""

import subprocess
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "section-compare.sh"
)

# The unambiguous short-circuit marker. Matching on the bare word "reusing" would
# also match the decline message ("...rather than reusing it").
_SHORT_CIRCUIT = "no source changes since last section-compare"

_ROWS = "| hero | 0 | 0 | ok | ✅ |\n"
_UNMEASURED_ROW = "| slideshow | — | — | unmeasured | ⚠️ UNMEASURED (blank-ref) |\n"


def _setup(tmp_path: Path, result_body: str) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.tsx").write_text("export default function App() { return null }\n")
    (sections / "result.txt").write_text(result_body, encoding="utf-8")
    return ref, src


def _run(ref: Path, src: Path) -> subprocess.CompletedProcess[str]:
    env_prefix = {"ONLY_IF_CHANGED": "1", "IMPL_SRC_DIR": str(src)}
    import os

    env = {**os.environ, **env_prefix}
    return subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "https://ref.example",
            "https://impl.example",
            "test-session",
            str(ref),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def _prime_hash(ref: Path, src: Path) -> None:
    """Write .last-impl-hash directly.

    Mirrors compute_impl_hash() in section-compare.sh, which hashes file *contents*
    relative to the source dir and is therefore path-independent. Priming by
    invoking the script instead would cost a full compare per test.
    """
    pipeline = (
        r"cd " + str(src) + r" && find . "
        r"\( -name '*.tsx' -o -name '*.jsx' -o -name '*.ts' -o -name '*.js' "
        r"-o -name '*.css' -o -name '*.scss' \) "
        r"-not -path '*/node_modules/*' -not -path '*/.next/*' "
        r"-not -path '*/dist/*' -not -path '*/build/*' "
        r"-type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null "
        r"| shasum -a 256 | awk '{print $1}'"
    )
    digest = subprocess.run(
        ["bash", "-c", pipeline], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    assert digest, "hash priming produced nothing — compute_impl_hash mirror is stale"
    (ref / "sections" / ".last-impl-hash").write_text(digest, encoding="utf-8")


def test_clean_prior_run_still_short_circuits(tmp_path: Path) -> None:
    body = _ROWS + "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n"
    ref, src = _setup(tmp_path, body)
    _prime_hash(ref, src)
    (ref / "sections" / "result.txt").write_text(body, encoding="utf-8")
    proc = _run(ref, src)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _SHORT_CIRCUIT in proc.stdout


def test_unmeasured_prior_run_is_not_reused_as_success(tmp_path: Path) -> None:
    body = (
        _ROWS
        + _UNMEASURED_ROW
        + "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 1 UNMEASURED**\n"
    )
    ref, src = _setup(tmp_path, body)
    _prime_hash(ref, src)
    (ref / "sections" / "result.txt").write_text(body, encoding="utf-8")
    proc = _run(ref, src)
    combined = proc.stdout + proc.stderr
    assert not (
        proc.returncode == 0 and _SHORT_CIRCUIT in proc.stdout
    ), "an inconclusive prior run must not be reused as a pass:\n" + combined


def test_failed_prior_run_is_not_reused_as_success(tmp_path: Path) -> None:
    body = _ROWS + "\n**Result: 1 PASS, 3 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n"
    ref, src = _setup(tmp_path, body)
    _prime_hash(ref, src)
    (ref / "sections" / "result.txt").write_text(body, encoding="utf-8")
    proc = _run(ref, src)
    assert not (proc.returncode == 0 and _SHORT_CIRCUIT in proc.stdout), proc.stdout + proc.stderr
