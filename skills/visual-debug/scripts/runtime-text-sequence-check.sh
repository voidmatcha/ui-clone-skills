#!/usr/bin/env bash
# runtime-text-sequence-check.sh — compare rendered text in document order.
#
# Usage:
#   runtime-text-sequence-check.sh <session> <ref-url> <impl-url> <ref-dir>
#
# Output: <ref-dir>/runtime-text-sequence.json
#
# Exit: 0 pass, 1 parity failure, 2 setup error.

set -uo pipefail

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: runtime-text-sequence-check.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "agent-browser not found in PATH" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/runtime-text-sequence.json"
REF_SESSION="${SESSION}-text-ref"
IMPL_SESSION="${SESSION}-text-impl"
ACTIVE_CAPTURE_SESSION=""
CAPTURE_CLOSE_ATTEMPTS=0

REF_TMP="$(mktemp -t runtime-text-ref-XXXXXX)"
mv "$REF_TMP" "${REF_TMP}.json"
REF_TMP="${REF_TMP}.json"
IMPL_TMP="$(mktemp -t runtime-text-impl-XXXXXX)"
mv "$IMPL_TMP" "${IMPL_TMP}.json"
IMPL_TMP="${IMPL_TMP}.json"

cleanup() {
  if [ -n "$ACTIVE_CAPTURE_SESSION" ]; then
    agent-browser --session "$ACTIVE_CAPTURE_SESSION" close >/dev/null 2>&1 || true
  fi
  rm -f "$REF_TMP" "$IMPL_TMP"
}
trap cleanup EXIT

capture_session_active() {  # <session>; 0=active, 1=absent, 2=list failed
  local session_name="$1"
  local listing=""
  if ! listing=$(agent-browser session list 2>/dev/null); then
    return 2
  fi
  printf '%s\n' "$listing" | awk -v target="$session_name" '
    /^  / {
      sub(/^  +/, "")
      if ($0 == target) found = 1
    }
    END { exit(found ? 0 : 1) }
  '
}

close_capture_session() {  # <session>
  local session_name="$1"
  local active_status=0
  CAPTURE_CLOSE_ATTEMPTS=0
  while [ "$CAPTURE_CLOSE_ATTEMPTS" -lt 3 ]; do
    CAPTURE_CLOSE_ATTEMPTS=$((CAPTURE_CLOSE_ATTEMPTS + 1))
    if agent-browser --session "$session_name" close >/dev/null 2>&1; then
      return 0
    fi
    capture_session_active "$session_name"
    active_status=$?
    if [ "$active_status" -eq 1 ]; then
      # The close response failed, but the authoritative registry confirms
      # the uniquely-owned session is already gone. Do not close it again:
      # re-closing an absent name can create a ghost registration.
      return 0
    fi
    [ "$CAPTURE_CLOSE_ATTEMPTS" -lt 3 ] && sleep 0.2
  done
  return 1
}

make_capture_session() {  # <parent-session> <attempt>
  local parent_session="$1"
  local attempt="$2"
  local candidate="${parent_session}-capture-$$-${attempt}"
  local candidate_bytes=""
  local side="x"
  local digest=""
  candidate_bytes="$(printf '%s' "$candidate" | wc -c | tr -d '[:space:]')"
  if [ "$candidate_bytes" -le 64 ]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  case "$parent_session" in
    *-text-ref) side="ref" ;;
    *-text-impl) side="impl" ;;
  esac
  digest="$(printf '%s' "${parent_session}:$$" | shasum -a 256 | awk '{ print substr($1, 1, 12) }')"
  printf 'rtseq-%s-%s-%s\n' "$side" "$digest" "$attempt"
}

