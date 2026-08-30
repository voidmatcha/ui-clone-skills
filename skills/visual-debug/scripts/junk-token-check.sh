#!/usr/bin/env bash
# junk-token-check.sh — stringified-junk lint over impl source + runtime DOM.
#
# Loop-9/10 regression class: generated state code shipped
# `classList.toggle('undefined', cond)` so live nav elements carried a
# literal "undefined" class, and no gate flagged it. Serialization junk —
# 'undefined' / 'null' / 'NaN' / '[object Object]' — appearing as a
# STANDALONE token in className, id, src, href, alt, or style values is
# always a generation defect (a JS value leaked into a string), never a
# design choice.
#
# Two scan layers:
#   static  — impl source markup/JS attribute strings (className/class/id/alt/
#             src/href/style PLUS data-*/aria-* and SVG presentation attrs like
#             fill=, batch-7 ITEM 5), classList.add/toggle/remove string
#             literals, and a value-walk of .json files. .astro joins the markup
#             family. Token membership strips default-ignorable Cf code points
#             (ZWJ/ZWSP) then NFKC + confusable-folds.
#   runtime — a DOM eval over the live impl across a scroll+settle sweep
#             (className/id/src/href/alt/style/data-*/aria-*/SVG presentation),
#             catching template-string junk that only materializes at runtime.
#             SCOPE (honest gap): interaction-gated junk (a className mutated
#             only on click/hover/route-change) and computed CSS-var/::before
#             content are NOT in the unit guarantee — they are a best-effort
#             browser-only sweep; the SAME junk in SOURCE is a static block.
#             The artifact records runtimeScanned either way so a static-only
#             run is never mistaken for full coverage.
#
# Usage: junk-token-check.sh <ref-dir> <impl-src> [session] [impl-url]
#
# Writes:
#   <ref-dir>/junk-token.json
#
# Exit:
#   0 pass, 1 fail, 2 setup error

set -euo pipefail

REF_DIR="${1:?Usage: junk-token-check.sh <ref-dir> <impl-src> [session] [impl-url]}"
IMPL_SRC="${2:?Usage: junk-token-check.sh <ref-dir> <impl-src> [session] [impl-url]}"
SESSION="${3:-}"
IMPL_URL="${4:-}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_SRC" ] || { echo "impl-src not found: $IMPL_SRC" >&2; exit 2; }

OUT="$REF_DIR/junk-token.json"
RUNTIME_RAW="$(mktemp "${TMPDIR:-/tmp}/junk-token-runtime.XXXXXX")"
trap 'rm -f "$RUNTIME_RAW"' EXIT

PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUN_WITH_TIMEOUT="$REPO_ROOT/scripts/lib/run_with_timeout.py"
AGENT_BROWSER_TIMEOUT_SEC="${JUNK_TOKEN_AGENT_BROWSER_TIMEOUT_SEC:-15}"

run_with_timeout() {
  "$PYTHON_BIN" "$RUN_WITH_TIMEOUT" "${AGENT_BROWSER_TIMEOUT_SEC}s" "$@"
}

# ── runtime DOM scan (optional) ──
RUNTIME_SCANNED=0
RUNTIME_ATTEMPTED=0
if [ -n "$SESSION" ] && [ -n "$IMPL_URL" ]; then
  RUNTIME_ATTEMPTED=1
