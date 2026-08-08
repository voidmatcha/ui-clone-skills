#!/usr/bin/env bash
# preview-runtime-health-check.sh — browser-level health guard for generated previews.
#
# Catches the failures that section pixels can miss:
#   1. impl build assets in <head> rewritten to the reference origin
#   2. mobile/tablet horizontal overflow after viewport resize
#   3. scroll-state/header transitions present on the reference but absent on impl
#
# Usage: preview-runtime-health-check.sh <session> <ref-url> <impl-url> <ref-dir>
# Output: <ref-dir>/preview-runtime-health.json
set -euo pipefail

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: preview-runtime-health-check.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "preview-runtime-health-check: agent-browser not found" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
VIEWPORTS="${PREVIEW_RUNTIME_HEALTH_VIEWPORTS:-390x844,768x1024,1280x800}"
WAIT_MS="${PREVIEW_RUNTIME_HEALTH_WAIT_MS:-700}"
OVERFLOW_TOLERANCE_PX="${PREVIEW_RUNTIME_HEALTH_OVERFLOW_TOLERANCE_PX:-8}"
AGENT_BROWSER_TIMEOUT_SEC="${PREVIEW_RUNTIME_HEALTH_AGENT_BROWSER_TIMEOUT_SEC:-25}"
OUT_PATH="$REF_DIR/preview-runtime-health.json"
mkdir -p "$REF_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUN_WITH_TIMEOUT="$REPO_ROOT/scripts/lib/run_with_timeout.py"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/preview-runtime-health.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

REF_ORIGIN="$($PYTHON_BIN - "$REF_URL" <<'PY'
from urllib.parse import urlparse
import sys
u = urlparse(sys.argv[1])
print(f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else "")
PY
)"
REF_ORIGIN_JSON="$($PYTHON_BIN - "$REF_ORIGIN" <<'PY'
import json, sys
print(json.dumps(sys.argv[1]))
PY
)"

