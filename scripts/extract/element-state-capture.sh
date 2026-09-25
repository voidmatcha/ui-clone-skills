#!/usr/bin/env bash
# element-state-capture.sh — canonical frame capture for element-scope evidence
# (element-capture.md). Every frame it writes under tmp/ref/<target>/frames/<side>/
# gets a provenance entry in frames/<side>/capture-manifest.json (page URL of
# the agent-browser session, session name, timestamp, sha256), which
# `python -m ui_clone.scoped_check` re-verifies. Never hand-write the frames,
# the manifest, or <state>.computed.json.
#
# Usage:
#   element-state-capture.sh clip  <session> <expected-url> <selector> <ref-dir> <ref|impl> <state> [navigation-receipt]
#       Put the page into the state first (hover / scroll / open trigger), then
#       run this: it measures the element, records its computed styles
#       (<state>.computed.json), takes a full-viewport screenshot, and crops the
#       element's box to frames/<side>/<state>.png.
#   element-state-capture.sh video <session> <expected-url> <ref-dir> <ref|impl> <recording.webm> <prefix> [navigation-receipt]
#       Extracts a recording made in that session at 60fps to
#       frames/<side>/<prefix>-%04d.png (replacing earlier <prefix>-* frames)
#       and records the session's current page URL for them.
#
# The session must be on <expected-url>'s origin (validated with
# validate-agent-browser-origin.py, like element-evidence.sh). Both modes also
# inventory the resources the page has loaded; an impl capture is refused when
# the page loaded anything from the reference host (proxy/iframe/bundle reuse
# is not an implementation) or any script/stylesheet the reference captures
# inventoried (frames/ref/capture-manifest.json `codeResources`, classified by
# initiator kind, code extension, or the response content type resource
# timing reports, matched by normalized URL; any non-media load from a
# non-first-party origin that served reference code is refused too, so an
# extensionless chunk fetched from that origin is caught; media and font
# hotlinks stay allowed). Clip mode requires the selector to
# match exactly one visible element with a box of at least TARGET_MIN_SIZE px
# (ui_clone.hooks._common) and records the target's element subtree (up to
# SUBTREE_MAX_NODES, see ui_clone.computed_style_diff) with the same property
# list. Exit: 0 ok, 2 usage, 3 browser/origin failure, 1 recording, target,
# or provenance failure.
set -euo pipefail