# shellcheck disable=SC2016  # JavaScript template literals are intentional.
ANALYSIS_JS='(() => {
  const root = document.body || document.documentElement;
  const skippedTags = new Set([
    "SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "META", "LINK",
  ]);
  const semanticTags = new Set([
    "H1", "H2", "H3", "H4", "H5", "H6", "P", "LI", "A", "BUTTON",
    "LABEL", "TD", "TH", "DT", "DD", "FIGCAPTION", "BLOCKQUOTE",
    "CAPTION", "LEGEND", "SUMMARY", "OPTION",
  ]);
  const blockDisplays = new Set([
    "block", "flex", "grid", "list-item", "table", "table-row",
    "table-cell", "flow-root",
  ]);
  const normalizeText = (value) => String(value || "")
    .normalize("NFC")
    .replace(/[\u200B\u2060]/g, "")
    .replace(/\u00A0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const colorAlpha = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized || normalized === "transparent") return 0;
    const slashAlpha = normalized.match(
      /\/\s*([0-9]*\.?[0-9]+)(%)?\s*\)$/,
    );
    if (slashAlpha) {
      const alpha = parseFloat(slashAlpha[1]);
      return slashAlpha[2] ? alpha / 100 : alpha;
    }
    const legacy = normalized.match(
      /^rgba\([^,]+,[^,]+,[^,]+,\s*([0-9]*\.?[0-9]+)\s*\)$/,
    );
    if (legacy) return parseFloat(legacy[1]);
    if (/^#[0-9a-f]{8}$/i.test(normalized)) {
      return parseInt(normalized.slice(7, 9), 16) / 255;
    }
    return 1;
  };
  const hasVisibleTextPaint = (element) => {
    const style = getComputedStyle(element);
    const textFill = (
      style.webkitTextFillColor ||
      style.getPropertyValue("-webkit-text-fill-color") ||
      ""
    ).trim();
    const backgroundClip = (
      style.backgroundClip ||
      style.webkitBackgroundClip ||
      style.getPropertyValue("-webkit-background-clip") ||
      ""
    ).toLowerCase();
    const hasTextBackground = (
      backgroundClip.split(",").some((value) => value.trim() === "text") &&
      (
        (style.backgroundImage || "").toLowerCase() !== "none" ||
        colorAlpha(style.backgroundColor) > 0.01
      )
    );
    if (hasTextBackground) return true;
    if (textFill) return colorAlpha(textFill) > 0.01;
    return colorAlpha(style.color) > 0.01;
  };
  const overlapsHorizontally = (a, b) => (
    a.right > b.left && a.left < b.right
  );
  const overlapsVertically = (a, b) => (
    a.bottom > b.top && a.top < b.bottom
  );
  const clipsDescendants = (style) => {
    const clips = (value) => value === "hidden" || value === "clip";
    return {
      horizontal: clips(style.overflowX) || clips(style.overflow),
      vertical: clips(style.overflowY) || clips(style.overflow),
    };
  };
  const isOpaque = (element) => {
    const style = getComputedStyle(element);
    if (parseFloat(style.opacity || "1") < 0.5) return false;
    if (["IMG", "VIDEO", "CANVAS", "IFRAME", "SVG"].includes(element.tagName)) {
      return true;
    }
    const color = style.backgroundColor || "";
    const match = color.match(/rgba?\([^)]*(?:,\s*([0-9.]+))?\)/);
    const alpha = match && match[1] !== undefined ? parseFloat(match[1]) : (
      color && color !== "transparent" ? 1 : 0
    );
    return alpha >= 0.5 || (style.backgroundImage || "").includes("url(");
  };
  const isOccluded = (element, rects) => {
    let measured = 0;
    let blocked = 0;
    rects.forEach((rect) => {
      const points = [
        [rect.left + rect.width * 0.2, rect.top + rect.height / 2],
        [rect.left + rect.width * 0.5, rect.top + rect.height / 2],
        [rect.left + rect.width * 0.8, rect.top + rect.height / 2],
      ];
      points.forEach(([x, y]) => {
        if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return;
        measured += 1;
        const stack = document.elementsFromPoint(x, y);
        for (const candidate of stack) {
          if (
            candidate === element ||
            element.contains(candidate) ||
            candidate.contains(element)
          ) return;
          if (isOpaque(candidate)) {
            blocked += 1;
            return;
          }
        }
      });
    });
    return measured > 0 && blocked / measured >= 0.5;
  };
  const isHidden = (element) => {
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      if (
        current.hidden ||
        current.hasAttribute("inert") ||
        current.getAttribute("aria-hidden") === "true"
      ) return true;
      const style = getComputedStyle(current);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.visibility === "collapse" ||
        style.contentVisibility === "hidden" ||
        parseFloat(style.opacity || "1") <= 0.01 ||
        style.clip === "rect(0px, 0px, 0px, 0px)" ||
        /inset\(\s*50%\s*\)/.test(style.clipPath || "")
      ) return true;
      const rect = current.getBoundingClientRect();
      if (
        rect.width <= 1 &&
        rect.height <= 1 &&
        (style.overflow === "hidden" || style.position === "absolute")
      ) return true;
      current = current.parentElement;
    }
    return false;
  };
  const state = window.__uiCloneRuntimeTextState || {
    samples: [],
    initialBlocks: new WeakSet(),
    phaseSampleStartIndex: null,
  };
  window.__uiCloneRuntimeTextState = state;
  const isRenderedText = (node) => {
    const parent = node.parentElement;
    if (
      !parent ||
      parent.closest("[data-pseudo]") ||
      skippedTags.has(parent.tagName) ||
      isHidden(parent) ||
      !hasVisibleTextPaint(parent)
    ) return false;
    if (!(node.nodeValue || "")) return false;
    try {
      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = [...range.getClientRects()].filter(
        (rect) => rect.width > 0 && rect.height > 0,
      );
      range.detach();
      if (!rects.length) return false;
      const margin = 16;
      const horizontallyOffCanvas = rects.every(
        (rect) => rect.right <= -margin || rect.left >= innerWidth + margin,
      );
      const aboveCanvas = rects.every((rect) => rect.bottom <= -margin);
      const belowDocument = rects.every(
        (rect) => rect.top + scrollY >= document.documentElement.scrollHeight + innerHeight,
      );
      if (horizontallyOffCanvas || aboveCanvas || belowDocument) return false;

      let ancestor = parent.parentElement;
      while (ancestor && ancestor !== document.documentElement) {
        const style = getComputedStyle(ancestor);
        const clippedAxes = clipsDescendants(style);
        if (clippedAxes.horizontal || clippedAxes.vertical) {
          const clipRect = ancestor.getBoundingClientRect();
          const intersectsClip = rects.some((rect) => (
            (!clippedAxes.horizontal || overlapsHorizontally(rect, clipRect)) &&
            (!clippedAxes.vertical || overlapsVertically(rect, clipRect))
          ));
          if (!intersectsClip) return false;
        }
        ancestor = ancestor.parentElement;
      }
      return !isOccluded(parent, rects);
    } catch (error) {
      throw new Error(`runtime text measurement failed: ${error}`);
    }
  };
  const blockFor = (element) => {
    let current = element;
    while (current && current !== root) {
      if (semanticTags.has(current.tagName)) return current;
      current = current.parentElement;
    }
    current = element;
    while (current && current !== root) {
      if (blockDisplays.has(getComputedStyle(current).display)) return current;
      current = current.parentElement;
    }
    return root;
  };

  const elementPath = (element) => {
    const parts = [];
    let current = element;
    while (current && current !== root) {
      let ordinal = 1;
      let sibling = current.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === current.tagName) ordinal += 1;
        sibling = sibling.previousElementSibling;
      }
      parts.push(`${current.tagName.toLowerCase()}:nth-of-type(${ordinal})`);
      current = current.parentElement;
    }
    return parts.reverse().join(">");
  };
  const grouped = [];
  let activeBlock = null;
  let activeAnchor = null;
  let activeParts = [];
  let runOrdinals = new Map();
  const flush = () => {
    if (activeBlock && activeAnchor && activeParts.length) {
      const run = (runOrdinals.get(activeBlock) || 0) + 1;
      runOrdinals.set(activeBlock, run);
      grouped.push({
        anchor: activeAnchor,
        block: activeBlock,
        parts: activeParts,
        slot: `${elementPath(activeBlock)}::run(${run})`,
      });
    }
    activeBlock = null;
    activeAnchor = null;
    activeParts = [];
  };
  const collect = () => {
    grouped.length = 0;
    activeBlock = null;
    activeAnchor = null;
    activeParts = [];
    runOrdinals = new Map();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!isRenderedText(node)) continue;
      const block = blockFor(node.parentElement);
      if (activeBlock !== null && block !== activeBlock) flush();
      activeBlock = block;
      if (activeAnchor === null) activeAnchor = node;
      activeParts.push(node.nodeValue || "");
    }
    flush();
    const entries = [];
    grouped.forEach(({ anchor, block, parts, slot }) => {
      const text = normalizeText(parts.join(""));
      if (!text) return;
      const rect = block.getBoundingClientRect();
      if (
        state.samples.length === 0 &&
        rect.bottom > 0 &&
        rect.top < innerHeight &&
        rect.right > 0 &&
        rect.left < innerWidth
      ) {
        state.initialBlocks.add(block);
      }
      entries.push({
        slot,
        text,
        tag: block.tagName,
        initialViewport: state.initialBlocks.has(block),
        anchor,
      });
    });
    entries
      .filter(({ anchor }) => anchor.isConnected)
      .sort(
        (left, right) => {
          if (left.anchor === right.anchor) return 0;
          const position = left.anchor.compareDocumentPosition(right.anchor);
          if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
          if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
          return 0;
        },
      );
    const records = entries.map(({ anchor, ...entry }) => entry);
    state.samples.push({ records });
    return JSON.stringify({
      blocks: records.map(({ text }) => text),
      records,
      samples: state.samples.map(({ records: sample }) => sample),
      phaseSampleStartIndex: state.phaseSampleStartIndex,
      blockCount: records.length,
      actualUrl: location.href,
      pageReceipt: (() => {
        const navigation = performance.getEntriesByType("navigation")[0];
        const responseStatus = Number(navigation?.responseStatus || 0);
        const errorDocument = (
          !["http:", "https:"].includes(location.protocol) ||
          Boolean(document.querySelector(
            "#main-frame-error, .interstitial-wrapper, #sub-frame-error",
          ))
        );
        return {
          actualUrl: location.href,
          origin: location.origin,
          readyState: document.readyState,
          navigationType: String(navigation?.type || ""),
          responseStatus: Number.isInteger(responseStatus) ? responseStatus : 0,
          errorDocument,
        };
      })(),
    });
  };
  window.__uiCloneCollectRuntimeText = collect;
  return collect();
})()'

