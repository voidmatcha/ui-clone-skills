#!/usr/bin/env bash
# resize-behavior-probe.sh — prove that the implementation changes layout
# across widths observed in the responsive reference.
#
# Usage:
#   resize-behavior-probe.sh <impl_url> <ref_dir> [--session S]
#
# Output: <ref_dir>/resize-behavior.json
# Exit: 0 pass/skip, 1 responsive behavior not proved, 2 setup error.

set -uo pipefail

IMPL_URL="${1:?Usage: resize-behavior-probe.sh <impl_url> <ref_dir> [--session S]}"
REF_DIR="${2:?Usage: resize-behavior-probe.sh <impl_url> <ref_dir> [--session S]}"
SESSION="resize-probe"
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --session) SESSION="$2"; shift 2 ;;
    *) shift ;;
  esac
done

OUT="$REF_DIR/resize-behavior.json"

PROBE_INPUT=$(python3 - "$REF_DIR" <<'PY'
import json
import re
import sys
from pathlib import Path

ref = Path(sys.argv[1])

def load(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None

def number(value):
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        return int(float(match.group())) if match else None
    if isinstance(value, dict):
        for key in ("width", "value", "px", "breakpoint"):
            found = number(value.get(key))
            if found:
                return found
    return None

breakpoint_doc = load(ref / "detected-breakpoints.json")
breakpoints = (
    breakpoint_doc.get("breakpoints")
    if isinstance(breakpoint_doc, dict)
    else breakpoint_doc
)
breakpoint_widths = sorted({
    width
    for item in (breakpoints if isinstance(breakpoints, list) else [])
    if (width := number(item)) is not None
})

keys = []
sample_widths = set()
sizing_path = ref / "responsive" / "sizing-expressions.json"
if not sizing_path.is_file():
    sizing_path = ref / "sizing-expressions.json"
sizing_doc = load(sizing_path)
expressions = (
    sizing_doc.get("expressions", sizing_doc)
    if isinstance(sizing_doc, dict)
    else []
)
if isinstance(expressions, list):
    entries = [
        (
            item.get("selector") or item.get("target"),
            [(
                item.get("property") or item.get("cssProperty") or "width",
                item,
            )],
        )
        for item in expressions
        if isinstance(item, dict)
    ]
elif isinstance(expressions, dict):
    entries = []
    for selector, properties in expressions.items():
        if not isinstance(selector, str) or not isinstance(properties, dict):
            continue
        property_entries = []
        for value in properties.values():
            if not isinstance(value, dict):
                continue
            samples = value.get("samples")
            if isinstance(samples, dict):
                sample_widths.update(
                    width for key in samples if (width := number(key)) is not None
                )
        for property_name, value in properties.items():
            if isinstance(value, dict):
                property_entries.append((property_name, value))
        entries.append((selector, property_entries))
else:
    entries = []

for selector, property_entries in entries:
    if not selector:
        continue
    kinds = [
        value.get("type") or value.get("classification")
        or value.get("kind") or value.get("class")
        for _property_name, value in property_entries
    ]
    normalized = [str(kind).lower() for kind in kinds if kind]
    responsive = any(
        any(token in kind for token in (
            "vw", "calc", "linear", "fluid", "breakpoint", "switched",
        ))
        for kind in normalized
    )
    fixed = bool(normalized) and all(
        "fixed" in kind or kind in {"px", "fixed-px"}
        for kind in normalized
    )
    responsive_properties = []
    for property_name, value in property_entries:
        kind = str(
            value.get("type") or value.get("classification")
            or value.get("kind") or value.get("class") or ""
        ).lower()
        if not any(token in kind for token in (
            "vw", "calc", "linear", "fluid", "breakpoint", "switched",
        )):
            continue
        raw_samples = value.get("samples")
        samples = {}
        if isinstance(raw_samples, dict):
            for sample_width, sample_value in raw_samples.items():
                width = number(sample_width)
                measured = number(sample_value)
                if width is not None and measured is not None:
                    samples[str(width)] = measured
                    sample_widths.add(width)
        responsive_properties.append({
            "name": str(property_name),
            "samples": samples,
        })
    keys.append({
        "selector": selector,
        "expect": "responsive" if responsive else ("fixed" if fixed else "unknown"),
        "responsiveProperties": responsive_properties,
    })

if not keys:
    keys = [
        {"selector": selector, "expect": "unknown"}
        for selector in (
            "main > *", "section", "header", "footer",
            "[class*=section]", "[class*=container]", "[class*=wrap]",
            "[class*=Section]", "[class*=Container]",
        )
    ]

reference_widths = sorted(width for width in sample_widths if 240 <= width <= 2560)
if len(reference_widths) > 4:
    reference_widths = [
        reference_widths[0],
        reference_widths[len(reference_widths) // 3],
        reference_widths[(2 * len(reference_widths)) // 3],
        reference_widths[-1],
    ]
priority_widths = list(reference_widths)
if breakpoint_widths:
    priority_widths.extend([
        max(320, breakpoint_widths[0] - 1),
        min(1920, breakpoint_widths[-1] + 1),
    ])
widths = []
for width in [*priority_widths, *breakpoint_widths]:
    if 240 <= width <= 2560 and width not in widths:
        widths.append(width)
    if len(widths) == 6:
        break
widths.sort()

print(json.dumps({
    "breakpointCount": len(breakpoint_widths),
    "breakpointWidths": breakpoint_widths,
    "widths": widths,
    "keys": keys[:40],
}))
PY
)

BP_COUNT=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["breakpointCount"])' "$PROBE_INPUT")
if [ "${BP_COUNT:-0}" -lt 2 ]; then
  python3 -c 'import json,sys; json.dump({"schemaVersion":2,"status":"skip","reason":"ref not responsive (<2 breakpoints) — probe N/A","breakpoints":int(sys.argv[1])}, open(sys.argv[2],"w"), indent=2)' "${BP_COUNT:-0}" "$OUT"
  echo "resize-behavior: skip (ref not responsive)"
  exit 0
fi

KEYS_JSON=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["keys"], separators=(",",":")))' "$PROBE_INPUT")
WIDTHS=$(python3 -c 'import json,sys; print(" ".join(map(str,json.loads(sys.argv[1])["widths"])))' "$PROBE_INPUT")

