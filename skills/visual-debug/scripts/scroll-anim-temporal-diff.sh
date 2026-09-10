#!/usr/bin/env bash
# scroll-anim-temporal-diff.sh — General-purpose phase/frequency diff for
# scroll-driven repeating animations.
#
# Why it matters:
#   Scroll-driven animations on N repeating elements (rows of a number stack,
#   columns of a marquee, parallax tiles) usually fall into ONE of two patterns:
#     1. Single traveling wave — every element rides the same wave function;
#        consecutive elements differ by a constant phase offset (≈ 2π/N).
#        Reads as a smooth continuous interlock.
#     2. Per-row frequency — each element index gets a different frequency
#        multiplier (e.g. sin(p·π·(i+1))). Reads as irregular gaps and tight
#        overlaps because rows desync.
#
#   Both look "animated" in screenshots, both pass AE diff (the wave amplitude
#   may match), but the perceived motion is completely different. AE/SSIM
#   never separate these — they are pixel comparisons frozen in time. Hover
#   diffs don't see them either (these are scroll-driven). The only way to
#   distinguish them is to sample each element's position over scroll progress
#   and analyze the per-element trajectories.
#
#   This script does exactly that — N samples × M elements on both ref and
#   impl, then compares (a) per-element zero-crossing count (frequency proxy),
#   (b) amplitude, (c) phase relationship between consecutive elements.
#
# Usage:
#   bash scroll-anim-temporal-diff.sh <session> <ref-url> <impl-url> <selector> [out-dir]
#
#   <selector> is a CSS selector that matches the repeating elements on BOTH
#   ref and impl pages. The script captures every element matched on each
#   side and pairs them by DOM order (1st ref ↔ 1st impl, etc.).
#
#   Examples:
#     "svg.number-stack-svg .num"        — number stack rows (the hottype "28" bug class)
#     ".marquee-track .marquee-item"     — marquee tiles
#     ".parallax-tile"                    — vertical parallax tiles
#     "[data-row]"                        — custom data-attribute marker
#
# Env:
#   SAMPLES   — number of scroll positions to sample (default 21, gives 0.05 increments)
#   AXIS      — "y" (default) or "x" — which boundingClientRect delta to track
#   WAIT_MS   — wait per scroll position (default 250)
#
# Exit: 0 = patterns match, 1 = patterns diverge (likely wrong wave family),
#       2 = setup error.
#
# See diagnosis.md → Root Cause E (Animation / Transition Not Applied or Wrong Easing).

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser" >&2
  exit 2
fi
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 required" >&2
  exit 2
fi

SESSION="${1:?Usage: scroll-anim-temporal-diff.sh <session> <ref-url> <impl-url> <selector> [out-dir]}"
REF_URL="${2:?Missing ref-url}"
IMPL_URL="${3:?Missing impl-url}"
SELECTOR="${4:?Missing selector}"
OUT_DIR="${5:-tmp/scroll-anim-temporal}"

SAMPLES="${SAMPLES:-21}"
AXIS="${AXIS:-y}"
WAIT_MS="${WAIT_MS:-250}"

mkdir -p "$OUT_DIR"

SESSION_REF="${SESSION}-tmp-ref"
SESSION_IMPL="${SESSION}-tmp-impl"

cleanup() {
  agent-browser --session "$SESSION_REF" close >/dev/null 2>&1
  agent-browser --session "$SESSION_IMPL" close >/dev/null 2>&1
}
trap cleanup EXIT

# Open both pages once. Set viewport AFTER open (otherwise it's silently dropped).
agent-browser --session "$SESSION_REF" navigate "$REF_URL" >/dev/null 2>&1
agent-browser --session "$SESSION_IMPL" navigate "$IMPL_URL" >/dev/null 2>&1
agent-browser --session "$SESSION_REF" set viewport "${VIEW_W:-1280}" "${VIEW_H:-900}" >/dev/null 2>&1
agent-browser --session "$SESSION_IMPL" set viewport "${VIEW_W:-1280}" "${VIEW_H:-900}" >/dev/null 2>&1
sleep 2

