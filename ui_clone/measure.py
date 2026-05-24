"""ui_clone.measure — Python orchestrator for measurement bash scripts.

Single entry point that invokes the bash measurement scripts with
hardcoded defaults so agents cannot route around them via env vars.

Closes the failure class observed across multiple benchmark runs:

- An agent ran section-compare with default `EXCLUDE_DYNAMIC=0`,
  letting `<video>` first-frame variance balloon AE to 1M+ on sections
  whose static layout matched fine.
- An agent set `SECTION_THRESHOLD=250000` (vs default 2000) so AE/Mpx
  values of 88,823 and 228,325 — both nominally `critical` (>20000) —
  were re-classified as `minor` and ✅ PASSed. The gate could not see
  this because section-compare.sh writes whatever severity the env
  chose; gate.py only reads the written band.

This module enforces the safe defaults from the *Python* layer, where
the agent can't reach in and tweak them. Bash scripts remain the
measurement workers (agent-browser, ImageMagick, file IO) — only the
*orchestration* moves to Python.

Usage:
    python -m ui_clone.measure section-compare <ref-dir> --orig-url ... --impl-url ...
    python -m ui_clone.measure all <ref-dir> --orig-url ... --impl-url ...

Subcommands write the same artifacts the bash scripts produce; the
caller (or gate.py) reads them as before. The wrapper additionally
prints a single JSON status line on stdout so callers can branch on
status without parsing markdown tables.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Locked defaults — these override caller env by being injected into the
# subprocess env explicitly. To opt back into less-strict measurement
# (debugging, exploratory), call the bash scripts directly — measure.py
# is the *guarded* entry, not the only entry.
LOCKED_DEFAULTS: dict[str, str] = {
    # section-compare: mask <video>/<canvas>/dynamic-spec sections from AE.
    # Per-pixel motion variance is validated separately by transition-compare
    # / video-motion-compare, not by section-compare. See section-compare.sh
    # header for the full rationale.
    "EXCLUDE_DYNAMIC": "1",
    # section-compare: AE/Mpx classifier threshold. d19e28d benchmark exposed
    # an agent setting this to 250000 to re-classify `critical` AE as `minor`
    # and silently pass. 2000 is the script's documented static-content
    # default; pages with heavy imagery should declare asset-substitution
    # rather than inflate the threshold.
    "SECTION_THRESHOLD": "2000",
}


def _project_root() -> Path:
    """Return the repo root containing skills/, scripts/."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "skills" / "visual-debug" / "scripts").is_dir():
            return parent
    return here.parents[1]


def _bash(script_rel: str, args: list[str], env_overrides: dict[str, str]) -> int:
    """Invoke a bash script under skills/visual-debug/scripts/ with locked env.

    Returns the subprocess exit code. Stdout/stderr stream through to
    the caller so the agent sees the same output it would see from
    running bash directly.
    """
    root = _project_root()
    script = root / "skills" / "visual-debug" / "scripts" / script_rel
    if not script.is_file():
        print(
            json.dumps({"status": "error", "reason": f"script not found: {script}"}),
            file=sys.stderr,
        )
        return 2
    env = dict(os.environ)
    # Locked defaults: ALWAYS override caller env. The whole point of
    # routing through measure.py is to prevent the agent (or a wrapper
    # shell) from setting these to permissive values.
    for k, v in env_overrides.items():
        env[k] = v
    cmd = ["bash", str(script), *args]
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


def _emit_status(payload: dict[str, object]) -> None:
    """Print a single JSON status line to stdout so callers can branch."""
    print(json.dumps(payload, ensure_ascii=False))


def cmd_section_compare(args: argparse.Namespace) -> int:
    """Run section-compare with EXCLUDE_DYNAMIC=1 + SECTION_THRESHOLD=2000 locked."""
    bash_args = [args.orig_url, args.impl_url, args.session, args.ref_dir]
    rc = _bash("section-compare.sh", bash_args, LOCKED_DEFAULTS)
    result_file = Path(args.ref_dir) / "sections" / "result.txt"
    _emit_status({
        "step": "section-compare",
        "exit_code": rc,
        "produces": str(result_file),
        "exists": result_file.is_file(),
        "locked_env": LOCKED_DEFAULTS,
    })
    return rc


def cmd_transition_compare(args: argparse.Namespace) -> int:
    """Run transition-compare (uses timeout-shim.sh internally)."""
    bash_args = [args.orig_url, args.impl_url, args.session, args.ref_dir]
    # No SECTION_THRESHOLD here — transition-compare has its own scoring.
    rc = _bash("transition-compare.sh", bash_args, {})
    result_file = Path(args.ref_dir) / "transitions" / "result.txt"
    _emit_status({
        "step": "transition-compare",
        "exit_code": rc,
        "produces": str(result_file),
        "exists": result_file.is_file(),
    })
    return rc