# Restrict the source diagnostic to viewport media queries. Preference queries
# such as prefers-reduced-motion and prefers-color-scheme do not imply a frozen
# responsive layout and must not fail this probe.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
RESOLVER="$REPO_ROOT/scripts/extract/find-impl-root.sh"
IMPL_SRC=""
if [ -x "$RESOLVER" ]; then
  IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
  [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT/src" ] && IMPL_SRC="$IMPL_ROOT/src"
fi
JS_ONESHOT=0
if [ -n "$IMPL_SRC" ]; then
  JS_ONESHOT=$(python3 - "$IMPL_SRC" <<'PY'
import os
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
excluded = {"from-ref", "ref-css", "node_modules", "dist", "build", ".next"}
viewport_queries = 0
responsive_handlers = 0
match_media = re.compile(r"\bmatchMedia\s*\(\s*(['\"])(.*?)\1", re.S)
viewport_query = re.compile(r"(?:min-|max-)?width|orientation|aspect-ratio", re.I)
handler = re.compile(
    r"addEventListener\(\s*['\"]resize['\"]|\.addListener\(|ResizeObserver"
    r"|onresize\b|addEventListener\(\s*['\"]change['\"]"
)
for root, dirs, files in os.walk(src):
    if any(segment in excluded for segment in Path(root).parts):
        continue
    dirs[:] = [name for name in dirs if name not in excluded]
    for name in files:
        if Path(name).suffix.lower() not in {
            ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
        }:
            continue
        try:
            text = Path(root, name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        viewport_queries += sum(
            1 for match in match_media.finditer(text)
            if viewport_query.search(match.group(2))
        )
        responsive_handlers += len(handler.findall(text))
print(1 if viewport_queries > 0 and responsive_handlers == 0 else 0)
PY
)
fi

: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME
if ! agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1; then
  python3 -c 'import json,sys; json.dump({"schemaVersion":2,"status":"fail","reason":"impl URL not reachable: "+sys.argv[1]}, open(sys.argv[2],"w"), indent=2)' "$IMPL_URL" "$OUT"
  echo "resize-behavior: fail (impl not reachable)"
  exit 2
fi

EVAL_JS="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/resize.$$.js")"
RAW="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/resize.$$.out")"
trap 'rm -f "$EVAL_JS" "$RAW"; agent-browser --session "$SESSION" close >/dev/null 2>&1 || true' EXIT

