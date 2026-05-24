#!/usr/bin/env bash
# svg-provenance-check.sh — fail when impl SVGs are invented rather than
# sourced from the ref site.
#
# Usage:
#   svg-provenance-check.sh <session> <ref-url> <impl-url> <ref-dir> [max-invented]
#
#
# Detection logic:
#   1. Open ref URL, enumerate every `<svg>` and collect normalized
#      `<path d="...">` strings (also `<polygon points>`, `<circle>`,
#      `<rect>`, `<line>` geometry where present). Build a hash set.
#   2. Open impl URL, enumerate every `<svg>` the same way.
#   3. For each impl SVG with ≥1 geometry child, check whether AT LEAST
#      ONE of its geometry strings is in the ref set. If none are →
#      INVENTED.
#   4. FAIL when invented count > MAX_INVENTED (default 2 — small
#      tolerance for impls that legitimately add a fallback icon or two).
#
# Catches:
#   - invented-icon pattern (e.g. a hand-written IconMark.tsx whose paths
#     exist only to satisfy count parity)
#   - LLM hand-rolling icons that look approximately like the ref but
#     don't share any vertex data
#   - copy-and-edit drift: ref path "M10 5 L20 15..." vs impl "M11 5 L20 16..."
#     The minor edit hashes to a different string and reads as INVENTED.
#     (False-positive risk; mitigated by max-invented tolerance.)
#
# Skips when:
#   - ref has 0 inline SVGs (gate doesn't apply)
#   - agent-browser CLI is missing
#
# Writes:
#   <ref-dir>/svg-provenance.json
#
# Exit 0 on pass/skip, 1 on too many invented SVGs, 2 on setup error.

set -uo pipefail

SESSION="${1:?Usage: svg-provenance-check.sh <session> <ref-url> <impl-url> <ref-dir> [max-invented]}"
REF_URL="${2:?ref-url required}"
IMPL_URL="${3:?impl-url required}"
REF_DIR="${4:?ref-dir required}"
MAX_INVENTED="${5:-scale}"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "svg-provenance: agent-browser CLI missing" >&2
  exit 2
fi
if [ ! -d "$REF_DIR" ]; then
  echo "svg-provenance: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

OUT="$REF_DIR/svg-provenance.json"
REF_SESSION="${SESSION}-svg-ref"
IMPL_SESSION="${SESSION}-svg-impl"
REF_RAW=$(mktemp -t svg-ref.XXXX.json)
IMPL_RAW=$(mktemp -t svg-impl.XXXX.json)
trap 'rm -f "$REF_RAW" "$IMPL_RAW"; agent-browser --session "$REF_SESSION" close >/dev/null 2>&1 || true; agent-browser --session "$IMPL_SESSION" close >/dev/null 2>&1 || true' EXIT

# Probe: collect every <svg>'s geometry strings.
# Normalization: collapse whitespace, lowercase the command letters,
# drop trailing zeros after the decimal point (so "M10.00" and "M10"
# hash the same). Helps absorb minification noise without erasing real
# differences.
PROBE_JS='
(() => {
  const norm = (s) => {
    if (!s) return "";
    return String(s)
      .replace(/[,\s]+/g, " ")
      .replace(/\b0+(\d)/g, "$1")
      .replace(/(\d)\.0+\b/g, "$1")
      .toLowerCase()
      .trim();
  };
  const svgs = Array.from(document.querySelectorAll("svg"));
  const out = svgs.map((svg) => {
    const geoms = [];
    svg.querySelectorAll("path").forEach((p) => {
      const d = p.getAttribute("d");
      if (d) geoms.push("p:" + norm(d));
    });
    svg.querySelectorAll("use").forEach((u) => {
      const href = u.getAttribute("href") || u.getAttribute("xlink:href");
      if (href) geoms.push("use:" + norm(href));
    });
    svg.querySelectorAll("polygon, polyline").forEach((p) => {
      const pts = p.getAttribute("points");
      if (pts) geoms.push("pg:" + norm(pts));
    });
    svg.querySelectorAll("circle").forEach((c) => {
      const cx = c.getAttribute("cx") || "0";
      const cy = c.getAttribute("cy") || "0";
      const r  = c.getAttribute("r")  || "0";
      geoms.push("c:" + norm(`${cx} ${cy} ${r}`));
    });
    svg.querySelectorAll("rect").forEach((r) => {
      const x = r.getAttribute("x") || "0";
      const y = r.getAttribute("y") || "0";
      const w = r.getAttribute("width")  || "0";
      const h = r.getAttribute("height") || "0";
      geoms.push("r:" + norm(`${x} ${y} ${w} ${h}`));
    });
    return {
      cls: svg.className && svg.className.baseVal ? svg.className.baseVal : "",
      id: svg.id || "",
      viewBox: svg.getAttribute("viewBox") || "",
      geomCount: geoms.length,
      geoms,
    };
  });
  return JSON.stringify({ count: svgs.length, svgs: out });
})()
'

probe_url() {
  local session="$1" url="$2" out_file="$3"
  agent-browser --session "$session" open "$url" --wait 1500 >/dev/null 2>&1 || true
  agent-browser --session "$session" eval "$PROBE_JS" > "$out_file" 2>/dev/null || true
}