def cmd_asset_utilization(args: argparse.Namespace) -> int:
    """Run asset-utilization-check."""
    bash_args = [args.ref_dir]
    if args.impl_src:
        bash_args.append(args.impl_src)
    rc = _bash("asset-utilization-check.sh", bash_args, {})
    artifact = Path(args.ref_dir) / "asset-utilization.json"
    _emit_status({
        "step": "asset-utilization",
        "exit_code": rc,
        "produces": str(artifact),
        "exists": artifact.is_file(),
    })
    return rc


def cmd_bundle_impl_coverage(args: argparse.Namespace) -> int:
    """Run bundle-impl-coverage-check."""
    bash_args = [args.ref_dir]
    if args.impl_pkg:
        bash_args.append(args.impl_pkg)
    rc = _bash("bundle-impl-coverage-check.sh", bash_args, {})
    artifact = Path(args.ref_dir) / "bundle-impl-coverage.json"
    _emit_status({
        "step": "bundle-impl-coverage",
        "exit_code": rc,
        "produces": str(artifact),
        "exists": artifact.is_file(),
    })
    return rc


def cmd_all(args: argparse.Namespace) -> int:
    """Run the canonical measurement sequence in order.

    Order matters: static fidelity (section-compare) first so motion
    noise doesn't contaminate the structural verdict, then motion
    fidelity (transition-compare), then composition checks
    (asset-utilization, bundle-impl-coverage).
    """
    summary: list[dict[str, object]] = []
    overall_rc = 0

    # 1) static fidelity
    rc = cmd_section_compare(args)
    summary.append({"step": "section-compare", "exit_code": rc})
    overall_rc = overall_rc or rc

    # 2) motion fidelity — only if a transition-spec exists
    spec = Path(args.ref_dir) / "transition-spec.json"
    if spec.is_file():
        rc = cmd_transition_compare(args)
        summary.append({"step": "transition-compare", "exit_code": rc})
        overall_rc = overall_rc or rc
    else:
        summary.append({"step": "transition-compare", "exit_code": "skip", "reason": "no transition-spec.json"})

    # 3) composition checks
    rc = cmd_asset_utilization(args)
    summary.append({"step": "asset-utilization", "exit_code": rc})
    overall_rc = overall_rc or rc

    rc = cmd_bundle_impl_coverage(args)
    summary.append({"step": "bundle-impl-coverage", "exit_code": rc})
    overall_rc = overall_rc or rc

    _emit_status({
        "step": "all",
        "summary": summary,
        "overall_exit_code": overall_rc,
        "locked_env": LOCKED_DEFAULTS,
    })
    return overall_rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ui_clone.measure",
        description=(
            "Python orchestrator for bash measurement scripts with locked "
            "defaults. Use this instead of invoking the bash scripts directly "
            "when the goal is fidelity verification rather than ad-hoc debug."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    def _add_url_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("ref_dir", help="ref dir (e.g. tmp/ref/<component>)")
        sp.add_argument("--orig-url", required=True, help="ref site URL")
        sp.add_argument("--impl-url", required=True, help="local impl URL")
        sp.add_argument("--session", default="ui-clone-measure", help="agent-browser session name")

    sp = sub.add_parser("section-compare", help="static fidelity (EXCLUDE_DYNAMIC=1 locked)")
    _add_url_args(sp)
    sp.set_defaults(func=cmd_section_compare)

    sp = sub.add_parser("transition-compare", help="motion fidelity")
    _add_url_args(sp)
    sp.set_defaults(func=cmd_transition_compare)

    sp = sub.add_parser("asset-utilization", help="src code references vs downloaded files")
    sp.add_argument("ref_dir")
    sp.add_argument("--impl-src", default=None, help="impl/src dir (auto-detected if omitted)")
    sp.set_defaults(func=cmd_asset_utilization)

    sp = sub.add_parser("bundle-impl-coverage", help="bundle-detected libs vs package.json deps")
    sp.add_argument("ref_dir")
    sp.add_argument("--impl-pkg", default=None, help="impl/package.json path (auto-detected)")
    sp.set_defaults(func=cmd_bundle_impl_coverage)

    sp = sub.add_parser("all", help="run the canonical measurement sequence in order")
    _add_url_args(sp)
    sp.add_argument("--impl-src", default=None)
    sp.add_argument("--impl-pkg", default=None)
    sp.set_defaults(func=cmd_all)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