sample_one() {
  local SESS="$1"
  local OUT="$2"
  : > "$OUT"
  python3 -c "
import json, sys
samples = $SAMPLES
print(json.dumps([round(i / (samples - 1), 4) for i in range(samples)]))
" > "$OUT_DIR/_progresses.json"

  for P in $(python3 -c "import json; print(' '.join(str(p) for p in json.load(open('$OUT_DIR/_progresses.json'))))"); do
    # Scroll to the proportional position.
    agent-browser --session "$SESS" eval "(() => {
      const p = ${P};
      const sh = document.documentElement.scrollHeight - window.innerHeight;
      window.scrollTo(0, Math.round(sh * p));
    })()" >/dev/null 2>&1
    perl -e "select(undef,undef,undef,${WAIT_MS}/1000)"
    RAW=$(agent-browser --session "$SESS" eval "(() => {
      const els = [...document.querySelectorAll('${SELECTOR}')];
      const axis = '${AXIS}';
      const out = els.map(el => {
        const r = el.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      });
      return JSON.stringify({ p: ${P}, count: els.length, axis, items: out });
    })()" 2>/dev/null)
    # Unwrap agent-browser quoting.
    echo "$RAW" | sed 's/^\"//;s/\"$//' | sed 's/\\\"/\"/g' >> "$OUT"
  done
}

echo "▸ Sampling ref ($REF_URL)..."
sample_one "$SESSION_REF" "$OUT_DIR/ref-samples.jsonl"

echo "▸ Sampling impl ($IMPL_URL)..."
sample_one "$SESSION_IMPL" "$OUT_DIR/impl-samples.jsonl"

echo ""
echo "═══ Scroll Animation Temporal Diff ═══"
echo "Selector: $SELECTOR"
echo "Samples: $SAMPLES   Axis: $AXIS"
echo ""

python3 - <<PYEOF
import json, sys
from pathlib import Path

OUT = Path("$OUT_DIR")
AXIS = "$AXIS"

def load(jsonl):
    rows = []
    for line in Path(jsonl).read_text().splitlines():
        line = line.strip()
        if not line: continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    rows.sort(key=lambda r: r["p"])
    return rows

ref = load(OUT / "ref-samples.jsonl")
impl = load(OUT / "impl-samples.jsonl")

if not ref or not impl:
    print("ERROR: failed to capture samples")
    sys.exit(2)

ref_count = ref[0].get("count", 0)
impl_count = impl[0].get("count", 0)

print(f"Element count: ref={ref_count}, impl={impl_count}")
if ref_count == 0 or impl_count == 0:
    print(f"ERROR: selector '$SELECTOR' matched 0 elements on one side")
    sys.exit(2)
if ref_count != impl_count:
    print(f"⚠️  Element count mismatch — pairing first min({ref_count},{impl_count}) by DOM order")

N = min(ref_count, impl_count)
S = len(ref)

def trajectory(rows, idx):
    """Return list of (p, value) for element idx along axis."""
    out = []
    for row in rows:
        items = row.get("items", [])
        if idx < len(items):
            out.append((row["p"], items[idx][AXIS]))
    return out

def detrend(traj):
    """Subtract mean — what we care about is oscillation, not bulk scroll movement."""
    if not traj: return []
    m = sum(v for _, v in traj) / len(traj)
    return [(p, v - m) for p, v in traj]

def zero_crossings(traj):
    """Count sign changes in detrended trajectory — frequency proxy."""
    crossings = 0
    prev = None
    for _, v in traj:
        if prev is not None and ((prev <= 0) != (v <= 0)) and (prev != 0 or v != 0):
            crossings += 1
        prev = v
    return crossings

def amplitude(traj):
    if not traj: return 0
    vs = [v for _, v in traj]
    return max(vs) - min(vs)

def peak_progress(traj):
    """Progress at which trajectory reaches its max — phase proxy."""
    if not traj: return 0
    return max(traj, key=lambda pv: pv[1])[0]

def analyze(rows, label):
    out = {"label": label, "elements": []}
    for i in range(N):
        traj = detrend(trajectory(rows, i))
        out["elements"].append({
            "index": i,
            "zero_crossings": zero_crossings(traj),
            "amplitude": round(amplitude(traj), 1),
            "peak_progress": peak_progress(traj),
        })
    return out

ra = analyze(ref, "ref")
ia = analyze(impl, "impl")