# shellcheck disable=SC2016  # JavaScript backticks are prose, not shell expansion.
SCROLL_SWEEP_JS='(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));
  const maxSteps = 48;
  const maxMs = 12000;
  const stableNeeded = 3;
  const started = performance.now();
  const stepPx = Math.max(320, Math.floor(innerHeight * 0.7));
  let mutations = 0;
  let stable = 0;
  let previous = "";
  let steps = 0;
  const observer = new MutationObserver((records) => { mutations += records.length; });
  observer.observe(document.documentElement, {
    attributes: true,
    childList: true,
    characterData: true,
    subtree: true,
  });
  try {
    window.__uiCloneCollectRuntimeText?.();
    while (steps < maxSteps && performance.now() - started < maxMs) {
      const height = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
      );
      const maxY = Math.max(0, height - innerHeight);
      const nextY = Math.min(maxY, scrollY + stepPx);
      window.scrollTo(0, nextY);
      await nextFrame();
      await sleep(180);
      await nextFrame();
      window.__uiCloneCollectRuntimeText?.();
      const currentHeight = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
      );
      const signature = [
        currentHeight,
        (document.body.innerText || "").length,
        document.getElementsByTagName("*").length,
      ].join(":");
      const atEnd = scrollY >= Math.max(0, currentHeight - innerHeight) - 2;
      if (atEnd && mutations === 0 && signature === previous) stable += 1;
      else stable = 0;
      previous = signature;
      mutations = 0;
      steps += 1;
      if (atEnd && stable >= stableNeeded) break;
    }
  } finally {
    observer.disconnect();
  }
  window.__uiCloneCollectRuntimeText?.();
  // Phase comparison must start from a settled top-of-page state. Captured
  // pages commonly set `scroll-behavior: smooth`; a single 250 ms wait then
  // records a mid-reset viewport as a fake text phase (observed on GitHub
  // Docs: exact final copy, but 102 vs 70 transient blocks). Force an instant
  // reset and require two consecutive stable text catalogs before marking the
  // phase window. These pre-window samples remain available as diagnostics.
  const rootStyle = document.documentElement.style;
  const bodyStyle = document.body?.style;
  const priorRootScrollBehavior = rootStyle.getPropertyValue("scroll-behavior");
  const priorRootScrollPriority = rootStyle.getPropertyPriority("scroll-behavior");
  const priorBodyScrollBehavior = bodyStyle?.getPropertyValue("scroll-behavior") || "";
  const priorBodyScrollPriority = bodyStyle?.getPropertyPriority("scroll-behavior") || "";
  rootStyle.setProperty("scroll-behavior", "auto", "important");
  bodyStyle?.setProperty("scroll-behavior", "auto", "important");
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  let topStable = 0;
  let topSignature = "";
  for (let topStep = 0; topStep < 16; topStep += 1) {
    await nextFrame();
    await sleep(150);
    if (Math.abs(scrollY) > 1) {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
    const rawTopSample = window.__uiCloneCollectRuntimeText?.() || "{}";
    let currentTopSignature = "";
    try {
      currentTopSignature = JSON.stringify(
        JSON.parse(rawTopSample).blocks || [],
      );
    } catch (_error) {
      currentTopSignature = "";
    }
    if (
      Math.abs(scrollY) <= 1 &&
      currentTopSignature &&
      currentTopSignature === topSignature
    ) {
      topStable += 1;
    } else {
      topStable = 0;
    }
    topSignature = currentTopSignature;
    if (topStable >= 2) break;
  }
  if (priorRootScrollBehavior) {
    rootStyle.setProperty(
      "scroll-behavior",
      priorRootScrollBehavior,
      priorRootScrollPriority,
    );
  } else {
    rootStyle.removeProperty("scroll-behavior");
  }
  if (bodyStyle) {
    if (priorBodyScrollBehavior) {
      bodyStyle.setProperty(
        "scroll-behavior",
        priorBodyScrollBehavior,
        priorBodyScrollPriority,
      );
    } else {
      bodyStyle.removeProperty("scroll-behavior");
    }
  }
  const phaseSamples = 12;
  window.__uiCloneRuntimeTextState.phaseSampleStartIndex =
    window.__uiCloneRuntimeTextState.samples.length;
  for (let phaseStep = 0; phaseStep < phaseSamples; phaseStep += 1) {
    window.__uiCloneCollectRuntimeText?.();
    await sleep(500);
    await nextFrame();
  }
  window.__uiCloneCollectRuntimeText?.();
  return JSON.stringify({
    steps,
    phaseSamples,
    quiescent: stable >= stableNeeded,
    finalHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
  });
})()'