MODE="${1:-}"
case "$MODE" in
  clip)
    if [[ $# -lt 7 || $# -gt 8 ]]; then
      echo "Usage: element-state-capture.sh clip <session> <expected-url> <selector> <ref-dir> <ref|impl> <state> [navigation-receipt]" >&2
      exit 2
    fi
    SESSION="$2"; EXPECTED_URL="$3"; SELECTOR="$4"; REF_DIR="$5"; SIDE="$6"; STATE="$7"
    NAVIGATION_RECEIPT="${8:-$REF_DIR/capture-navigation.json}"
    ;;
  video)
    if [[ $# -lt 7 || $# -gt 8 ]]; then
      echo "Usage: element-state-capture.sh video <session> <expected-url> <ref-dir> <ref|impl> <recording.webm> <prefix> [navigation-receipt]" >&2
      exit 2
    fi
    SESSION="$2"; EXPECTED_URL="$3"; REF_DIR="$4"; SIDE="$5"; RECORDING="$6"; PREFIX="$7"
    NAVIGATION_RECEIPT="${8:-$REF_DIR/capture-navigation.json}"
    ;;
  *)
    echo "Usage: element-state-capture.sh clip|video ..." >&2
    exit 2
    ;;
esac

if [[ "$SIDE" != "ref" && "$SIDE" != "impl" ]]; then
  echo "element-state-capture: side must be ref or impl, got '$SIDE'" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ORIGIN_VALIDATOR="$SCRIPT_DIR/validate-agent-browser-origin.py"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
# The recorder stamps this script's sha256 into every manifest entry as the
# driver record; scoped_check compares it with the shipped script.
export UI_CLONE_CAPTURE_DRIVER="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"

FRAMES_DIR="$REF_DIR/frames/$SIDE"
mkdir -p "$FRAMES_DIR"

RAW_FILE="$(mktemp)"
FULL_PNG="$(mktemp).png"
trap 'rm -f "$RAW_FILE" "$FULL_PNG"' EXIT

validate_origin() {
  if ! python3 "$ORIGIN_VALIDATOR" "$EXPECTED_URL" --session "$SESSION" \
    --navigation "$NAVIGATION_RECEIPT" < "$RAW_FILE"; then
    echo "element-state-capture: agent-browser session is not on the expected page origin (session=$SESSION, expected=$EXPECTED_URL)" >&2
    exit 3
  fi
}

# Loaded-resource inventory shared by both modes: Performance resource entries
# (scripts, stylesheets, fetch/XHR, images, fonts) plus the URLs of every
# script/link/iframe/img/media element in the DOM, so an iframe of the
# reference site or its bundles show up even when the timing buffer is full.
RESOURCES_JS=""
read -r -d '' RESOURCES_JS <<'JS' || true
  const collectResources = () => {
    const seen = new Map();
    const add = (url, kind, contentType) => {
      if (!url || typeof url !== "string" || !/^https?:/i.test(url)) return;
      if (!seen.has(url)) seen.set(url, { kind, contentType: typeof contentType === "string" ? contentType : "" });
    };
    let entries = [];
    try { entries = performance.getEntriesByType("resource"); } catch (e) { entries = []; }
    // entry.contentType (Chromium resource timing) classifies a bundle chunk
    // fetched from an extensionless URL as code without an extra request.
    for (const entry of entries) add(entry.name, entry.initiatorType || "other", entry.contentType);
    for (const el of document.querySelectorAll("script[src]")) add(el.src, "script");
    for (const el of document.querySelectorAll("link[href]")) add(el.href, (el.rel || "link").toLowerCase());
    for (const el of document.querySelectorAll("iframe[src],frame[src]")) add(el.src, "iframe");
    for (const el of document.querySelectorAll("img,video,audio,source,object,embed")) {
      add(el.currentSrc || el.src || el.data, el.tagName.toLowerCase());
    }
    const resources = [];
    for (const [url, meta] of seen) resources.push({ url, kind: meta.kind, contentType: meta.contentType });
    return { resources, resourceEntryCount: entries.length };
  };
JS

if [[ "$MODE" == "clip" ]]; then
  SELECTOR_JSON="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$SELECTOR")"
  PROPS_JSON="$(python3 -c 'import json; from ui_clone.computed_style_diff import COMPUTED_STYLE_PROPS as P; print(json.dumps(list(P)))')"
  MAX_NODES="$(python3 -c 'from ui_clone.computed_style_diff import SUBTREE_MAX_NODES as N; print(N)')"
  EVAL_JS=""
  read -r -d '' EVAL_JS <<JS || true
(() => {
  const selector = ${SELECTOR_JSON};
  const props = ${PROPS_JSON};
  const maxNodes = ${MAX_NODES};
${RESOURCES_JS}
  // Target sanity (shared with element-evidence.sh): exactly one match,
  // visible in the captured state; the box minimum is checked by the recorder.
  let matches;
  try {
    matches = document.querySelectorAll(selector);
  } catch (e) {
    return { ok: false, url: location.href, selector, matchCount: 0, error: "invalid selector: " + e.message };
  }
  const matchCount = matches.length;
  if (matchCount !== 1) {
    return {
      ok: false,
      url: location.href,
      selector,
      matchCount,
      error: matchCount === 0 ? "selector not found" : "selector matches " + matchCount + " elements, expected exactly 1"
    };
  }
  const element = matches[0];
  const visibilityOf = (node) => {
    const own = getComputedStyle(node);
    const record = { display: own.display, visibility: own.visibility, opacity: own.opacity, hiddenBy: null };
    let current = node;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const style = current === node ? own : getComputedStyle(current);
      const where = current === node ? "target" : "ancestor <" + current.tagName.toLowerCase() + ">";
      if (style.display === "none") { record.hiddenBy = where + " display:none"; break; }
      if (style.visibility === "hidden" || style.visibility === "collapse") { record.hiddenBy = where + " visibility:" + style.visibility; break; }
      if (parseFloat(style.opacity) === 0) { record.hiddenBy = where + " opacity:0"; break; }
      current = current.parentElement;
    }
    return record;
  };
  const visibility = visibilityOf(element);
  const styleRecord = (node) => {
    const computed = getComputedStyle(node);
    const computedStyle = {};
    for (const prop of props) {
      computedStyle[prop] = String(computed[prop] || "");
    }
    return computedStyle;
  };
  // Target subtree: element children only (text/comment nodes never count),
  // depth-first pre-order, path = tag[index-among-element-siblings]/...
  const subtree = [];
  let descendantCount = 0;
  const walk = (node, prefix) => {
    const children = Array.from(node.children);
    children.forEach((child, index) => {
      descendantCount += 1;
      const path = prefix + child.tagName.toLowerCase() + "[" + index + "]";
      if (subtree.length < maxNodes) {
        subtree.push({ path, tag: child.tagName.toLowerCase(), computedStyle: styleRecord(child) });
      }
      walk(child, path + "/");
    });
  };
  walk(element, "");
  const rect = element.getBoundingClientRect();
  return {
    ok: true,
    url: location.href,
    selector,
    matchCount,
    bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    visibility,
    visible: visibility.hiddenBy === null,
    viewport: { innerWidth: window.innerWidth, innerHeight: window.innerHeight, dpr: window.devicePixelRatio },
    computedStyle: styleRecord(element),
    subtree,
    descendantCount,
    ...collectResources()
  };
})()
JS
  if ! agent-browser --session "$SESSION" eval --json "$EVAL_JS" >"$RAW_FILE"; then
    echo "element-state-capture: agent-browser eval failed (session=$SESSION)" >&2
    exit 3
  fi
  validate_origin
  if ! agent-browser --session "$SESSION" screenshot "$FULL_PNG" >/dev/null || [[ ! -s "$FULL_PNG" ]]; then
    echo "element-state-capture: agent-browser screenshot failed (session=$SESSION)" >&2
    exit 3
  fi
  python3 -m ui_clone.element_capture record-clip "$REF_DIR" "$SIDE" "$STATE" \
    --full "$FULL_PNG" --session "$SESSION" < "$RAW_FILE"
  echo "$FRAMES_DIR/$STATE.png"
  exit 0