fi
if [ -n "$SESSION" ] && [ -n "$IMPL_URL" ] && command -v agent-browser >/dev/null 2>&1; then
  JS='(async () => {
    const JUNK = new Set(["undefined", "null", "NaN"]);
    // Unicode-confusable fold (batch-6 ITEM 5b): NFKC-normalize then map common
    // Latin-lookalikes so "undefinеd" (Cyrillic e) is caught. Double-quoted keys
    // only (single quotes would break the shell wrapper); literal homoglyphs.
    const CONF = { "а": "a", "α": "a", "е": "e", "Е": "E", "Ε": "E",
      "о": "o", "ο": "o", "с": "c", "р": "p", "х": "x", "у": "y",
      "і": "i", "ι": "i", "ӏ": "l", "ԁ": "d", "ѕ": "s", "Ν": "N", "А": "A", "Α": "A" };
    // Strip default-ignorable Cf code points (ZWJ/ZWSP/ZWNJ/WJ/BOM) before NFKC
    // (batch-7 ITEM 5) — charCodeAt avoids backslash escapes (one-unescape pass).
    const STRIP_CP = [0x200b, 0x200c, 0x200d, 0x2060, 0xfeff];
    const fold = (s) => {
      if (!s) return s;
      const kept = Array.from(s).filter(c => STRIP_CP.indexOf(c.charCodeAt(0)) < 0).join("");
      const n = kept.normalize ? kept.normalize("NFKC") : kept;
      return Array.from(n).map(c => CONF[c] || c).join("");
    };
    const isJunk = (t) => JUNK.has(t) || JUNK.has(fold(t));
    // Widened attribute surface (batch-7 ITEM 5): SVG presentation attrs that
    // carry serialization junk (fill="undefined" renders default black).
    const PRESENTATION = ["fill", "stroke", "stop-color", "flood-color", "lighting-color"];
    const findings = [];
    const seen = new Set();
    const push = (f) => {
      const k = f.tag + "|" + f.attr + "|" + f.value;
      if (seen.has(k)) return;
      seen.add(k);
      findings.push(f);
    };
    const segJunk = (value) => {
      if (!value) return false;
      if (value.includes("[object Object]")) return true;
      return value.split("/").some(seg => {
        const base = seg.split("?")[0].split("#")[0];
        const stem = base.includes(".") ? base.substring(0, base.indexOf(".")) : base;
        return isJunk(stem);
      });
    };
    const scan = () => {
      Array.from(document.querySelectorAll("*")).forEach(el => {
        const tag = el.tagName.toLowerCase();
        if (tag === "script" || tag === "style") return;
        const cls = (el.className && el.className.toString ? el.className.toString() : "");
        cls.split(" ").forEach(tok => {
          const t = tok.trim();
          if (isJunk(t)) push({ tag, attr: "className", value: cls.substring(0, 80) });
        });
        if (cls.includes("[object Object]")) push({ tag, attr: "className", value: cls.substring(0, 80) });
        if (el.id && isJunk(el.id)) push({ tag, attr: "id", value: el.id });
        ["src", "href"].forEach(a => {
          const v = el.getAttribute(a);
          if (v && segJunk(v)) push({ tag, attr: a, value: v.substring(0, 120) });
        });
        const alt = el.getAttribute("alt");
        if (alt && (isJunk(alt.trim()) || alt.includes("[object Object]"))) {
          push({ tag, attr: "alt", value: alt.substring(0, 80) });
        }
        const styleAttr = el.getAttribute("style") || "";
        if (styleAttr.includes("[object Object]")
            || /(^|[^A-Za-z0-9_-])(undefined|NaN)/.test(styleAttr)) {
          push({ tag, attr: "style", value: styleAttr.substring(0, 120) });
        }
        // data-*/aria-* + SVG presentation attribute values (batch-7 ITEM 5).
        try {
          el.getAttributeNames().forEach(a => {
            const isDataAria = a.indexOf("data-") === 0 || a.indexOf("aria-") === 0;
            if (!isDataAria && PRESENTATION.indexOf(a) < 0) return;
            const v = el.getAttribute(a) || "";
            if (v.split(" ").some(t => isJunk(t.trim())) || v.includes("[object Object]")) {
              push({ tag, attr: a, value: v.substring(0, 120) });
            }
          });
        } catch (e) {}
        // Computed CSS content / paint junk (batch-8 ITEM 6): ::before/::after
        // content and computed fill/stroke can carry serialization junk that
        // never appears as an attribute. Strip quote chars by code point (34/39)
        // to avoid a quote literal in the single-quoted shell wrapper.
        try {
          const stripQ = (s) => Array.from(s).filter(c => c.charCodeAt(0) !== 34 && c.charCodeAt(0) !== 39).join("");
          ["::before", "::after"].forEach(pe => {
            const c = getComputedStyle(el, pe).content || "";
            if (isJunk(stripQ(c).trim()) || c.includes("[object Object]")) {
              push({ tag, attr: pe + " content", value: c.substring(0, 80) });
            }
          });
          const cs2 = getComputedStyle(el);
          PRESENTATION.forEach(p => {
            const v = (cs2.getPropertyValue(p) || "").trim();
            if (isJunk(v)) push({ tag, attr: p, value: v.substring(0, 80) });
          });
        } catch (e) {}
      });
    };
    const wait = ms => new Promise(r => setTimeout(r, ms));
    scan();
    // Scroll + settle sweep (batch-6 ITEM 5a): the fixed ~2s window misses junk
    // applied later via setTimeout(>2s) or on scroll/timer (the specific regression nav-dot
    // class). Re-scan across a scroll sweep and after a longer settle so
    // late-materializing junk is captured.
    const maxScroll = () => Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
    const steps = 6;
    for (let i = 1; i <= steps; i++) {
      window.scrollTo({ top: (i / steps) * maxScroll(), behavior: "instant" });
      await wait(500);
      scan();
    }
    window.scrollTo({ top: 0, behavior: "instant" });
    await wait(2500);
    scan();
    // Interaction sweep (batch-8 ITEM 6): junk gated behind click/hover/route-
    // change never materializes under a scroll-only sweep. Fire a representative
    // gesture on non-navigating interactive elements + a hashchange, then re-scan
    // (best-effort; the SOURCE scan is the authoritative block). Anchors and
    // nav links are excluded to avoid navigating away mid-scan.
    try {
      const interactive = Array.from(document.querySelectorAll("button,[role=button],[onclick],[tabindex]")).slice(0, 40);
      interactive.forEach(el => {
        try { el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); } catch (e) {}
        try { el.click(); } catch (e) {}
      });
      await wait(300);
      location.hash = "#__junk_sweep__";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
      await wait(300);
      scan();
    } catch (e) {}
    return findings.slice(0, 100);
  })()'
  if run_with_timeout agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1 \
     && run_with_timeout agent-browser --session "$SESSION" wait 2000 >/dev/null 2>&1 \
     && run_with_timeout agent-browser --session "$SESSION" eval "$JS" > "$RUNTIME_RAW" 2>/dev/null; then
    RUNTIME_SCANNED=1
  fi