run_capture() {
  local url="$1"
  local out="$2"
  local session="$3"
  local batch_commands=""
  local batch_result=""
  local batch_status=0
  local validation_status=0
  local attempt=0
  local max_attempts=3
  local attempt_session=""

  batch_commands="$(mktemp -t runtime-text-batch-commands-XXXXXX)"
  batch_result="$(mktemp -t runtime-text-batch-result-XXXXXX)"
  while [ "$attempt" -lt "$max_attempts" ]; do
    attempt=$((attempt + 1))
    batch_status=0
    # Never reuse a session name: a separate close process can race daemon
    # shutdown against the next open and reset that fresh batch to about:blank.
    attempt_session="$(make_capture_session "$session" "$attempt")"
    ACTIVE_CAPTURE_SESSION="$attempt_session"
    python3 - "$url" "$SCROLL_SWEEP_JS" "$ANALYSIS_JS" > "$batch_commands" <<'PY'
import json
import sys

print(json.dumps([
    ["set", "media", "light", "reduced-motion"],
    ["open", sys.argv[1]],
    ["wait", "1800"],
    ["eval", sys.argv[3]],
    ["eval", sys.argv[2]],
    ["eval", sys.argv[3]],
]))
PY
    agent-browser --session "$attempt_session" batch --json --bail \
      < "$batch_commands" > "$batch_result" 2>/dev/null || batch_status=$?

    validation_status=0
    python3 - "$batch_result" "$out" "$batch_status" "$attempt" "$max_attempts" "$url" <<'PY' || validation_status=$?
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

result_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
batch_status = int(sys.argv[3])
attempt = int(sys.argv[4])
max_attempts = int(sys.argv[5])
requested_url = sys.argv[6]


def fail(
    detail: str,
    *,
    kind: str = "batch-failed",
    retryable: bool = False,
    command_result_count: int | None = None,
) -> None:
    evidence = {
        "error": "agent-browser batch failed",
        "kind": kind,
        "detail": detail,
        "batchStatus": batch_status,
        "attempt": attempt,
        "attempts": attempt,
        "maxAttempts": max_attempts,
    }
    if command_result_count is not None:
        evidence["commandResultCount"] = command_result_count
    out_path.write_text(
        json.dumps(evidence) + "\n",
        encoding="utf-8",
    )
    raise SystemExit(75 if retryable else 1)


def canonical_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))


