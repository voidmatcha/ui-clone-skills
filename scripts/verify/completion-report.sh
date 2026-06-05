#!/usr/bin/env bash
# completion-report.sh — assemble the SKILL.md "completion-report
# contract" output from existing artifacts.
#
# Usage:
#   completion-report.sh [--check] <ref-dir> <impl-root>
#
# 2026-05-22 SKILL.md "Hard Done Criteria" mandates a completion
# report with specific fields (modified files, ref-JS dependency,
# runtime-proof, transition-proof, scroll/hover/header state proof,
# gate output, INCOMPLETE markers). This script reads the relevant
# artifacts and prints the assembled report.
#
# Designed to be called at the end of an iteration before declaring
# done. If any required artifact is missing or status≠pass, the
# report explicitly marks INCOMPLETE for that section.
#
# Default mode exits 0 as a report builder. `--check` exits 1 when the
# assembled report is incomplete, making closeout machine-checkable for
# unattended loops.

set -uo pipefail

CHECK_MODE=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_MODE=1
  shift
fi

if [[ $# -ne 2 ]]; then
  echo "Usage: completion-report.sh [--check] <ref-dir> <impl-root>" >&2
  exit 2
fi

REF_DIR="$1"
IMPL_ROOT="$2"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

REPO_ROOT=$(cd "$IMPL_ROOT" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || echo "")

python3 - "$REF_DIR" "$IMPL_ROOT" "$REPO_ROOT" "$CHECK_MODE" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ref_dir, impl_root, repo_root, check_mode_raw = sys.argv[1:5]
ref_p = Path(ref_dir).resolve()
impl_p = Path(impl_root).resolve()
repo_root = str(Path(repo_root).resolve()) if repo_root else ""
check_mode = check_mode_raw == "1"
incomplete_signals: list[str] = []

def read_json(name: str) -> dict | None:
    p = ref_p / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _font_substitution_declared() -> bool:
    substitution = read_json("asset-substitution.json")
    fonts = substitution.get("fonts") if isinstance(substitution, dict) else None
    return isinstance(fonts, list) and bool(fonts)

def normalized_status(name: str, art: dict | None, key: str = "status") -> str | None:
    if art is None:
        return None
    if name == "font-parity":
        parity = art.get("parity")
        if parity == "match":
            return "pass"
        if parity == "mismatch" and _font_substitution_declared():
            return "pass"
        return str(parity or art.get(key) or "?")
    status = art.get(key)
    return str(status) if status is not None else None

def status_line(name: str, art: dict | None, key: str = "status") -> str:
    if art is None:
        return f"  - {name}: ❌ INCOMPLETE (artifact missing)"
    s = normalized_status(name, art, key) or "?"
    marker = "✓" if s == "pass" else "○" if s == "skip" else "❌"
    return f"  - {name}: {marker} {s}"

def artifact_signal(name: str, art: dict | None, *, allow_skip: bool = False) -> str | None:
    if art is None:
        return f"{name} missing"
    status = normalized_status(name, art)
    if status == "pass" or (allow_skip and status == "skip"):
        return None
    return f"{name} status={status!r}"

def section_compare_counts(text: str) -> dict[str, int]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        first = cells[0] if cells else ""
        if first.lower() == "section" or (first and set(first) <= {"-"}):
            continue
        rows.append(line)
    structural_only = sum(1 for line in rows if "STRUCTURAL_ONLY" in line)
    fail = sum(1 for line in rows if "❌" in line or "🌑" in line)
    missing = sum(1 for line in rows if "MISSING impl" in line)
    passed = sum(1 for line in rows if "✅" in line or "STRUCTURAL_ONLY" in line)
    return {
        "total": len(rows),
        "pass": passed,
        "fail": fail,
        "missing": missing,
        "structural_only": structural_only,
    }

print("━" * 70)
print("Completion Report — SKILL.md 'Hard Done Criteria'")
print("━" * 70)

state = read_json("pipeline-state.json")
current_gate = state.get("current_gate") if isinstance(state, dict) else None
print("\n## Pipeline state\n")
if current_gate == "done":
    print('  - current_gate: ✓ "done"')
elif current_gate is None:
    print("  - current_gate: ❌ INCOMPLETE (pipeline-state.json missing/unreadable)")
    incomplete_signals.append("pipeline-state.json missing/unreadable")
else:
    print(f"  - current_gate: ❌ INCOMPLETE ({current_gate!r}, need 'done')")
    incomplete_signals.append(f"current_gate is {current_gate!r}, not 'done'")

# ── Tier 1: Static visual ────────────────────────────────────────────
print("\n## Tier 1 — Static visual match\n")
for art_name, label in [
    ("font-parity.json",         "font-parity"),
    ("image-fidelity.json",      "image-fidelity"),
    ("svg-dom-parity.json",      "svg-dom-parity"),
    ("required-media-coverage.json", "required-media-coverage"),
    ("hero-composite.json",      "hero-composite"),
    ("svg-provenance.json",      "svg-provenance"),
    ("color-token-grounding.json","color-token-grounding"),
]:
    art = read_json(art_name)
    print(status_line(label, art))
    signal = artifact_signal(label, art, allow_skip=True)
    if signal:
        incomplete_signals.append(signal)

sc_result = ref_p / "sections" / "result.txt"
if sc_result.exists():
    try:
        text = sc_result.read_text(encoding="utf-8")
        counts = section_compare_counts(text)
        section_incomplete = bool(counts["fail"] or counts["missing"] or "INCOMPLETE" in text)
        if section_incomplete:
            print(
                f"  - section-compare: ❌ {counts['pass']} pass / "
                f"{counts['fail']} fail / {counts['missing']} missing"
            )
            reason = (
                f"section-compare dirty: {counts['fail']} fail / "
                f"{counts['missing']} missing"
            )
            if "INCOMPLETE" in text:
                reason += " / INCOMPLETE marker"
            incomplete_signals.append(reason)
        else:
            print(f"  - section-compare: ✓ {counts['pass']} pass / 0 fail")
        if counts["total"] == 0:
            incomplete_signals.append("section-compare has 0 measured rows")
        if (
            counts["total"] > 0
            and counts["structural_only"] >= 3
            and counts["structural_only"] / counts["total"] >= 0.30
        ):
            pct = round(100 * counts["structural_only"] / counts["total"])
            print(
                f"      ⚠ STRUCTURAL_ONLY coverage broad: "
                f"{counts['structural_only']}/{counts['total']} sections ({pct}%). "
                "pixel AE polishing skipped for those rows; narrow "
                "asset-substitution.json before claiming pixel fidelity."
            )
    except Exception:
        print("  - section-compare: ❌ INCOMPLETE (result unreadable)")
        incomplete_signals.append("section-compare result unreadable")
else:
    print("  - section-compare: ❌ INCOMPLETE (not run)")
    incomplete_signals.append("section-compare not run")

# ── Tier 2-4: Runtime + state ────────────────────────────────────────
print("\n## Tier 2-4 — Runtime + state + transitions (composite)\n")
rp = read_json("runtime-proof.json")
tp = read_json("transition-proof.json")
print(status_line("runtime-proof", rp))
signal = artifact_signal("runtime-proof", rp, allow_skip=True)
if signal:
    incomplete_signals.append(signal)
if rp and rp.get("status") != "pass":
    for reason in (rp.get("reasons") or [])[:3]:
        print(f"      • {reason}")
print(status_line("transition-proof", tp))
signal = artifact_signal("transition-proof", tp, allow_skip=True)
if signal:
    incomplete_signals.append(signal)
if tp and tp.get("status") != "pass":
    for reason in (tp.get("reasons") or [])[:3]:
        print(f"      • {reason}")

# ── Tier 5: No-cheat ─────────────────────────────────────────────────
print("\n## Tier 5 — No-cheat\n")
for art_name, label in [
    ("ref-js-loader.json",    "ref-js-loader"),
    ("proxy-mirror.json",     "proxy-mirror"),
    ("html-paste.json",       "html-paste"),
    ("ref-screenshot-asset.json", "ref-screenshot-asset"),
    ("impl-scope.json",       "impl-scope (gate-cheat block)"),
    ("runtime-env.json",      "runtime-env"),
]:
    art = read_json(art_name)
    print(status_line(label, art))
    signal = artifact_signal(label, art, allow_skip=True)
    if signal:
        incomplete_signals.append(signal)

# ── Modified files ───────────────────────────────────────────────────
print("\n## Modified files (impl scope)\n")
if repo_root:
    try:
        diff = subprocess.run(
            ["git", "-C", repo_root, "diff", "--name-only", "HEAD~5"],
            capture_output=True, text=True, timeout=10,
        )
        files = [f for f in diff.stdout.strip().splitlines() if f]
        try:
            impl_rel = str(impl_p.relative_to(repo_root)) if repo_root else ""
        except ValueError:
            impl_rel = str(impl_p)
        impl_files = [f for f in files if f.startswith(impl_rel + "/")]
        plugin_files = [f for f in files if not f.startswith(impl_rel + "/") and not f.startswith("tmp/")]
        for f in impl_files[:10]:
            print(f"  - {f}")
        if len(impl_files) > 10:
            print(f"  ... + {len(impl_files) - 10} more impl files")
        if plugin_files:
            print(f"\n  ⚠ {len(plugin_files)} plugin file(s) modified in last 5 commits:")
            for f in plugin_files[:5]:
                print(f"    - {f}")
            print("    (impl-scope-check should have caught these if they were during iteration)")
    except Exception as e:
        print(f"  (git diff failed: {e})")
else:
    print("  (no git repo root)")

# ── Ref-JS direct-load dependency ────────────────────────────────────
print("\n## Ref-JS direct-load dependency\n")
rjl = read_json("ref-js-loader.json")
if rjl is None:
    print("  ❌ INCOMPLETE (ref-js-loader gate not run)")
else:
    s = rjl.get("status", "?")
    v = len(rjl.get("violations", []))
    if s == "pass":
        print(f"  ✓ false (no ref-host references in impl)")
    elif s == "skip":
        print("  ○ skipped (no ref host candidates)")
    else:
        print(f"  ❌ true — {v} violation(s). Iteration CANNOT claim done.")

# ── Overall verdict ──────────────────────────────────────────────────
print("\n## Overall verdict\n")
if incomplete_signals:
    print("  ❌ INCOMPLETE")
    print("     Reasons:")
    for s in dict.fromkeys(incomplete_signals):
        print(f"     - {s}")
    print("\n  Do NOT report the clone as done until all signals above resolve.")
else:
    print("  ✓ all green per Hard Done Criteria")

print("\n" + "━" * 70)
if check_mode and incomplete_signals:
    sys.exit(1)
PY