probe_url "$REF_SESSION" "$REF_URL" "$REF_RAW"
probe_url "$IMPL_SESSION" "$IMPL_URL" "$IMPL_RAW"

python3 - "$OUT" "$REF_RAW" "$IMPL_RAW" "$REF_URL" "$IMPL_URL" "$MAX_INVENTED" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out_path, ref_path, impl_path, ref_url, impl_url, max_inv_str = sys.argv[1:7]
# Tolerance can be an explicit integer or the literal "scale" — when
# "scale", we compute it after reading ref svg count.
max_invented_explicit: int | None = None
if max_inv_str != "scale":
    try:
        max_invented_explicit = int(max_inv_str)
    except ValueError:
        max_invented_explicit = 2  # fallback to old hardcoded default

def read_probe(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {"count": 0, "svgs": [], "error": "probe-missing"}
    for line in reversed(text.strip().splitlines()):
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except Exception:
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                continue
        if isinstance(value, dict):
            return value
    return {"count": 0, "svgs": [], "error": "probe-parse-failed"}

ref = read_probe(ref_path)
impl = read_probe(impl_path)

ref_geom_set: set[str] = set()
for s in ref.get("svgs", []):
    for g in s.get("geoms", []):
        if g:
            ref_geom_set.add(g)

invented: list[dict] = []
matched: list[dict] = []
no_geom: list[dict] = []
for i, s in enumerate(impl.get("svgs", [])):
    geoms = [g for g in s.get("geoms", []) if g]
    entry = {
        "idx": i,
        "id": s.get("id", ""),
        "cls": s.get("cls", ""),
        "viewBox": s.get("viewBox", ""),
        "geomCount": len(geoms),
    }
    if not geoms:
        # No geometry children — typically a `<svg>` used as a container
        # (e.g., a mask root or a `<use>` host). Not invented; not matched.
        no_geom.append(entry)
        continue
    has_match = any(g in ref_geom_set for g in geoms)
    if has_match:
        matched.append(entry)
    else:
        invented.append(entry)

ref_count = ref.get("count", 0)
impl_count = impl.get("count", 0)
invented_count = len(invented)
matched_count = len(matched)
no_geom_count = len(no_geom)

# Compute effective tolerance. Explicit caller override wins; otherwise
# scale with ref svg count so icon-library-heavy sites don't fail on
# 2-3 legit fallbacks.
if max_invented_explicit is not None:
    max_invented = max_invented_explicit
else:
    max_invented = max(2, ref_count // 10)

reasons: list[str] = []

if ref_count == 0:
    status = "skip"
    reasons.append("ref has no inline SVGs — provenance gate does not apply")
elif impl_count == 0:
    # The svg-dom-parity gate already handles this case (count mismatch);
    # this gate skips to avoid double-failing the same root cause.
    status = "skip"
    reasons.append("impl has no inline SVGs — svg-dom-parity handles this, gate skips")
elif invented_count > max_invented:
    status = "fail"
    reasons.append(
        f"{invented_count} impl SVG(s) have no ref-matching geometry "
        f"(threshold: {max_invented}). LLM-invented marks suspected. "
        "Restore the ref's actual SVG path data instead of hand-rolling icons "
        "to satisfy svg-dom-parity count."
    )
else:
    status = "pass"
    if invented_count > 0:
        reasons.append(
            f"informational: {invented_count} impl SVG(s) within tolerance "
            f"({invented_count} ≤ {max_invented}) — likely a legitimate fallback "
            "or minified path drift"
        )

payload = {
    "schemaVersion": 1,
    "status": status,
    "ref": {
        "url": ref_url,
        "svgCount": ref_count,
        "uniqueGeometryStrings": len(ref_geom_set),
    },
    "impl": {
        "url": impl_url,
        "svgCount": impl_count,
        "matchedCount": matched_count,
        "inventedCount": invented_count,
        "noGeomCount": no_geom_count,
        "matched": matched[:10],     # cap so artifact stays small
        "invented": invented[:10],
    },
    "maxInventedAllowed": max_invented,
    "reasons": reasons,
    "nextAction": (
        "Replace the invented SVG path data with the ref's actual path/polygon/"
        "circle/rect strings. Download ref SVGs via their original URLs (see "
        "head.json / extracted.json) and either embed them as inline <svg> with "
        "the exact `d=...` from the ref, or reference them via <img src='/icon-N.svg'>. "
        "Do NOT hand-roll new path data to satisfy the svg-dom-parity COUNT — "
        "that's the IconMark.tsx-style cheat this gate blocks."
        if (status == "fail") else "all impl SVG geometry traces to ref palette"
    ),
    "rule": (
        "Every impl <svg> with geometry children must share at least one normalized "
        "path/polygon/circle/rect geometry string with some ref <svg>. Otherwise it "
        "is treated as invented. svg-dom-parity enforces count + section presence; "
        "this gate enforces that the inline geometry itself was sourced from the ref."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "invented": invented_count, "matched": matched_count, "out": out_path}, ensure_ascii=False))
sys.exit({"pass": 0, "skip": 0, "fail": 1}.get(status, 2))
PY