fi

python3 - "$IMPL_SRC" "$OUT" "$RUNTIME_RAW" "$RUNTIME_SCANNED" "$RUNTIME_ATTEMPTED" <<'PY'
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

impl_src = Path(sys.argv[1])
out_path = Path(sys.argv[2])
runtime_raw = Path(sys.argv[3])
runtime_scanned = sys.argv[4] == "1"
runtime_attempted = sys.argv[5] == "1"

JUNK = {"undefined", "null", "NaN"}

# Unicode-confusable folding (batch-6 ITEM 5b): a Cyrillic/Greek homoglyph
# ('nav_dot undefinеd' with Cyrillic e U+0435) reads as junk to a human but the
# ASCII-exact JUNK set misses it. NFKC-normalize then fold the common
# Latin-lookalikes before membership testing.
_CONFUSABLE = {
    "а": "a", "α": "a", "е": "e", "Е": "E", "Ε": "E",
    "о": "o", "ο": "o", "с": "c", "р": "p", "х": "x",
    "у": "y", "і": "i", "ι": "i", "ӏ": "l", "ԁ": "d",
    "ѕ": "s", "Ν": "N", "А": "A", "Α": "A",
}


def _fold(token: str) -> str:
    # Strip default-ignorable Cf code points FIRST (batch-7 ITEM 5): NFKC does
    # not remove ZWJ/ZWSP/ZWNJ/WJ/BOM, so 'u<ZWJ>ndefined' survived the fold and
    # read as a non-junk token. Removing the whole Cf category is generic.
    stripped = "".join(ch for ch in token if unicodedata.category(ch) != "Cf")
    t = unicodedata.normalize("NFKC", stripped)
    return "".join(_CONFUSABLE.get(ch, ch) for ch in t)