def same_url(left: Any, right: Any) -> bool:
    canonical_left = canonical_http_url(left)
    return (
        canonical_left is not None
        and canonical_left == canonical_http_url(right)
    )


requested_canonical = canonical_http_url(requested_url)
if requested_canonical is None:
    fail(f"requested URL is not valid HTTP(S): {requested_url!r}")


try:
    raw_payload: Any = json.loads(result_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    fail(
        f"unparseable batch JSON: {exc}",
        kind="malformed-batch",
        retryable=True,
    )

payload = raw_payload
if isinstance(raw_payload, dict):
    payload = raw_payload.get("results")
    if not isinstance(payload, list):
        error = raw_payload.get("error")
        if raw_payload.get("success") is False and isinstance(error, str):
            fail(
                f"agent-browser batch error: {error}",
                kind="batch-failed",
                retryable=True,
            )
        fail(
            "batch result object did not contain a results list",
            kind="malformed-batch",
            retryable=True,
        )
if not isinstance(payload, list):
    fail(
        f"batch result must be a list, got {type(payload).__name__}",
        kind="malformed-batch",
        retryable=True,
    )


def decode_analysis_value(item: Any):
    if not isinstance(item, dict) or item.get("success") is not True:
        return None, None
    result = item.get("result")
    analysis_origin = result.get("origin") if isinstance(result, dict) else None
    analysis_value = result.get("result") if isinstance(result, dict) else None
    value: Any = analysis_value
    for _ in range(3):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("blocks"), list)
        or not isinstance(analysis_origin, str)
    ):
        return None, None
    return value, {"origin": analysis_origin}


