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
#   3. include downloaded animation JSON/dotLottie archive under public/, src/,
#      or app/, AND
#   4. when an impl URL is provided: actually ready a Lottie container and
#      advance frame/progress in the browser — proving the runtime is wired,
#      not just imported or replaced with a fallback SVG.
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
if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
  elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

write_json() {
  local status="$1" ref_detected="$2" impl_root="$3" package_found="$4" runtime_found="$5" json_count="$6" reasons="$7"
  local runtime_proof="${8:-not-attempted}" candidate_count="${9:-0}" animating_count="${10:-0}"
  "$PYTHON_BIN" - "$OUT" "$status" "$ref_detected" "$impl_root" "$package_found" "$runtime_found" "$json_count" "$reasons" "$runtime_proof" "$candidate_count" "$animating_count" <<'PY'
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
        # browser proof skipped; runtime-pass = container reached ready state
        # and frame/progress advanced; runtime-fail = container exists but
        # stayed fallback/static;
        # no-candidates = ref signaled lottie but impl has no container.
        "status": runtime_proof,
        "candidateCount": int(candidate_count),
        "animatingCount": int(animating_count),
    },
    "reasons": [r for r in reasons.split("|") if r],
    "rule": (
        "When ref artifacts contain Lottie/bodymovin/dotlottie evidence, the impl "
        "must use a real Lottie runtime package and local animation JSON/dotLottie "
        "archive, AND when an impl URL is supplied, at least one Lottie container "
        "must reach ready state and advance frame/progress. Fallback SVG/canvas "
        "surfaces and CSS/GSAP placeholder motion are not equivalent."
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
  required-media.json \
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
  PACKAGE_FOUND=$("$PYTHON_BIN" - "$PKG" <<'PY'
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
PROOF_JSON=""
RUNTIME_SESSION=""
cleanup() {
  rm -f "$SOURCE_TMP"
  if [ -n "${PROOF_JSON:-}" ]; then
    rm -f "$PROOF_JSON"
  fi
  if [ -n "${RUNTIME_SESSION:-}" ] && command -v agent-browser >/dev/null 2>&1; then
    # Keep EXIT cleanup bounded. A stale browser session should not make the
    # static lottie check or pytest timeout after the result has been written.
    "$PYTHON_BIN" - "$RUNTIME_SESSION" <<'PY' >/dev/null 2>&1 || true
import subprocess
import sys

try:
    subprocess.run(
        ["agent-browser", "--session", sys.argv[1], "close"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
except Exception:
    pass
PY
  fi
}
trap cleanup EXIT
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
  JSON_COUNT=$("$PYTHON_BIN" - "${JSON_DIRS[@]}" <<'PY'
import json
import sys
import zipfile
from pathlib import Path

skip_names = {
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vercel.json",
}

def is_lottie_json(data):
    return isinstance(data, dict) and isinstance(data.get("layers"), list) and (
        "v" in data or "fr" in data or "ip" in data or "op" in data
    )


def is_dotlottie_archive(path):
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                return False
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception:
                return False
            animations = manifest.get("animations") if isinstance(manifest, dict) else None
            candidate_paths = []
            if isinstance(animations, list):
                for item in animations:
                    if not isinstance(item, dict):
                        continue
                    raw_path = item.get("path") or item.get("id")
                    if isinstance(raw_path, str) and raw_path:
                        candidate_paths.append(raw_path if raw_path.endswith(".json") else f"animations/{raw_path}.json")
            if not candidate_paths:
                candidate_paths = [name for name in names if name.startswith("animations/") and name.endswith(".json")]
            for name in candidate_paths:
                if name not in names:
                    continue
                try:
                    if is_lottie_json(json.loads(zf.read(name).decode("utf-8"))):
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


count = 0
for root_arg in sys.argv[1:]:
    root = Path(root_arg)
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".lottie" and is_dotlottie_archive(path):
            count += 1
            continue
        if path.suffix != ".json" or path.name in skip_names:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Bodymovin/Lottie JSON is data-shaped, not merely extension-shaped:
        # it has layers plus timeline/version metadata. This avoids accepting
        # arbitrary app config JSON as proof that animation data was copied.
        if is_lottie_json(data):
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

  # Open impl, give hydration time to mount lottie containers and call
  # loadAnimation. Then snapshot status/frame/progress; fallback surfaces are
  # candidates, but never proof of animation.
  agent-browser --session "$RUNTIME_SESSION" open "$IMPL_URL" >/dev/null 2>&1 || true
  sleep 2  # open --wait is not a supported flag; settle explicitly

  agent-browser --session "$RUNTIME_SESSION" eval '
(async () => {
  const num = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const readNumber = (obj, key) => {
    if (!obj) return null;
    try {
      const value = obj[key];
      return typeof value === "function" ? num(value.call(obj)) : num(value);
    } catch (error) {
      return null;
    }
  };
  const playerFor = async (el) => {
    try {
      if (typeof el.getLottie === "function") return await el.getLottie();
    } catch (error) {}
    try {
      if (typeof el.getAnimation === "function") return await el.getAnimation();
    } catch (error) {}
    return null;
  };
  const sampleMotion = async (el) => {
    const player = await playerFor(el);
    return {
      frame: num(el.getAttribute("data-lottie-current-frame")),
      progress: num(el.getAttribute("data-lottie-progress")),
      totalFrames: num(el.getAttribute("data-lottie-total-frames")),
      nativeFrame: readNumber(el, "frame") ?? readNumber(player, "frame"),
      nativeCurrentFrame: readNumber(el, "currentFrame") ?? readNumber(player, "currentFrame"),
      nativeCurrentTime: readNumber(el, "currentTime") ?? readNumber(player, "currentTime"),
      nativeProgress: readNumber(el, "progress") ?? readNumber(player, "progress"),
    };
  };
  const candidates = [
    ...document.querySelectorAll("lottie-player, dotlottie-player"),
    ...document.querySelectorAll("[data-lottie], [data-animation-path], [data-lottie-src]"),
    ...document.querySelectorAll("[class*=\"lottie\" i]"),
  ];
  // Deduplicate (same node could match multiple selectors).
  const uniq = Array.from(new Set(candidates));
  window.__lottieSnap = await Promise.all(uniq.map(async (el) => {
    const motion = await sampleMotion(el);
    return {
      tag: el.tagName.toLowerCase(),
      hasSvg: !!el.querySelector("svg"),
      hasCanvas: !!el.querySelector("canvas"),
      state: String(el.getAttribute("data-lottie") || el.getAttribute("data-lottie-status") || "").toLowerCase(),
      frame: motion.frame,
      progress: motion.progress,
      totalFrames: motion.totalFrames,
      nativeFrame: motion.nativeFrame,
      nativeCurrentFrame: motion.nativeCurrentFrame,
      nativeCurrentTime: motion.nativeCurrentTime,
      nativeProgress: motion.nativeProgress,
      childCount: el.childElementCount,
      innerHTMLLen: el.innerHTML.length,
    };
  }));
  return JSON.stringify({ count: uniq.length });
})()
' > "$PROOF_JSON" 2>/dev/null || true

  # Scroll-bound Lottie surfaces do not advance while the viewport is idle.
  # Drive a bounded page scroll between snapshots when possible, then wait for
  # the generated scroll handler or native player to publish frame/progress.
  agent-browser --session "$RUNTIME_SESSION" eval '
(async () => {
  const doc = document.documentElement;
  const maxY = Math.max(0, (doc.scrollHeight || 0) - window.innerHeight);
  const startY = window.scrollY || window.pageYOffset || 0;
  let targetY = startY;
  if (maxY > 0) {
    const step = Math.max(120, Math.min(600, Math.round(maxY * 0.25)));
    targetY = Math.min(maxY, startY + step);
    if (targetY === startY && startY > 0) targetY = Math.max(0, startY - step);
    window.scrollTo(0, targetY);
    window.dispatchEvent(new Event("scroll"));
    document.dispatchEvent(new Event("scroll"));
  }
  window.__lottieScrollProbe = { maxY, startY, targetY, scrolled: targetY !== startY };
  return JSON.stringify(window.__lottieScrollProbe);
})()
' >/dev/null 2>&1 || true

  agent-browser --session "$RUNTIME_SESSION" wait 1000 >/dev/null 2>&1 || true

  # Re-probe and diff against the snapshot. A valid runtime proof needs ready
  # state plus frame/progress delta. SVG/canvas alone can be a static fallback.
  agent-browser --session "$RUNTIME_SESSION" eval '
(async () => {
  const num = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const readNumber = (obj, key) => {
    if (!obj) return null;
    try {
      const value = obj[key];
      return typeof value === "function" ? num(value.call(obj)) : num(value);
    } catch (error) {
      return null;
    }
  };
  const playerFor = async (el) => {
    try {
      if (typeof el.getLottie === "function") return await el.getLottie();
    } catch (error) {}
    try {
      if (typeof el.getAnimation === "function") return await el.getAnimation();
    } catch (error) {}
    return null;
  };
  const sampleMotion = async (el) => {
    const player = await playerFor(el);
    return {
      frame: num(el.getAttribute("data-lottie-current-frame")),
      progress: num(el.getAttribute("data-lottie-progress")),
      totalFrames: num(el.getAttribute("data-lottie-total-frames")),
      nativeFrame: readNumber(el, "frame") ?? readNumber(player, "frame"),
      nativeCurrentFrame: readNumber(el, "currentFrame") ?? readNumber(player, "currentFrame"),
      nativeCurrentTime: readNumber(el, "currentTime") ?? readNumber(player, "currentTime"),
      nativeProgress: readNumber(el, "progress") ?? readNumber(player, "progress"),
    };
  };
  const changed = (value, before) => value !== null && before !== null && value !== before;
  const candidates = [
    ...document.querySelectorAll("lottie-player, dotlottie-player"),
    ...document.querySelectorAll("[data-lottie], [data-animation-path], [data-lottie-src]"),
    ...document.querySelectorAll("[class*=\"lottie\" i]"),
  ];
  const uniq = Array.from(new Set(candidates));
  const before = window.__lottieSnap || [];
  let animating = 0;
  let fallbackCount = 0;
  let readyCount = 0;
  let advancedCount = 0;
  await Promise.all(uniq.map(async (el, i) => {
    const b = before[i] || {};
    const state = String(el.getAttribute("data-lottie") || el.getAttribute("data-lottie-status") || "").toLowerCase();
    const motion = await sampleMotion(el);
    const hasSvg = !!el.querySelector("svg");
    const hasCanvas = !!el.querySelector("canvas");
    const childCount = el.childElementCount;
    const innerHTMLLen = el.innerHTML.length;
    const isFallback = state === "fallback" || el.matches("[data-lottie=\"fallback\"], [data-lottie-status=\"fallback\"]");
    const isReady = !isFallback && (
      state === "ready" ||
      state === "loaded" ||
      el.tagName.toLowerCase().includes("lottie-player") ||
      el.tagName.toLowerCase().includes("dotlottie-player") ||
      ((motion.totalFrames || 0) > 0 && (hasSvg || hasCanvas))
    );
    const advanced = isReady && (
      changed(motion.frame, b.frame) ||
      changed(motion.progress, b.progress) ||
      changed(motion.nativeFrame, b.nativeFrame) ||
      changed(motion.nativeCurrentFrame, b.nativeCurrentFrame) ||
      changed(motion.nativeCurrentTime, b.nativeCurrentTime) ||
      changed(motion.nativeProgress, b.nativeProgress)
    );
    if (isFallback) fallbackCount += 1;
    if (isReady) readyCount += 1;
    if (advanced) advancedCount += 1;
    if (advanced) animating += 1;
  }));
  return JSON.stringify({ count: uniq.length, animating, fallbackCount, readyCount, advancedCount });
})()
' > "$PROOF_JSON" 2>/dev/null || true

  FALLBACK_COUNT=0
  READY_COUNT=0
  ADVANCED_COUNT=0
  if [ -s "$PROOF_JSON" ]; then
    # agent-browser eval prints the eval result. Recent agent-browser
    # versions wrap the JSON string return in outer quotes (e.g.
    # `"{\"count\":1}"`), so the leading `{` no longer appears at column
    # zero. Accept both forms — line starts with `{` (legacy) OR `"{`
    # (JSON-string-wrapped) — then unwrap the outer quotes if present.
    PROOF_LINE=$(grep -E '^\s*("?\{|\{)' "$PROOF_JSON" | head -1 || true)
    if [ -n "$PROOF_LINE" ]; then
      PROOF_LINE=$("$PYTHON_BIN" -c "import json,sys; v=sys.argv[1].strip(); o=json.loads(v); print(json.dumps(json.loads(o) if isinstance(o,str) else o))" "$PROOF_LINE" 2>/dev/null || echo "$PROOF_LINE")
      CANDIDATE_COUNT=$("$PYTHON_BIN" -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('count',0))" "$PROOF_LINE" 2>/dev/null || echo 0)
      ANIMATING_COUNT=$("$PYTHON_BIN" -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('animating',0))" "$PROOF_LINE" 2>/dev/null || echo 0)
      FALLBACK_COUNT=$("$PYTHON_BIN" -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('fallbackCount',0))" "$PROOF_LINE" 2>/dev/null || echo 0)
      READY_COUNT=$("$PYTHON_BIN" -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('readyCount',0))" "$PROOF_LINE" 2>/dev/null || echo 0)
      ADVANCED_COUNT=$("$PYTHON_BIN" -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('advancedCount', d.get('animating',0)))" "$PROOF_LINE" 2>/dev/null || echo 0)
    fi
  fi
  ANIMATING_COUNT="$ADVANCED_COUNT"

  if [ "${CANDIDATE_COUNT:-0}" -eq 0 ]; then
    RUNTIME_PROOF="no-candidates"
    REASONS+=("ref signaled Lottie but impl has no candidate container (selectors: lottie-player, [data-lottie], [class*=lottie])")
  elif [ "${ADVANCED_COUNT:-0}" -eq 0 ]; then
    RUNTIME_PROOF="runtime-fail"
    if [ "${FALLBACK_COUNT:-0}" -gt 0 ]; then
      REASONS+=("$FALLBACK_COUNT Lottie fallback container(s) found; fallback SVG/canvas is not runtime proof")
    elif [ "${READY_COUNT:-0}" -eq 0 ]; then
      REASONS+=("$CANDIDATE_COUNT Lottie container(s) found but none reached ready state")
    else
      REASONS+=("$READY_COUNT Lottie container(s) reached ready state but none advanced frame/progress within 1.5s")
    fi
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