def is_junk(token: str) -> bool:
    return token in JUNK or _fold(token) in JUNK


# .astro joins the markup family; .json gets a dedicated value-walk pass below
# (keys/structure are not regex-scanned as markup).
SOURCE_EXTS = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs", ".html", ".vue", ".svelte", ".astro"}

# batch-9 ITEM 4: scan ALL string-literal args of classList.add/remove/toggle/
# replace, not only the first — classList.add("ok", "undefined") shipped junk in
# a trailing arg past the old first-arg-only regex. Capture the whole arg list,
# then test each quoted literal with is_junk (which covers undefined/null/NaN +
# the confusable fold).
CLASSLIST_CALL_RE = re.compile(r"classList\.(?:toggle|add|remove|replace)\(([^)]*)\)")
STRING_LITERAL_RE = re.compile(r"""(['"])(.*?)\1""")
TOKEN_ATTR_RE = re.compile(r"\b(className|class|id|alt)\s*=\s*([\"'])(.*?)\2")
URL_ATTR_RE = re.compile(r"\b(src|href)\s*=\s*([\"'])(.*?)\2")
STYLE_ATTR_RE = re.compile(r"\bstyle\s*=\s*([\"'])(.*?)\1")
# Widened attribute surface (batch-7 ITEM 5): data-*/aria-* values and SVG
# presentation attributes (fill/stroke/...) carried junk no rule scanned. Both
# require a QUOTED LITERAL value so JSX `data-x={expr}` (legit JS) is excluded.
DATA_ATTR_RE = re.compile(r"\b((?:data|aria)-[A-Za-z][\w-]*)\s*=\s*([\"'])(.*?)\2")
PRESENTATION_ATTR_RE = re.compile(
    r"\b(fill|stroke|stop-color|flood-color|lighting-color)\s*=\s*([\"'])(.*?)\2"
)
# prefix boundary only: junk renders as "NaNpx" / "undefinedpx" with a unit
# glued on, so requiring a trailing boundary would miss the common form.
STYLE_JUNK_RE = re.compile(r"(^|[^A-Za-z0-9_-])(undefined|NaN)")

# CSS declaration value junk (batch-8 ITEM 6): `content: "undefined"`,
# `fill: undefined`, `--x: undefined`. .css/.scss/.sass/.less are scanned via a
# dedicated value-walk (NOT added to SOURCE_EXTS, which runs markup/JS regexes
# that assume attribute syntax). Only a value that FOLDS to a standalone junk
# token flags, so `var(--undefined-token)` / `#0a0` / prose are not flagged.
CSS_EXTS = {".css", ".scss", ".sass", ".less"}
CSS_DECL_RE = re.compile(r"([-\w]+)\s*:\s*([^;{}]+)")

# JS attribute/property sinks that bypass the markup-shape regexes (batch-8
# ITEM 6): setAttribute("class"/"id"/"data-x"/"fill"/..., "undefined"),
# el.dataset.foo = "undefined", el.style.cssText = "...: undefined". Each
# requires a QUOTED STRING LITERAL so a dynamic identifier value
# (setAttribute("class", clsName) / dataset.x = value) is excluded.
# Direct el.className/el.id = "..." is already caught by TOKEN_ATTR_RE.
SETATTR_RE = re.compile(r"\.setAttribute\(\s*(['\"])([\w-]+)\1\s*,\s*(['\"])(.*?)\3")
# batch-9 ITEM 4: support both dot (.dataset.foo) and bracket (.dataset["foo"] /
# ['foo']) assignment. The bracket KEY must be a string literal so a dynamic
# computed key (.dataset[varKey] = ...) — legit JS — stays excluded, mirroring
# the setAttribute(name, x) dynamic-identifier exclusion. The value capture
# groups (quote = group 1, value = group 2) are unchanged.
DATASET_ASSIGN_RE = re.compile(
    r"\.dataset(?:\.[\w$]+|\[\s*['\"][\w$-]+['\"]\s*\])\s*=\s*(['\"])(.*?)\1"
)
CSSTEXT_RE = re.compile(r"\.style\.cssText\s*=\s*(['\"])(.*?)\1")


