#!/usr/bin/env bash
# runtime-image-validity-check.sh — fail when impl images load with
# HTTP 200 but render as broken / HTML fallback.
#
#
#
# Validation method (browser-side, not network-side):
#   - Open impl URL in agent-browser
#   - Scroll mid+bottom to settle lazy-loaded images
#   - For every <img> in document.images:
#       fail if (img.complete && img.naturalWidth === 0)
#       fail if currentSrc resolves to a content-type starting with text/html
#   - Both signals catch Vite/Next dev-server fallback that delivers HTML
#     to image requests as 200.
#
# Usage:
#   runtime-image-validity-check.sh <session> <impl-url> [w] [h]
#   REF_DIR=<dir>  — required when invoked by verification-plan.sh
#                    (writes <REF_DIR>/runtime-image-validity.json)
#
# Exit: 0 = pass, 1 = at least one broken image, 2 = setup error.

set -uo pipefail

SESSION="${1:-}"
URL="${2:-}"
VIEW_W="${3:-1280}"
VIEW_H="${4:-800}"
WAIT_MS="${WAIT_MS:-1500}"

if [ -z "$SESSION" ] || [ -z "$URL" ]; then
  echo "Usage: runtime-image-validity-check.sh <session> <impl-url> [w] [h]" >&2
  echo "  Optional env: REF_DIR=<dir>  (writes runtime-image-validity.json)" >&2
  echo "                WAIT_MS=<ms>   (default 1500 — settle delay)" >&2
  exit 2
fi

REF_DIR="${REF_DIR:-}"
OUT_PATH=""
if [ -n "$REF_DIR" ]; then
  mkdir -p "$REF_DIR" 2>/dev/null || true
  OUT_PATH="$REF_DIR/runtime-image-validity.json"
fi

TMP_OUT=$(mktemp)
cleanup() {
  agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
  rm -f "$TMP_OUT"
}
trap cleanup EXIT INT TERM

agent-browser --session "$SESSION" open "$URL" >/dev/null 2>&1 || {
  echo "runtime-image-validity: failed to open $URL" >&2
  exit 2
}
agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" wait "$WAIT_MS" >/dev/null 2>&1 || true

# Scroll mid + bottom to settle lazy-loaded images.
agent-browser --session "$SESSION" eval "(() => { window.scrollTo(0, document.body.scrollHeight / 2); return window.scrollY; })()" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" wait 800 >/dev/null 2>&1 || true
agent-browser --session "$SESSION" eval "(() => { window.scrollTo(0, document.body.scrollHeight); return window.scrollY; })()" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" wait 800 >/dev/null 2>&1 || true
# Return to top for consistent state.
agent-browser --session "$SESSION" eval "(() => { window.scrollTo(0, 0); return window.scrollY; })()" >/dev/null 2>&1 || true
agent-browser --session "$SESSION" wait 400 >/dev/null 2>&1 || true

# IIFE collects document.images. Uses fetch to verify content-type so
# Vite/Next HTML fallbacks (200 text/html) are caught. Heredoc avoids
# quote-conflict issues — all string literals inside use double quotes.
EVAL_JS=$(cat <<'JSEOF'
(async () => {
  const images = await Promise.all([...document.images].map(async (img) => {
    const item = {
      src: img.getAttribute("src") || img.src || "",
      currentSrc: img.currentSrc || img.src || "",
      naturalWidth: img.naturalWidth,
      naturalHeight: img.naturalHeight,
      complete: img.complete,
    };
    const reasons = [];
    if (item.complete === true && item.naturalWidth === 0) {
      reasons.push("complete-zero-naturalWidth");
    }
    if (
      item.currentSrc &&
      !item.currentSrc.startsWith("data:") &&
      !item.currentSrc.startsWith("blob:")
    ) {
      try {
        const res = await fetch(item.currentSrc, { cache: "no-store" });
        const ct = (res.headers.get("content-type") || "").toLowerCase();
        if (ct.startsWith("text/html")) reasons.push("html-response");
      } catch (_) {
        // Network-level failure is reported elsewhere by hydration-check.
      }
    }
    return { ...item, reason: reasons.join("; ") };
  }));
  const failures = images.filter((i) => i.reason).map((i) => ({
    src: i.src || i.currentSrc,
    naturalWidth: i.naturalWidth,
    reason: i.reason,
  }));
  return JSON.stringify({
    status: failures.length ? "fail" : "pass",
    total: images.length,
    broken: failures.length,
    failures: failures.slice(0, 50),
    generatedAt: new Date().toISOString(),
  });
})()
JSEOF
)

agent-browser --session "$SESSION" eval "$EVAL_JS" > "$TMP_OUT" 2>/dev/null || {
  echo "runtime-image-validity: eval failed" >&2
  exit 2
}

python3 - "$TMP_OUT" "${OUT_PATH:-/dev/stdout}" <<'PY'
import json
import sys
from pathlib import Path

raw = open(sys.argv[1]).read().strip()
# agent-browser returns the eval result as either bare JSON or a JSON-
# wrapped string. Handle both.
try:
    data = json.loads(raw)
    if isinstance(data, str):
        data = json.loads(data)
except json.JSONDecodeError:
    print(json.dumps({
        "status": "error",
        "reason": "eval returned non-JSON",
        "raw": raw[:200],
    }, indent=2))
    sys.exit(2)

dest = sys.argv[2]
if dest == "/dev/stdout":
    print(json.dumps(data, indent=2))
else:
    Path(dest).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"runtime-image-validity: {data.get('broken', 0)} broken / {data.get('total', 0)} images → {dest}")

sys.exit(0 if data.get("status") == "pass" else 1)
PY
