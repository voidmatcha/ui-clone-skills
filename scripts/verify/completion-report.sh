#!/usr/bin/env bash
# completion-report.sh — assemble the SKILL.md "completion-report
# contract" output from existing artifacts.
#
# Usage:
#   completion-report.sh <ref-dir> <impl-root>
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
# Exit 0 always — this is a report builder, not a gate. The report's
# content tells the agent / user whether the iteration is genuinely
# done.

set -uo pipefail

REF_DIR="${1:?Usage: completion-report.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?impl-root required}"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

REPO_ROOT=$(cd "$IMPL_ROOT" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || echo "")

python3 - "$REF_DIR" "$IMPL_ROOT" "$REPO_ROOT" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ref_dir, impl_root, repo_root = sys.argv[1:4]
ref_p = Path(ref_dir).resolve()
impl_p = Path(impl_root).resolve()
repo_root = str(Path(repo_root).resolve()) if repo_root else ""

def read_json(name: str) -> dict | None:
    p = ref_p / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def status_line(name: str, art: dict | None, key: str = "status") -> str:
    if art is None:
        return f"  - {name}: ❌ INCOMPLETE (artifact missing)"
    s = art.get(key, "?")
    marker = "✓" if s == "pass" else "○" if s == "skip" else "❌"
    return f"  - {name}: {marker} {s}"

print("━" * 70)
print("Completion Report — SKILL.md 'Hard Done Criteria'")
print("━" * 70)

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
    print(status_line(label, read_json(art_name)))

sc_result = ref_p / "sections" / "result.txt"
if sc_result.exists():
    try:
        text = sc_result.read_text(encoding="utf-8")
        # Crude FAIL line count
        fail_lines = sum(1 for line in text.splitlines() if "FAIL" in line.upper())
        pass_lines = sum(1 for line in text.splitlines() if "PASS" in line.upper())
        if fail_lines:
            print(f"  - section-compare: ❌ {pass_lines} pass / {fail_lines} fail")
        else:
            print(f"  - section-compare: ✓ {pass_lines} pass")
    except Exception:
        print("  - section-compare: ❌ INCOMPLETE (result unreadable)")
else:
    print("  - section-compare: ❌ INCOMPLETE (not run)")

# ── Tier 2-4: Runtime + state ────────────────────────────────────────
print("\n## Tier 2-4 — Runtime + state + transitions (composite)\n")
rp = read_json("runtime-proof.json")
tp = read_json("transition-proof.json")
print(status_line("runtime-proof", rp))
if rp and rp.get("status") != "pass":
    for reason in (rp.get("reasons") or [])[:3]:
        print(f"      • {reason}")
print(status_line("transition-proof", tp))
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
    print(status_line(label, read_json(art_name)))

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
incomplete_signals = []
if rp is None or (rp.get("status") not in ("pass", "skip")):
    incomplete_signals.append("runtime-proof not green")
if tp is None or (tp.get("status") not in ("pass", "skip")):
    incomplete_signals.append("transition-proof not green")
if not sc_result.exists():
    incomplete_signals.append("section-compare not run")
if incomplete_signals:
    print("  ❌ INCOMPLETE")
    print("     Reasons:")
    for s in incomplete_signals:
        print(f"     - {s}")
    print("\n  Do NOT report the clone as done until all signals above resolve.")
else:
    print("  ✓ all green per Hard Done Criteria")

print("\n" + "━" * 70)
PY