def url_has_junk(value: str) -> bool:
    if "[object Object]" in value:
        return True
    for seg in value.split("/"):
        base = seg.split("?")[0].split("#")[0]
        stem = base.split(".")[0] if "." in base else base
        if is_junk(stem):
            return True
    return False


static_findings: list[dict[str, Any]] = []


def _json_value_junk(node: Any, rel: str, findings: list[dict[str, Any]]) -> None:
    """Walk JSON string VALUES (not keys) flagging standalone junk tokens —
    closes the .json scan-exclusion (batch-7 ITEM 5 / Attack 4). Standalone
    semantics (split + is_junk) avoid over-flagging human sentences."""
    if isinstance(node, str):
        # whole-value only — a prose value containing the word 'undefined' is
        # not junk; a value that IS 'undefined'/'null'/'NaN'/[object Object] is.
        if is_junk(node.strip()) or "[object Object]" in node:
            findings.append({"file": rel, "line": 0, "kind": "json-value-junk",
                             "value": node[:120]})
    elif isinstance(node, list):
        for v in node:
            _json_value_junk(v, rel, findings)
    elif isinstance(node, dict):
        for v in node.values():
            _json_value_junk(v, rel, findings)


# D24 (loop-nvti-1): a 2.6MB reference CSS with a single 1.7M-char minified
# line hung CSS_DECL_RE's per-line finditer (superlinear; process-group-killed
# at the row budget). Two guards: (1) ref-css/** holds REFERENCE-sourced
# vendor bytes copied into impl for @import mirroring — not generated code,
# out of scope for a generation-junk check; (2) any line longer than the cap
# is a minified asset, skipped from regex scanning and COUNTED (no silent
# cap — the artifact reports skips).
LONG_LINE_CAP = 20_000
skipped_long_lines = 0
skipped_ref_css_files = 0


def scan_lines(text: str):
    global skipped_long_lines
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > LONG_LINE_CAP:
            skipped_long_lines += 1
            continue
        yield lineno, line


