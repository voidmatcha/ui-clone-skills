#!/usr/bin/env bash
# lottie-runtime-check.sh — fail when ref Lottie/bodymovin evidence is not
# implemented with a real runtime plus local animation JSON.
#
# Usage:
#   lottie-runtime-check.sh <ref-dir> [<impl-dir>] [<impl-url>]
#
# Reads ref artifacts for lottie/bodymovin/dotlottie signals. If none are
# present, writes status=skip and exits 0. If a signal is present, the impl must:
#   1. declare a Lottie runtime package in package.json,
#   2. use that runtime from source,
#   3. include downloaded animation JSON under public/, src/, or app/, AND
#   4. when an impl URL is provided: actually paint a Lottie container in the
#      browser within 1.5s (gain an svg/canvas child OR mutate its
#      innerHTML/childCount) — proving the runtime is wired, not just imported.
#
# Failure modes the runtime-proof catches that static checks miss:
#   - package + import present, but never instantiated (dead import)
#   - container rendered with `data-lottie="..."` attribute but no JS that
#     calls loadAnimation
#   - import behind a feature flag / SSR-only branch that never executes in
#     the impl's deploy mode
#   - container exists but lottie JSON path is wrong → silently no-ops
#
#
# Writes:
#   <ref-dir>/lottie-runtime.json
#
# Exit 0 on pass/skip, 1 on missing runtime or JSON or no animating
# container, 2 on setup error.
set -uo pipefail

REF_DIR="${1:?Usage: lottie-runtime-check.sh <ref-dir> [<impl-dir>] [<impl-url>]}"
IMPL_ARG="${2:-}"
IMPL_URL="${3:-}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/lottie-runtime.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

write_json() {
  local status="$1" ref_detected="$2" impl_root="$3" package_found="$4" runtime_found="$5" json_count="$6" reasons="$7"
  local runtime_proof="${8:-not-attempted}" candidate_count="${9:-0}" animating_count="${10:-0}"
  python3 - "$OUT" "$status" "$ref_detected" "$impl_root" "$package_found" "$runtime_found" "$json_count" "$reasons" "$runtime_proof" "$candidate_count" "$animating_count" <<'PY'
import json
import sys
from pathlib import Path

(out, status, ref_detected, impl_root, package_found, runtime_found,
 json_count, reasons, runtime_proof, candidate_count, animating_count) = sys.argv[1:12]
payload = {
    "schemaVersion": 2,
    "status": status,
    "refDetected": ref_detected == "true",
    "implRoot": impl_root,
    "packageFound": package_found == "true",
    "runtimeUsageFound": runtime_found == "true",
    "jsonFound": int(json_count) > 0,
    "jsonCount": int(json_count),
    "runtimeProof": {
        # not-attempted = no impl URL passed; static = package+source ok,
        # browser proof skipped; runtime-pass = container painted svg/canvas
        # within 1.5s; runtime-fail = container exists but never animated;
        # no-candidates = ref signaled lottie but impl has no container.
        "status": runtime_proof,
        "candidateCount": int(candidate_count),
        "animatingCount": int(animating_count),
    },
    "reasons": [r for r in reasons.split("|") if r],
    "rule": (
        "When ref artifacts contain Lottie/bodymovin/dotlottie evidence, the impl "
        "must use a real Lottie runtime package and local animation JSON, AND when "
        "an impl URL is supplied, at least one Lottie container must paint "
        "svg/canvas or mutate within 1.5s — proving the runtime is wired, not just "
        "imported. CSS/GSAP placeholder motion is not equivalent."
    ),
}
Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

REF_DETECTED="false"
REF_FILES=()
for name in \
  animation-runtime-dump.json \
  bundle-map.json \
  external-sdks.json \
  transition-spec.json \
  canvas-webgl-detection.json \
  interactions-detected.json \
  assets.json \
  extracted.json; do
  [ -f "$REF_DIR/$name" ] && REF_FILES+=("$REF_DIR/$name")
done

if [ "${#REF_FILES[@]}" -gt 0 ] && grep -Eiq 'lottie|bodymovin|dotlottie|lottie-player' "${REF_FILES[@]}" 2>/dev/null; then
  REF_DETECTED="true"
fi

if [ "$REF_DETECTED" != "true" ]; then
  write_json skip false "" false false 0 "no Lottie/bodymovin/dotlottie signal in ref artifacts"
  echo "lottie-runtime: SKIP (no ref signal)"
  exit 0
fi

IMPL_ROOT=""
if [ -n "$IMPL_ARG" ]; then
  if [ -f "$IMPL_ARG/package.json" ]; then
    IMPL_ROOT="$(cd "$IMPL_ARG" && pwd)"
  elif [ "$(basename "$IMPL_ARG")" = "src" ] || [ "$(basename "$IMPL_ARG")" = "app" ]; then
    IMPL_ROOT="$(cd "$IMPL_ARG/.." && pwd)"
  elif [ -d "$IMPL_ARG" ]; then
    IMPL_ROOT="$(cd "$IMPL_ARG" && pwd)"
  fi
fi

if [ -z "$IMPL_ROOT" ]; then
  RESOLVER="${PLUGIN_ROOT:-$ROOT_DIR}/scripts/extract/find-impl-root.sh"
  if [ -x "$RESOLVER" ]; then
    RESOLVED=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | sed -n '1p' || true)
    if [ -n "$RESOLVED" ] && [ -d "$RESOLVED" ]; then
      IMPL_ROOT="$RESOLVED"
    fi
  fi