# Frequency family classification.
def classify(elements):
    zcs = [e["zero_crossings"] for e in elements]
    if not zcs: return "empty", {}
    same = all(z == zcs[0] for z in zcs)
    if same:
        return "single-frequency", {"zc_per_element": zcs[0]}
    increasing = all(zcs[i+1] >= zcs[i] for i in range(len(zcs) - 1)) and zcs[-1] > zcs[0]
    if increasing:
        return "per-row-frequency", {"zcs": zcs}
    return "mixed", {"zcs": zcs}

ref_family, ref_meta = classify(ra["elements"])
impl_family, impl_meta = classify(ia["elements"])

# Phase relationship: are consecutive peaks evenly spaced (traveling wave)?
def phase_evenness(elements):
    peaks = [e["peak_progress"] for e in elements]
    if len(peaks) < 3: return None
    diffs = [(peaks[i+1] - peaks[i]) % 1.0 for i in range(len(peaks) - 1)]
    if not diffs: return None
    mean_d = sum(diffs) / len(diffs)
    if mean_d == 0: return 0.0
    var = sum((d - mean_d) ** 2 for d in diffs) / len(diffs)
    return round(var / abs(mean_d), 3) if mean_d else None  # coefficient of variation

ref_phase_cv = phase_evenness(ra["elements"])
impl_phase_cv = phase_evenness(ia["elements"])

# Report.
def fmt_family(name, meta):
    if name == "single-frequency":
        return f"single-frequency (zc={meta.get('zc_per_element')} per element)"
    if name == "per-row-frequency":
        return f"per-row-frequency (zcs={meta.get('zcs')})"
    if name == "mixed":
        return f"mixed (zcs={meta.get('zcs')})"
    return name

print("")
print("| metric | ref | impl |")
print("|--------|-----|------|")
print(f"| frequency family | {fmt_family(ref_family, ref_meta)} | {fmt_family(impl_family, impl_meta)} |")
print(f"| amplitude (px, mean across elements) | {round(sum(e['amplitude'] for e in ra['elements'])/N, 1)} | {round(sum(e['amplitude'] for e in ia['elements'])/N, 1)} |")
print(f"| phase-spacing CV (lower = more even/traveling) | {ref_phase_cv} | {impl_phase_cv} |")

findings = []
if ref_family != impl_family:
    findings.append(
        f"FREQUENCY_FAMILY_DIVERGENCE: ref is {ref_family}, impl is {impl_family}. "
        f"This is the 'single traveling wave vs per-row frequency' bug class — "
        f"e.g. ref uses sin(progress·2π + i·2π/N), impl uses sin(progress·π·(i+1))."
    )
if ref_phase_cv is not None and impl_phase_cv is not None and ref_phase_cv < 0.5 and impl_phase_cv > 1.5:
    findings.append(
        f"PHASE_IRREGULARITY: ref peaks are evenly spaced (CV={ref_phase_cv}), "
        f"impl peaks are irregular (CV={impl_phase_cv}). Likely wrong phase term in impl."
    )
ref_amp = sum(e['amplitude'] for e in ra['elements']) / N
impl_amp = sum(e['amplitude'] for e in ia['elements']) / N
if ref_amp > 0 and (impl_amp / ref_amp < 0.5 or impl_amp / ref_amp > 2.0):
    findings.append(
        f"AMPLITUDE_DIVERGENCE: ref mean amplitude {round(ref_amp,1)}px, "
        f"impl mean amplitude {round(impl_amp,1)}px. Wrong magnitude multiplier."
    )

report = {
    "selector": "$SELECTOR",
    "axis": AXIS,
    "samples": S,
    "element_count": {"ref": ref_count, "impl": impl_count, "paired": N},
    "ref": ra,
    "impl": ia,
    "ref_family": ref_family,
    "impl_family": impl_family,
    "ref_phase_cv": ref_phase_cv,
    "impl_phase_cv": impl_phase_cv,
    "findings": findings,
}
(OUT / "report.json").write_text(json.dumps(report, indent=2))

print("")
if not findings:
    print("✅ No temporal-pattern divergences detected.")
    sys.exit(0)

print("⛔ Findings:")
for f in findings:
    print(f"  • {f}")
print("")
print(f"Full report: {OUT}/report.json")
print("")
print("Common fix patterns:")
print("  • Single traveling wave: sineOffset = sin(progress * 2*PI + i * (2*PI / N)) * AMP")
print("  • Per-row frequency:     sineOffset = sin(progress * PI * (i + 1)) * AMP")
print("  Pick the one that matches the ref family above.")
sys.exit(1)
PYEOF