for path in sorted(impl_src.rglob("*")):
    if not path.is_file():
        continue
    if "node_modules" in path.parts or "dist" in path.parts:
        continue
    if "ref-css" in path.parts:
        skipped_ref_css_files += 1
        continue
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        _json_value_junk(data, str(path.relative_to(impl_src)), static_findings)
        continue
    if suffix in CSS_EXTS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(impl_src))
        for lineno, line in scan_lines(text):
            for m in CSS_DECL_RE.finditer(line):
                prop, raw = m.group(1), m.group(2).strip()
                val = raw.strip("'\"").strip()
                if is_junk(val) or "[object Object]" in raw:
                    static_findings.append(
                        {"file": rel, "line": lineno, "kind": "css-decl-junk",
                         "value": (prop + ": " + raw)[:120]}
                    )
        continue
    if suffix not in SOURCE_EXTS:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    rel = str(path.relative_to(impl_src))
    for lineno, line in scan_lines(text):
        for m in CLASSLIST_CALL_RE.finditer(line):
            junk_args = [
                lit for _q, lit in STRING_LITERAL_RE.findall(m.group(1))
                if is_junk(lit.strip()) or "[object Object]" in lit
            ]
            if junk_args:
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": "classList-literal",
                     "value": ("classList: " + ", ".join(junk_args))[:100]}
                )
        for m in SETATTR_RE.finditer(line):
            name, value = m.group(2), m.group(4)
            if name in ("class", "className"):
                hit = any(is_junk(t) for t in value.split()) or "[object Object]" in value
            else:
                hit = is_junk(value.strip()) or "[object Object]" in value
            if hit:
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": "setAttribute-junk",
                     "value": (name + "=" + value)[:100]}
                )
        for m in DATASET_ASSIGN_RE.finditer(line):
            value = m.group(2)
            if is_junk(value.strip()) or "[object Object]" in value:
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": "dataset-junk",
                     "value": value[:100]}
                )
        for m in CSSTEXT_RE.finditer(line):
            value = m.group(2)
            if "[object Object]" in value or STYLE_JUNK_RE.search(value):
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": "cssText-junk",
                     "value": value[:120]}
                )
        for m in TOKEN_ATTR_RE.finditer(line):
            attr, value = m.group(1), m.group(3)
            tokens = value.split()
            if any(is_junk(t) for t in tokens) or "[object Object]" in value:
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": f"{attr}-token",
                     "value": value[:100]}
                )
        # data-*/aria-*/SVG presentation values are single values, not class
        # lists — flag only when the WHOLE value is junk (or [object Object]),
        # so a human-prose value containing the word 'undefined' is not flagged.
        for m in DATA_ATTR_RE.finditer(line):
            attr, value = m.group(1), m.group(3)
            if is_junk(value.strip()) or "[object Object]" in value:
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": f"{attr}-token",
                     "value": value[:100]}
                )
        for m in PRESENTATION_ATTR_RE.finditer(line):
            attr, value = m.group(1), m.group(3)
            if is_junk(value.strip()) or "[object Object]" in value:
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": f"{attr}-token",
                     "value": value[:100]}
                )
        for m in URL_ATTR_RE.finditer(line):
            attr, value = m.group(1), m.group(3)
            if url_has_junk(value):
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": f"{attr}-junk-segment",
                     "value": value[:120]}
                )
        for m in STYLE_ATTR_RE.finditer(line):
            value = m.group(2)
            if "[object Object]" in value or STYLE_JUNK_RE.search(value):
                static_findings.append(
                    {"file": rel, "line": lineno, "kind": "style-junk",
                     "value": value[:120]}
                )

runtime_findings: list[dict[str, Any]] = []
if runtime_scanned:
    try:
        data = json.loads(runtime_raw.read_text(encoding="utf-8"))
        if isinstance(data, str):  # agent-browser double-encodes eval output
            data = json.loads(data)
        if isinstance(data, list):
            runtime_findings = [f for f in data if isinstance(f, dict)]
    except (OSError, json.JSONDecodeError):
        runtime_scanned = False

if static_findings or runtime_findings:
    status = "fail"
elif runtime_attempted and not runtime_scanned:
    # Review-1 MAJOR 3: runtime args were supplied but the DOM scan failed —
    # static coverage alone must not read as a clean pass.
    status = "warn"
else:
    status = "pass"
payload: dict[str, Any] = {
    "schemaVersion": 1,
    "status": status,
    "staticFindings": static_findings[:100],
    "runtimeFindings": runtime_findings[:100],
    "runtimeScanned": runtime_scanned,
    "runtimeAttempted": runtime_attempted,
    "implSrcDir": str(impl_src.resolve()),
    "skippedLongLines": skipped_long_lines,
    "skippedRefCssFiles": skipped_ref_css_files,
    "rule": (
        "Serialization junk ('undefined'/'null'/'NaN'/'[object Object]') must "
        "never appear as a standalone token in className, id, src, href, alt, "
        "or style values — in impl source or in the live DOM. A JS value "
        "leaked into a string is a generation defect."
    ),
}
if status == "warn":
    payload["reason"] = (
        "runtime DOM scan attempted but failed — static-only coverage is "
        "incomplete (template-string junk only materializes live); re-run "
        "with a reachable impl URL"
    )
if status == "fail":
    first = (static_findings or runtime_findings)[0]
    payload["diagnostic"] = (
        f"{len(static_findings)} static + {len(runtime_findings)} runtime "
        f"junk token(s); first: {json.dumps(first)}"
    )

out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
sys.exit(1 if status == "fail" else 0)
PY