fi

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  write_json fail true "" false false 0 "impl directory not found"
  echo "lottie-runtime: FAIL (impl directory not found)" >&2
  exit 1
fi

PKG="$IMPL_ROOT/package.json"
PACKAGE_FOUND="false"
if [ -f "$PKG" ]; then
  PACKAGE_FOUND=$(python3 - "$PKG" <<'PY'
import json
import sys

packages = {
    "lottie-web",
    "lottie-react",
    "@lottiefiles/react-lottie-player",
    "@lottiefiles/lottie-player",
    "@dotlottie/react-player",
    "@dotlottie/dotlottie-js",
}
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        pkg = json.load(fh)
    installed = set((pkg.get("dependencies") or {}).keys())
    installed.update((pkg.get("devDependencies") or {}).keys())
    print("true" if installed.intersection(packages) else "false")
except Exception:
    print("false")
PY
)
fi

SOURCE_TMP="$(mktemp)"
trap 'rm -f "$SOURCE_TMP"' EXIT
SOURCE_DIRS=()
[ -d "$IMPL_ROOT/src" ] && SOURCE_DIRS+=("$IMPL_ROOT/src")
[ -d "$IMPL_ROOT/app" ] && SOURCE_DIRS+=("$IMPL_ROOT/app")
if [ "${#SOURCE_DIRS[@]}" -gt 0 ]; then
  grep -RIEih \
    --include='*.tsx' --include='*.ts' --include='*.jsx' --include='*.js' \
    'loadAnimation|useLottie|<[A-Za-z0-9_.]*(Lottie|Player)|lottie-player|dotlottie|DotLottie|new[[:space:]]+DotLottie' \
    "${SOURCE_DIRS[@]}" > "$SOURCE_TMP" 2>/dev/null || true
fi
RUNTIME_FOUND="false"
[ -s "$SOURCE_TMP" ] && RUNTIME_FOUND="true"

JSON_DIRS=()
[ -d "$IMPL_ROOT/public" ] && JSON_DIRS+=("$IMPL_ROOT/public")
[ -d "$IMPL_ROOT/src" ] && JSON_DIRS+=("$IMPL_ROOT/src")
[ -d "$IMPL_ROOT/app" ] && JSON_DIRS+=("$IMPL_ROOT/app")
JSON_COUNT=0
if [ "${#JSON_DIRS[@]}" -gt 0 ]; then
  JSON_COUNT=$(python3 - "${JSON_DIRS[@]}" <<'PY'
import json
import sys
from pathlib import Path

skip_names = {
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vercel.json",
}

count = 0
for root_arg in sys.argv[1:]:
    root = Path(root_arg)
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".lottie":
            count += 1
            continue
        if path.suffix != ".json" or path.name in skip_names:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        # Bodymovin/Lottie JSON is data-shaped, not merely extension-shaped:
        # it has layers plus timeline/version metadata. This avoids accepting
        # arbitrary app config JSON as proof that animation data was copied.
        if isinstance(data.get("layers"), list) and (
            "v" in data or "fr" in data or "ip" in data or "op" in data
        ):
            count += 1
print(count)
PY
)
fi

REASONS=()
[ "$PACKAGE_FOUND" = "true" ] || REASONS+=("runtime package missing")
[ "$RUNTIME_FOUND" = "true" ] || REASONS+=("runtime usage missing in source")
[ "${JSON_COUNT:-0}" -gt 0 ] || REASONS+=("animation JSON missing")

#
# Skipped when:
#   - no impl URL passed (backward compat with old positional callers)
#   - agent-browser CLI is missing
#   - static checks already failed (no point proving a broken setup runs)
RUNTIME_PROOF="not-attempted"
CANDIDATE_COUNT=0
ANIMATING_COUNT=0

if [ "${#REASONS[@]}" -eq 0 ] && [ -n "$IMPL_URL" ] && command -v agent-browser >/dev/null 2>&1; then
  RUNTIME_SESSION="lottie-runtime-proof-$$"
  PROOF_JSON="$(mktemp -t lottie-proof.XXXX.json)"
  trap 'rm -f "$PROOF_JSON" "$SOURCE_TMP"; agent-browser --session "$RUNTIME_SESSION" close >/dev/null 2>&1 || true' EXIT

  # Open impl, give hydration 1.5s to mount lottie containers and call
  # loadAnimation. Then probe: count candidates and how many show
  # svg/canvas children right now (initial state).
  agent-browser --session "$RUNTIME_SESSION" open "$IMPL_URL" --wait 1500 >/dev/null 2>&1 || true

  agent-browser --session "$RUNTIME_SESSION" eval '