def has_final_capture_proof(item: Any) -> bool:
    value, _analysis = decode_analysis_value(item)
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("phaseSampleStartIndex"), int)
        and isinstance(value.get("pageReceipt"), dict)
        and bool(value.get("blocks"))
    )


if len(payload) > 6:
    fail(
        f"batch result contained {len(payload)}/6 command results",
        kind="oversized-batch",
        retryable=True,
        command_result_count=len(payload),
    )
compact_final_capture = (
    len(payload) == 5 and any(has_final_capture_proof(item) for item in payload)
)
if len(payload) < 6 and not compact_final_capture:
    fail(
        f"batch result contained {len(payload)}/6 command results",
        kind="incomplete-batch",
        retryable=True,
        command_result_count=len(payload),
    )
for index, item in enumerate(payload):
    if not isinstance(item, dict) or item.get("success") is not True:
        error = item.get("error") if isinstance(item, dict) else repr(item)
        fail(f"command {index} unsuccessful: {error}")

open_result = payload[1].get("result")
loaded_url = open_result.get("url") if isinstance(open_result, dict) else None
if canonical_http_url(loaded_url) is None:
    fail(f"open command did not report a fresh HTTP(S) URL: {loaded_url!r}")
if not same_url(requested_url, loaded_url):
    fail(
        "open command loaded a different URL/route "
        f"(requested={requested_url!r}, actual={loaded_url!r})",
        kind="url-mismatch",
    )

analysis_item = payload[5] if len(payload) == 6 else next(
    item for item in payload if has_final_capture_proof(item)
)
value, analysis = decode_analysis_value(analysis_item)
analysis_origin = analysis.get("origin") if isinstance(analysis, dict) else None
if not isinstance(analysis_origin, str) or not analysis_origin.lower().startswith(
    ("http://", "https://")
):
    fail(f"final analysis command did not run on HTTP(S): {analysis_origin!r}")
if not isinstance(value, dict) or not isinstance(value.get("blocks"), list):
    fail("final analysis command did not return a blocks payload")
if not value["blocks"]:
    fail("final analysis command returned zero text blocks", kind="empty-capture")
actual_url = value.get("actualUrl")
page_receipt = value.get("pageReceipt")
if not same_url(requested_url, actual_url):
    fail(
        "final analysis ran on a different URL/route "
        f"(requested={requested_url!r}, actual={actual_url!r})",
        kind="url-mismatch",
    )
if not isinstance(page_receipt, dict):
    fail("final analysis did not return a page capture receipt")
if not same_url(actual_url, page_receipt.get("actualUrl")):
    fail("page capture receipt actualUrl does not match final analysis URL")