write_probe_error() {
  local out_file="$1" label="$2" url="$3" width="$4" height="$5" message="$6"
  "$PYTHON_BIN" - "$out_file" "$label" "$url" "$width" "$height" "$message" <<'PY'
import json
import sys
from pathlib import Path
out, label, url, width, height, message = sys.argv[1:]
Path(out).write_text(
    json.dumps(
        {
            "label": label,
            "url": url,
            "viewport": {"width": int(width), "height": int(height)},
            "probeError": message,
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
PY
}

agent_browser() {
  "$PYTHON_BIN" "$RUN_WITH_TIMEOUT" "${AGENT_BROWSER_TIMEOUT_SEC}s" agent-browser "$@"
}

probe_url() {
  local label="$1" url="$2" width="$3" height="$4" out_file="$5"
  local probe_session="${SESSION}-${label}-${width}x${height}"

  if ! agent_browser --session "$probe_session" set viewport "$width" "$height" >/dev/null 2>&1; then
    write_probe_error "$out_file" "$label" "$url" "$width" "$height" "agent-browser set viewport failed"
    return 0
  fi
  local open_warning=""
  if ! agent_browser --session "$probe_session" open "$url" >/dev/null 2>&1; then
    open_warning="agent-browser open failed or timed out; continued with DOM probe"
  fi

  if ! cat <<JS | agent_browser --session "$probe_session" eval --stdin >"$out_file" 2>"$out_file.stderr" # agent-browser eval subcommand (timeout wrapper), not bash eval
(async () => {
  const refOrigin = ${REF_ORIGIN_JSON};
  const waitMs = Number(${WAIT_MS});
  const overflowTolerancePx = Number(${OVERFLOW_TOLERANCE_PX});
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  await sleep(waitMs);

  const pageOrigin = location.origin;
  const toUrl = (raw) => {
    try { return new URL(raw, location.href); } catch { return null; }
  };
  const isLocalBuildAsset = (u) => {
    if (!u) return false;
    return /\/(?:assets|static|build|_next)\//.test(u.pathname) || /\.(?:css|js|mjs)(?:$|[?#])/i.test(u.href);
  };
  const headAssets = Array.from(document.head ? document.head.querySelectorAll('script[src],link[href]') : []).map((el) => {
    const raw = el.getAttribute('src') || el.getAttribute('href') || '';
    const u = toUrl(raw);
    const localBuildAsset = isLocalBuildAsset(u);
    const headAssetOnReferenceOrigin = Boolean(
      u && refOrigin && refOrigin !== pageOrigin && u.origin === refOrigin && localBuildAsset
    );
    return {
      tag: el.tagName.toLowerCase(),
      rel: el.getAttribute('rel') || '',
      raw,
      url: u ? u.href : raw,
      origin: u ? u.origin : '',
      pathname: u ? u.pathname : '',
      localBuildAsset,
      headAssetOnReferenceOrigin,
    };
  });
  const suspectHeadAssets = headAssets.filter((a) => a.headAssetOnReferenceOrigin);

  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const doc = document.documentElement;
  const body = document.body;
  const scrollWidth = Math.max(document.documentElement ? document.documentElement.scrollWidth : 0, body ? body.scrollWidth : 0);
  const scrollHeight = Math.max(document.documentElement ? document.documentElement.scrollHeight : 0, body ? body.scrollHeight : 0);
  const overflowPx = Math.max(0, scrollWidth - viewportWidth);
  const overflowElements = Array.from(body ? body.querySelectorAll('*') : [])
    .map((el) => {
      const r = el.getBoundingClientRect();
      const cls = typeof el.className === 'string' ? el.className : '';
      return {
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        className: cls.slice(0, 160),
        left: Math.round(r.left),
        right: Math.round(r.right),
        width: Math.round(r.width),
      };
    })
    .filter((r) => r.width > viewportWidth + overflowTolerancePx || r.right > viewportWidth + overflowTolerancePx)
    .sort((a, b) => Math.max(b.width, b.right) - Math.max(a.width, a.right))
    .slice(0, 12);

  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };
  const stableClassName = (el) => {
    if (!el) return '';
    const cls = el.className;
    if (typeof cls === 'string') return cls;
    return cls && typeof cls.baseVal === 'string' ? cls.baseVal : '';
  };
  const headerCandidates = () => {
    let nodes = [];
    try {
      nodes = Array.from(document.querySelectorAll('header,[role="banner"],[class*="header" i],[id*="header" i],[class*="nav" i],[id*="nav" i]'));
    } catch {
      nodes = Array.from(document.querySelectorAll('header,[role="banner"]'));
    }
    return nodes
      .filter(visible)
      .map((el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return {
          tag: el.tagName.toLowerCase(),
          id: el.id || '',
          className: stableClassName(el).slice(0, 220),
          width: Math.round(r.width),
          height: Math.round(r.height),
          position: cs.position,
          transform: cs.transform,
          opacity: cs.opacity,
          backgroundColor: cs.backgroundColor,
          boxShadow: cs.boxShadow,
          color: cs.color,
        };
      })
      .slice(0, 10);
  };
  const rootSignature = () => ({
    htmlClass: stableClassName(document.documentElement).slice(0, 300),
    bodyClass: stableClassName(document.body).slice(0, 300),
    firstBodyClass: stableClassName(document.body && document.body.firstElementChild).slice(0, 300),
  });
  const captureScrollState = async (y) => {
    window.scrollTo(0, y);
    window.dispatchEvent(new Event('scroll'));
    await sleep(260);
    return {
      y: Math.round(window.scrollY),
      root: rootSignature(),
      headers: headerCandidates(),
    };
  };
  const maxScroll = Math.max(0, scrollHeight - viewportHeight);
  const scrollTarget = maxScroll < 80 ? 0 : Math.round(Math.min(600, Math.max(120, maxScroll * 0.45)));
  const atTop = await captureScrollState(0);
  const atScroll = await captureScrollState(scrollTarget);
  await captureScrollState(0);
  const signature = (state) => JSON.stringify({ root: state.root, headers: state.headers });
  const mutates = scrollTarget > 0 && signature(atTop) !== signature(atScroll);

  return {
    url: location.href,
    viewport: { width: viewportWidth, height: viewportHeight },
    origins: { page: pageOrigin, reference: refOrigin },
    headAssets,
    suspectHeadAssets,
    layout: {
      scrollWidth,
      scrollHeight,
      viewportWidth,
      viewportHeight,
      overflowPx,
      overflowElements,
    },
    scrollTransition: {
      scrollTarget,
      mutates,
      atTop,
      atScroll,
    },
  };
})()
JS
  then
    local err
    err="$(cat "$out_file.stderr" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
    write_probe_error "$out_file" "$label" "$url" "$width" "$height" "agent-browser eval failed: ${err}"
    return 0
  fi

  # agent-browser returns JSON; normalize string-wrapped results defensively.
  "$PYTHON_BIN" - "$out_file" "$label" "$url" "$width" "$height" "$open_warning" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
label, url, width, height, open_warning = sys.argv[2:7]
try:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, str):
        obj = json.loads(obj)
    if not isinstance(obj, dict):
        raise ValueError("probe result is not an object")
    obj["label"] = label
    obj.setdefault("url", url)
    obj.setdefault("viewport", {"width": int(width), "height": int(height)})
    if open_warning:
        obj["openWarning"] = open_warning
except Exception as exc:  # noqa: BLE001 - diagnostic normalizer for shell boundary
    obj = {
        "label": label,
        "url": url,
        "viewport": {"width": int(width), "height": int(height)},
        "probeError": f"invalid agent-browser JSON: {exc}",
    }
path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
PY
}

PROBE_FILES=()
IFS=',' read -r -a VIEWPORT_ARRAY <<< "$VIEWPORTS"
for vp in "${VIEWPORT_ARRAY[@]}"; do
  width="${vp%x*}"
  height="${vp#*x}"
  if ! [[ "$width" =~ ^[0-9]+$ && "$height" =~ ^[0-9]+$ ]]; then
    echo "preview-runtime-health-check: invalid viewport '$vp' (expected WIDTHxHEIGHT)" >&2
    exit 2
  fi
  ref_file="$TMP_DIR/ref-${width}x${height}.json"
  impl_file="$TMP_DIR/impl-${width}x${height}.json"
  probe_url "ref" "$REF_URL" "$width" "$height" "$ref_file"
  probe_url "impl" "$IMPL_URL" "$width" "$height" "$impl_file"
  PROBE_FILES+=("$width" "$height" "$ref_file" "$impl_file")
done

"$PYTHON_BIN" - "$OUT_PATH" "$OVERFLOW_TOLERANCE_PX" "${PROBE_FILES[@]}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
tolerance = int(sys.argv[2])
args = sys.argv[3:]
failures: list[dict[str, object]] = []
warnings: list[dict[str, object]] = []
viewports: list[dict[str, object]] = []

def load(path: str) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return data
        raise ValueError("not an object")
    except Exception as exc:  # noqa: BLE001 - artifact diagnostic
        return {"probeError": f"cannot read probe: {exc}"}

for i in range(0, len(args), 4):
    width = int(args[i])
    height = int(args[i + 1])
    ref = load(args[i + 2])
    impl = load(args[i + 3])
    label = f"{width}x{height}"

    checks = {
        "headAssetOnReferenceOrigin": "pass",
        "horizontalOverflow": "pass",
        "scrollTransitionParity": "pass",
    }

    for side, payload in (("ref", ref), ("impl", impl)):
        if payload.get("probeError"):
            checks[f"{side}Probe"] = "fail"
            failures.append(
                {
                    "viewport": label,
                    "check": f"{side}-probe",
                    "message": str(payload.get("probeError")),
                }
            )

    suspect_assets = impl.get("suspectHeadAssets") or []
    if isinstance(suspect_assets, list) and suspect_assets:
        checks["headAssetOnReferenceOrigin"] = "fail"
        failures.append(
            {
                "viewport": label,
                "check": "headAssetOnReferenceOrigin",
                "message": "impl <head> build assets resolve to the reference origin instead of the preview origin",
                "assets": suspect_assets[:10],
            }
        )

    ref_layout = ref.get("layout") if isinstance(ref.get("layout"), dict) else {}
    impl_layout = impl.get("layout") if isinstance(impl.get("layout"), dict) else {}
    ref_overflow = int(ref_layout.get("overflowPx") or 0)
    impl_overflow = int(impl_layout.get("overflowPx") or 0)
    if impl_overflow > tolerance and (ref_overflow <= tolerance or impl_overflow - ref_overflow > tolerance):
        checks["horizontalOverflow"] = "fail"
        failures.append(
            {
                "viewport": label,
                "check": "horizontalOverflow",
                "message": f"impl overflows viewport by {impl_overflow}px (ref overflow {ref_overflow}px)",
                "overflowElements": impl_layout.get("overflowElements") or [],
            }
        )

    ref_scroll = ref.get("scrollTransition") if isinstance(ref.get("scrollTransition"), dict) else {}
    impl_scroll = impl.get("scrollTransition") if isinstance(impl.get("scrollTransition"), dict) else {}
    ref_mutates = bool(ref_scroll.get("mutates"))
    impl_mutates = bool(impl_scroll.get("mutates"))
    if ref_mutates and not impl_mutates:
        checks["scrollTransitionParity"] = "fail"
        failures.append(
            {
                "viewport": label,
                "check": "scrollTransitionParity",
                "message": "reference changes root/header state on scroll, but impl does not",
                "refScrollTarget": ref_scroll.get("scrollTarget"),
                "implScrollTarget": impl_scroll.get("scrollTarget"),
            }
        )
    elif not ref_mutates:
        warnings.append(
            {
                "viewport": label,
                "check": "scrollTransitionParity",
                "message": "reference showed no root/header scroll mutation at this viewport",
            }
        )

    viewports.append(
        {
            "width": width,
            "height": height,
            "checks": checks,
            "ref": ref,
            "impl": impl,
        }
    )

status = "fail" if failures else "pass"
payload = {
    "status": status,
    "summary": {
        "viewports": len(viewports),
        "failures": len(failures),
        "warnings": len(warnings),
        "overflowTolerancePx": tolerance,
    },
    "failures": failures,
    "warnings": warnings,
    "viewports": viewports,
}
out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
print(f"preview-runtime-health: {status} failures={len(failures)} warnings={len(warnings)}")
sys.exit(1 if failures else 0)
PY