(() => {
  const candidates = [
    ...document.querySelectorAll("lottie-player, dotlottie-player"),
    ...document.querySelectorAll("[data-lottie], [data-animation-path], [data-lottie-src]"),
    ...document.querySelectorAll("[class*=\"lottie\" i]"),
  ];
  // Deduplicate (same node could match multiple selectors).
  const uniq = Array.from(new Set(candidates));
  window.__lottieSnap = uniq.map((el) => ({
    tag: el.tagName.toLowerCase(),
    hasSvg: !!el.querySelector("svg"),
    hasCanvas: !!el.querySelector("canvas"),
    childCount: el.childElementCount,
    innerHTMLLen: el.innerHTML.length,
  }));
  return JSON.stringify({ count: uniq.length });
})()
' > "$PROOF_JSON" 2>/dev/null || true

  # Wait 1000ms for the runtime to advance one tick (lottie default is
  # 60fps; a single frame requires svg/canvas to be painted by now).
  agent-browser --session "$RUNTIME_SESSION" wait 1000 >/dev/null 2>&1 || true

  # Re-probe and diff against the snapshot. Animating = current state
  # has svg/canvas child OR child count / innerHTML length changed.
  agent-browser --session "$RUNTIME_SESSION" eval '
(() => {
  const candidates = [
    ...document.querySelectorAll("lottie-player, dotlottie-player"),
    ...document.querySelectorAll("[data-lottie], [data-animation-path], [data-lottie-src]"),
    ...document.querySelectorAll("[class*=\"lottie\" i]"),
  ];
  const uniq = Array.from(new Set(candidates));
  const before = window.__lottieSnap || [];
  let animating = 0;
  uniq.forEach((el, i) => {
    const b = before[i] || {};
    const hasSvg = !!el.querySelector("svg");
    const hasCanvas = !!el.querySelector("canvas");
    const childCount = el.childElementCount;
    const innerHTMLLen = el.innerHTML.length;
    const isAnimating = hasSvg || hasCanvas ||
      (childCount !== b.childCount) ||
      (innerHTMLLen !== b.innerHTMLLen);
    if (isAnimating) animating += 1;
  });
  return JSON.stringify({ count: uniq.length, animating });
})()
' > "$PROOF_JSON" 2>/dev/null || true

  if [ -s "$PROOF_JSON" ]; then
    # agent-browser eval prints the eval result; strip everything but
    # the JSON line and parse it.
    PROOF_LINE=$(grep -E '^\s*\{' "$PROOF_JSON" | head -1 || true)
    if [ -n "$PROOF_LINE" ]; then
      CANDIDATE_COUNT=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('count',0))" "$PROOF_LINE" 2>/dev/null || echo 0)
      ANIMATING_COUNT=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('animating',0))" "$PROOF_LINE" 2>/dev/null || echo 0)
    fi
  fi

  if [ "${CANDIDATE_COUNT:-0}" -eq 0 ]; then
    RUNTIME_PROOF="no-candidates"
    REASONS+=("ref signaled Lottie but impl has no candidate container (selectors: lottie-player, [data-lottie], [class*=lottie])")
  elif [ "${ANIMATING_COUNT:-0}" -eq 0 ]; then
    RUNTIME_PROOF="runtime-fail"
    REASONS+=("$CANDIDATE_COUNT Lottie container(s) found but none painted svg/canvas or mutated within 1.5s — loadAnimation likely not wired")
  else
    RUNTIME_PROOF="runtime-pass"
  fi
elif [ "${#REASONS[@]}" -eq 0 ] && [ -n "$IMPL_URL" ]; then
  RUNTIME_PROOF="agent-browser-missing"
elif [ "${#REASONS[@]}" -eq 0 ]; then
  RUNTIME_PROOF="static-only"
fi

if [ "${#REASONS[@]}" -eq 0 ]; then
  write_json pass true "$IMPL_ROOT" true true "$JSON_COUNT" "" "$RUNTIME_PROOF" "$CANDIDATE_COUNT" "$ANIMATING_COUNT"
  if [ "$RUNTIME_PROOF" = "runtime-pass" ]; then
    echo "lottie-runtime: PASS (runtime-proof: $ANIMATING_COUNT/$CANDIDATE_COUNT containers animating)"
  else
    echo "lottie-runtime: PASS ($RUNTIME_PROOF)"
  fi
  exit 0
fi

REASON_STR=$(printf '%s|' "${REASONS[@]}")
write_json fail true "$IMPL_ROOT" "$PACKAGE_FOUND" "$RUNTIME_FOUND" "${JSON_COUNT:-0}" "$REASON_STR" "$RUNTIME_PROOF" "$CANDIDATE_COUNT" "$ANIMATING_COUNT"
echo "lottie-runtime: FAIL (${REASON_STR%|})" >&2
exit 1