fi

# video mode
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "element-state-capture: ffmpeg is required for video frame extraction" >&2
  exit 2
fi
if [[ ! -s "$RECORDING" ]]; then
  echo "element-state-capture: recording missing or empty: $RECORDING" >&2
  exit 1
fi
if [[ ! "$PREFIX" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "element-state-capture: prefix must be a plain lowercase name (frame, open, close), got '$PREFIX'" >&2
  exit 2
fi
VIDEO_JS=""
read -r -d '' VIDEO_JS <<JS || true
(() => {
${RESOURCES_JS}
  return { ok: true, url: location.href, ...collectResources() };
})()
JS
if ! agent-browser --session "$SESSION" eval --json "$VIDEO_JS" >"$RAW_FILE"; then
  echo "element-state-capture: agent-browser eval failed (session=$SESSION)" >&2
  exit 3
fi
validate_origin
# Wipe earlier frames of this prefix so a partial re-extraction cannot mix runs.
find "$FRAMES_DIR" -maxdepth 1 -type f -name "$PREFIX-[0-9]*.png" -delete
if ! ffmpeg -loglevel error -y -i "$RECORDING" -vf fps=60 "$FRAMES_DIR/$PREFIX-%04d.png"; then
  echo "element-state-capture: ffmpeg frame extraction failed for $RECORDING" >&2
  exit 1
fi
python3 -m ui_clone.element_capture record-video "$REF_DIR" "$SIDE" "$PREFIX" \
  --source "$RECORDING" --session "$SESSION" < "$RAW_FILE"
echo "$FRAMES_DIR/$PREFIX-%04d.png"