cat > "$EVAL_JS" <<'JS'
(() => {
  const keys = __KEYS__;
  const round = value => Math.round(value * 100) / 100;
  const semanticFallbacks = {
    main: '[role=main]',
  };
  const stableFallback = selector => {
    const match = selector.match(
      /^(?:[a-zA-Z][\w-]*)?(?:#(-?[_a-zA-Z][\w-]*))?((?:\.-?[_a-zA-Z][\w-]*)*)$/,
    );
    if (!match) return null;
    if (match[1]) return `#${match[1]}`;
    return match[2] || null;
  };
  const measureProperty = (element, property, rect, style) => {
    const rawProperty = property.trim();
    const normalized = rawProperty.toLowerCase();
    const geometry = {
      width: rect.width,
      height: rect.height,
      x: rect.x,
      left: rect.x,
      y: rect.y,
      top: rect.y,
      right: rect.right,
      bottom: rect.bottom,
    };
    if (Object.prototype.hasOwnProperty.call(geometry, normalized)) {
      return round(geometry[normalized]);
    }
    const cssName = rawProperty
      .replace(/[A-Z]/g, match => `-${match.toLowerCase()}`)
      .toLowerCase();
    const computed = style.getPropertyValue(cssName).trim();
    const numeric = computed.match(/^(-?\d+(?:\.\d+)?)px$/);
    return numeric ? round(Number(numeric[1])) : computed;
  };
  const results = keys.map(key => {
    let elements = [];
    try { elements = [...document.querySelectorAll(key.selector)]; } catch (error) {}
    let resolvedSelector = key.selector;
    if (!elements.length) {
      const fallback = semanticFallbacks[key.selector] || stableFallback(key.selector);
      if (fallback) {
        try { elements = [...document.querySelectorAll(fallback)]; } catch (error) {}
        if (elements.length) resolvedSelector = fallback;
      }
    }
    // Preserve selector identity across viewport samples. Picking the first
    // *visible* match can silently jump from a responsive element that becomes
    // display:none (for example the header nav) to a later footer element and
    // compare unrelated geometry. Reference sizing capture uses the first DOM
    // match, so the probe must retain that same match even when its rect is 0.
    const element = elements[0];
    if (!element) {
      return {
        selector: key.selector,
        resolvedSelector,
        expect: key.expect,
        present: false,
      };
    }
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const measuredProperties = {};
    for (const contract of key.responsiveProperties || []) {
      measuredProperties[contract.name] = measureProperty(
        element, contract.name, rect, style,
      );
    }
    return {
      selector: key.selector,
      resolvedSelector,
      expect: key.expect,
      present: true,
      measuredProperties,
      rect: {
        x: round(rect.x), y: round(rect.y),
        width: round(rect.width), height: round(rect.height),
      },
      style: {
        display: style.display,
        position: style.position,
        flexDirection: style.flexDirection,
        gridTemplateColumns: style.gridTemplateColumns,
        fontSize: style.fontSize,
        paddingInline: style.paddingInline,
        marginInline: style.marginInline,
      },
    };
  });
  return JSON.stringify({
    viewport: { width: window.innerWidth, height: window.innerHeight },
    results,
  });
})()
JS
sed -i.bak "s|__KEYS__|$KEYS_JSON|" "$EVAL_JS" && rm -f "$EVAL_JS.bak"

for WIDTH in $WIDTHS; do
  if ! agent-browser --session "$SESSION" set viewport "$WIDTH" 900 >/dev/null 2>&1; then
    printf '%s\t%s\n' "$WIDTH" "__SET_VIEWPORT_FAILED__" >> "$RAW"
    continue
  fi
  agent-browser --session "$SESSION" wait 150 >/dev/null 2>&1 || true
  RESULT=$(agent-browser --session "$SESSION" eval "$(cat "$EVAL_JS")" 2>/dev/null || true)
  printf '%s\t%s\n' "$WIDTH" "$RESULT" >> "$RAW"
done

python3 - "$RAW" "$OUT" "$PROBE_INPUT" "${JS_ONESHOT:-0}" <<'PY'
import json
import sys
from collections import defaultdict

raw_path, out_path = sys.argv[1], sys.argv[2]
probe_input = json.loads(sys.argv[3])
js_oneshot = sys.argv[4] == "1"

samples = []
errors = []
for line in open(raw_path):
    requested_text, _, payload = line.rstrip("\n").partition("\t")
    requested = int(requested_text)
    if payload == "__SET_VIEWPORT_FAILED__":
        errors.append(f"set viewport {requested} failed")
        continue
    try:
        sample = json.loads(payload)
        if isinstance(sample, str):
            sample = json.loads(sample)
    except (json.JSONDecodeError, TypeError):
        errors.append(f"eval at {requested}px returned no parseable result")
        continue
    if not isinstance(sample, dict) or not isinstance(sample.get("results"), list):
        errors.append(f"eval at {requested}px returned no result list")
        continue
    actual = sample.get("viewport", {}).get("width")
    if not isinstance(actual, (int, float)) or abs(actual - requested) > 2:
        errors.append(f"viewport evidence mismatch: requested {requested}, observed {actual}")
        continue
    sample["requestedWidth"] = requested
    samples.append(sample)

observations = defaultdict(list)
presence_observations = defaultdict(list)
for sample in samples:
    for result in sample["results"]:
        if not isinstance(result, dict):
            continue
        selector = result.get("selector")
        if not selector:
            continue
        presence_observations[selector].append(bool(result.get("present")))
        if result.get("present"):
            result["_requestedWidth"] = sample["requestedWidth"]
            observations[selector].append(result)

def changed(items):
    if len(items) < 2:
        return False
    rect_fields = ("x", "y", "width", "height")
    for field in rect_fields:
        values = [
            item.get("rect", {}).get(field)
            for item in items
            if isinstance(item.get("rect", {}).get(field), (int, float))
        ]
        if len(values) >= 2 and max(values) - min(values) > 1:
            return True
    style_signatures = {
        json.dumps(item.get("style", {}), sort_keys=True)
        for item in items
    }
    return len(style_signatures) > 1

def property_contract_result(items, contract, presence_changed):
    name = contract["name"]
    measured = [
        (item["_requestedWidth"], item.get("measuredProperties", {}).get(name))
        for item in items
        if name in item.get("measuredProperties", {})
    ]
    values = [value for _width, value in measured]
    numeric_values = [value for value in values if isinstance(value, (int, float))]
    if len(numeric_values) == len(values) and len(values) >= 2:
        property_changed = max(numeric_values) - min(numeric_values) > 1
    else:
        property_changed = len({
            json.dumps(value, sort_keys=True) for value in values
        }) > 1

    reference_samples = {
        int(width): value
        for width, value in contract.get("samples", {}).items()
        if isinstance(value, (int, float))
    }
    comparisons = []
    for width, actual in measured:
        expected = reference_samples.get(width)
        if expected is None or not isinstance(actual, (int, float)):
            continue
        tolerance = max(2.0, abs(expected) * 0.05)
        comparisons.append({
            "width": width,
            "expected": expected,
            "actual": actual,
            "tolerance": round(tolerance, 2),
            "withinTolerance": abs(actual - expected) <= tolerance,
        })

    trend_matches = True
    if len(comparisons) >= 2:
        ordered = sorted(comparisons, key=lambda row: row["width"])
        reference_delta = ordered[-1]["expected"] - ordered[0]["expected"]
        actual_delta = ordered[-1]["actual"] - ordered[0]["actual"]
        if abs(reference_delta) > 1:
            trend_matches = (
                reference_delta * actual_delta > 0
                and abs(actual_delta) >= max(1.0, abs(reference_delta) * 0.2)
            )

    required_reference_samples = min(2, len(reference_samples))
    reference_coverage_matches = (
        len(comparisons) >= required_reference_samples
        if required_reference_samples
        else True
    )
    samples_match = all(row["withinTolerance"] for row in comparisons)
    return {
        "name": name,
        "samplesPresent": len(measured),
        "changed": property_changed,
        "presenceChanged": presence_changed,
        "referenceComparisons": comparisons,
        "requiredReferenceSamples": required_reference_samples,
        "referenceCoverageMatch": reference_coverage_matches,
        "referenceSamplesMatch": samples_match,
        "referenceTrendMatch": trend_matches,
        "passed": (
            len(measured) >= 2
            and (property_changed or presence_changed)
            and reference_coverage_matches
            and samples_match
            and trend_matches
        ),
    }

selector_rows = []
for key in probe_input["keys"]:
    items = observations.get(key["selector"], [])
    presence_values = presence_observations.get(key["selector"], [])
    presence_changed = len(set(presence_values)) > 1
    property_rows = [
        property_contract_result(items, contract, presence_changed)
        for contract in key.get("responsiveProperties", [])
    ]
    row_changed = (
        all(row["passed"] for row in property_rows)
        if property_rows
        else changed(items) or presence_changed
    )
    selector_rows.append({
        "selector": key["selector"],
        "resolvedSelectors": sorted({
            item.get("resolvedSelector", key["selector"]) for item in items
        }),
        "expect": key["expect"],
        "samplesPresent": len(items),
        "presenceChanged": presence_changed,
        "responsiveProperties": property_rows,
        "changed": row_changed,
    })

responsive_rows = [row for row in selector_rows if row["expect"] == "responsive"]
evidence_rows = responsive_rows or selector_rows
missing_responsive = [
    row["selector"] for row in responsive_rows if row["samplesPresent"] < 2
]
unchanged_responsive = [
    row["selector"] for row in responsive_rows
    if row["samplesPresent"] >= 2 and not row["changed"]
]
failed_responsive_properties = [
    f'{row["selector"]}:{contract["name"]}'
    for row in responsive_rows
    for contract in row["responsiveProperties"]
    if not contract["passed"]
]
changed_rows = [row for row in evidence_rows if row["changed"]]

if errors or len(samples) < 2:
    status = "fail"
    reason = "multi-viewport browser evidence is incomplete: " + "; ".join(
        errors or [f"only {len(samples)} valid samples"]
    )
elif not observations:
    status = "fail"
    reason = "responsive ref produced zero visible selector evidence in the implementation"
elif missing_responsive:
    status = "fail"
    reason = (
        f"{len(missing_responsive)} responsive selector(s) were not visible at "
        "at least two measured widths"
    )
elif failed_responsive_properties:
    status = "fail"
    reason = (
        f"{len(failed_responsive_properties)} declared responsive property "
        "contract(s) did not change in-property or match reference samples/trend"
    )
elif not changed_rows:
    status = "fail"
    reason = "no measured key selector changed computed geometry/style across widths"
elif js_oneshot and not changed_rows:
    status = "fail"
    reason = "viewport matchMedia is read once and measured layout did not respond"
else:
    status = "pass"
    reason = (
        f"{len(changed_rows)}/{len(evidence_rows)} measured selector(s) changed "
        "computed geometry/style across responsive reference widths"
    )

json.dump({
    "schemaVersion": 2,
    "status": status,
    "breakpoints": probe_input["breakpointCount"],
    "breakpointWidths": probe_input["breakpointWidths"],
    "requestedWidths": probe_input["widths"],
    "validViewportSamples": len(samples),
    "selectors": selector_rows,
    "changedSelectors": [row["selector"] for row in changed_rows],
    "missingResponsiveSelectors": missing_responsive,
    "unchangedResponsiveSelectors": unchanged_responsive,
    "failedResponsiveProperties": failed_responsive_properties,
    "jsOneShotViewportMatchMedia": js_oneshot,
    "errors": errors,
    "reason": reason,
}, open(out_path, "w"), indent=2)
print(f"resize-behavior: {status} ({len(changed_rows)}/{len(evidence_rows)} selectors changed)")
sys.exit(0 if status == "pass" else 1)
PY