canonical_actual = canonical_http_url(actual_url)
assert canonical_actual is not None
actual_parts = urlsplit(canonical_actual)
actual_origin = f"{actual_parts.scheme}://{actual_parts.netloc}"
analysis_url = canonical_http_url(analysis_origin)
analysis_context_matches = (
    analysis_origin == actual_origin
    or analysis_url == canonical_actual
)
if page_receipt.get("origin") != actual_origin or not analysis_context_matches:
    fail(
        "analysis context does not match the requested URL "
        f"(receipt={page_receipt.get('origin')!r}, batch={analysis_origin!r}, "
        f"expected={actual_origin!r})"
    )
response_status = page_receipt.get("responseStatus")
if (
    type(response_status) is not int
    or response_status < 200
    or response_status >= 400
):
    fail(
        f"page capture did not prove a successful HTTP response: {response_status!r}",
        kind="http-error",
    )
if page_receipt.get("readyState") != "complete":
    fail(
        "page capture did not reach readyState=complete: "
        f"{page_receipt.get('readyState')!r}"
    )
if page_receipt.get("errorDocument") is not False:
    fail("page capture resolved to a browser error document", kind="http-error")
value["captureReceipt"] = {
    "requestedUrl": requested_canonical,
    "openUrl": canonical_http_url(loaded_url),
    "actualUrl": canonical_actual,
    "analysisUrl": canonical_actual,
    "analysisOrigin": actual_origin,
    "responseStatus": response_status,
    "readyState": page_receipt.get("readyState"),
    "navigationType": page_receipt.get("navigationType"),
    "errorDocument": False,
    "batchCommandCount": len(payload),
    "attempt": attempt,
    "maxAttempts": max_attempts,
    "retryCount": attempt - 1,
    "closed": False,
}
value.pop("pageReceipt", None)

out_path.write_text(
    json.dumps(value, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY
    if close_capture_session "$attempt_session"; then
      ACTIVE_CAPTURE_SESSION=""
    else
      python3 - "$out" "$batch_status" "$attempt" "$max_attempts" "$CAPTURE_CLOSE_ATTEMPTS" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps({
        "error": "agent-browser session close failed",
        "kind": "batch-close-failed",
        "detail": "capture session could not be closed after batch completion",
        "batchStatus": int(sys.argv[2]),
        "attempt": int(sys.argv[3]),
        "attempts": int(sys.argv[3]),
        "maxAttempts": int(sys.argv[4]),
        "closeAttempts": int(sys.argv[5]),
    }) + "\n",
    encoding="utf-8",
)
PY
      break
    fi
    if [ "$validation_status" -eq 0 ]; then
      python3 - "$out" "$CAPTURE_CLOSE_ATTEMPTS" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
capture = json.loads(path.read_text(encoding="utf-8"))
capture["captureReceipt"]["closed"] = True
capture["captureReceipt"]["closeAttempts"] = int(sys.argv[2])
path.write_text(
    json.dumps(capture, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY
      rm -f "$batch_commands" "$batch_result"
      return 0
    fi
    if [ "$validation_status" -eq 75 ] && [ "$attempt" -lt "$max_attempts" ]; then
      python3 - "$out" "$attempt" "$max_attempts" <<'PY' >&2
import json
import sys
from pathlib import Path

evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    "runtime-text-sequence: "
    f"{evidence['detail']} on attempt {sys.argv[2]}/{sys.argv[3]}; "
    "retrying with a fresh session"
)
PY
      continue
    fi
    break
  done
  rm -f "$batch_commands" "$batch_result"
  return 1
}

run_capture "$REF_URL" "$REF_TMP" "$REF_SESSION" || true
if [ -n "$ACTIVE_CAPTURE_SESSION" ]; then
  # A failed close leaves the exact ref session registered for the EXIT trap
  # to retry. Starting the impl capture here would overwrite that name and
  # turn a recoverable close failure into a leaked browser process.
  python3 - "$IMPL_TMP" "$ACTIVE_CAPTURE_SESSION" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps({
        "error": "implementation capture skipped",
        "kind": "prior-session-close-failed",
        "detail": (
            "reference capture session could not be closed; "
            "implementation capture was not started"
        ),
        "pendingSession": sys.argv[2],
    }) + "\n",
    encoding="utf-8",
)
PY
else
  run_capture "$IMPL_URL" "$IMPL_TMP" "$IMPL_SESSION" || true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/runtime_text_sequence_compare.py" \
  "$REF_TMP" "$IMPL_TMP" "$OUT_PATH" "$REF_URL" "$IMPL_URL"
