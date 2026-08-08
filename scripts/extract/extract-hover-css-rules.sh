#!/usr/bin/env bash
# extract-hover-css-rules.sh — bounded Step 5d-2b hover CSS extraction.
#
# Usage:
#   bash scripts/extract/extract-hover-css-rules.sh <session> <ref-dir> [url]
#
# Produces:
#   <ref-dir>/hover-css-rules.json
#
# The live CSSOM path catches inline <style> hover rules. If the browser
# command is unavailable, fails, or exceeds UI_CLONE_HOVER_CSS_TIMEOUT, the
# script falls back to a deterministic scan of downloaded <ref-dir>/css/*.css.

set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <session> <ref-dir> [url]" >&2
  exit 1
fi

SESSION="$1"
REF_DIR="$2"
SOURCE_URL="${3:-}"
TIMEOUT_SECONDS="${UI_CLONE_HOVER_CSS_TIMEOUT:-20}"

mkdir -p "$REF_DIR"

python3 - "$SESSION" "$REF_DIR" "$SOURCE_URL" "$TIMEOUT_SECONDS" <<'PY'
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SESSION = sys.argv[1]
REF_DIR = Path(sys.argv[2])
SOURCE_URL = sys.argv[3]
TIMEOUT_SECONDS = float(sys.argv[4])
OUT_PATH = REF_DIR / "hover-css-rules.json"

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

LIVE_JS = r"""
(() => {
  const out = [];
  const pushRule = (rule, context) => {
    const selectorText = String(rule.selectorText || "");
    if (!selectorText.includes(":hover")) return;
    if (selectorText.toLowerCase().includes("autofill")) return;
    const props = {};
    if (rule.style) {
      for (let i = 0; i < rule.style.length; i++) {
        const name = rule.style[i];
        props[name] = rule.style.getPropertyValue(name);
      }
    }
    out.push({
      selector: selectorText.slice(0, 500),
      css: String(rule.cssText || "").replace(/\s+/g, " ").slice(0, 1000),
      declarations: Object.entries(props).map(([k, v]) => `${k}: ${v}`).join("; "),
      cssProperties: props,
      source: "live-cssom",
      context
    });
  };
  const visit = (rules, context = []) => {
    if (!rules) return;
    for (const rule of rules) {
      try {
        if (rule.cssRules) {
          const label = rule.conditionText || rule.name || rule.type || "";
          visit(rule.cssRules, label ? context.concat(String(label)) : context);
          continue;
        }
        pushRule(rule, context);
      } catch (_) {}
    }
  };
  for (const sheet of document.styleSheets) {
    try { visit(sheet.cssRules, []); } catch (_) {}
  }
  return {
    schemaVersion: 1,
    source: "scripts/extract/extract-hover-css-rules.sh:live-cssom",
    url: location.href,
    rules: out,
    count: out.length
  };
})()
"""


def _json_loads_maybe(raw: str) -> Any:
    value: Any = json.loads(raw)
    for _ in range(3):
        if isinstance(value, dict) and isinstance(value.get("data"), dict) and "result" in value["data"]:
            value = value["data"]["result"]
        elif isinstance(value, dict) and "result" in value:
            value = value["result"]
        else:
            break
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                value = json.loads(stripped)
    return value


def _run_agent_browser(args: list[str], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    if shutil.which("agent-browser") is None:
        return {"status": "skipped", "reason": "agent-browser not found"}
    try:
        proc = subprocess.run(
            ["agent-browser", "--session", SESSION, *args],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "timeoutSeconds": timeout_seconds,
            "durationMs": round((time.monotonic() - started) * 1000),
        }
    result: dict[str, Any] = {
        "status": "pass" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "durationMs": round((time.monotonic() - started) * 1000),
    }
    if proc.returncode != 0:
        result["stderr"] = proc.stderr[-500:]
        result["stdout"] = proc.stdout[-500:]
    else:
        result["stdout"] = proc.stdout
    return result


def _split_selector_group(selector_group: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(selector_group):
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(selector_group[start:index].strip())
            start = index + 1
    parts.append(selector_group[start:].strip())
    return [part for part in parts if part]


def _iter_blocks(css_text: str) -> list[tuple[str, str]]:
    css_text = COMMENT_RE.sub("", css_text)
    blocks: list[tuple[str, str]] = []

    def walk(chunk: str) -> None:
      index = 0
      cursor = 0
      length = len(chunk)
      while index < length:
          brace = chunk.find("{", index)
          if brace == -1:
              break
          selector = chunk[cursor:brace].strip()
          depth = 1
          close = brace + 1
          while close < length and depth:
              if chunk[close] == "{":
                  depth += 1
              elif chunk[close] == "}":
                  depth -= 1
              close += 1
          if depth:
              break
          body = chunk[brace + 1 : close - 1]
          if selector.startswith("@"):
              walk(body)
          else:
              blocks.append((selector, body))
          index = close
          cursor = close

    walk(css_text)
    return blocks


def _scan_downloaded_css() -> tuple[list[dict[str, Any]], list[str]]:
    css_dir = REF_DIR / "css"
    files = sorted(path for path in css_dir.glob("*.css") if path.is_file()) if css_dir.is_dir() else []
    rules: list[dict[str, Any]] = []
    for path in files:
        try:
            css_text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for selector_group, body in _iter_blocks(css_text):
            if ":hover" not in selector_group:
                continue
            for selector in _split_selector_group(selector_group):
                if ":hover" not in selector or "autofill" in selector.lower():
                    continue
                declarations = " ".join(body.split())
                rules.append({
                    "selector": selector[:500],
                    "css": f"{selector} {{{declarations}}}"[:1000],
                    "declarations": declarations[:1000],
                    "source": "downloaded-css-fallback",
                    "file": str(path.relative_to(REF_DIR)),
                })
    return rules, [str(path.relative_to(REF_DIR)) for path in files]


def _live_rules() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: dict[str, Any] = {}
    if SOURCE_URL and os.environ.get("UI_CLONE_HOVER_CSS_OPEN", "1") != "0":
        open_result = _run_agent_browser(["open", SOURCE_URL], min(TIMEOUT_SECONDS, 10))
        diagnostics["open"] = {k: v for k, v in open_result.items() if k != "stdout"}
        time.sleep(1.5)  # open --wait is not a supported flag; settle explicitly
    eval_result = _run_agent_browser(["eval", "--json", LIVE_JS], TIMEOUT_SECONDS)
    diagnostics["eval"] = {k: v for k, v in eval_result.items() if k != "stdout"}
    if eval_result.get("status") != "pass":
        return [], diagnostics
    try:
        parsed = _json_loads_maybe(str(eval_result.get("stdout") or ""))
    except (json.JSONDecodeError, TypeError) as exc:
        diagnostics["eval"]["status"] = "invalid-json"
        diagnostics["eval"]["reason"] = str(exc)
        return [], diagnostics
    if not isinstance(parsed, dict):
        diagnostics["eval"]["status"] = "unexpected-shape"
        return [], diagnostics
    raw_rules = parsed.get("rules")
    if not isinstance(raw_rules, list):
        diagnostics["eval"]["status"] = "missing-rules"
        return [], diagnostics
    rules = [rule for rule in raw_rules if isinstance(rule, dict)]
    diagnostics["eval"]["ruleCount"] = len(rules)
    return rules, diagnostics


def _dedupe(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for rule in rules:
        selector = str(rule.get("selector") or "").strip()
        if not selector:
            continue
        css = str(rule.get("css") or rule.get("declarations") or "").strip()
        key = (selector, css)
        if key in seen:
            continue
        seen.add(key)
        out.append(rule)
    return out


def main() -> int:
    live, live_diagnostics = _live_rules()
    fallback, css_sources = _scan_downloaded_css()
    rules = _dedupe([*live, *fallback])
    has_evidence = bool(
        live_diagnostics.get("eval", {}).get("status") == "pass" or css_sources
    )
    payload = {
        "schemaVersion": 1,
        "source": "scripts/extract/extract-hover-css-rules.sh",
        "status": "pass" if has_evidence else "fail",
        "observation": "hover-css-rules" if rules else "no-hover-css-rules-observed",
        "count": len(rules),
        "rules": rules,
        "derivedFrom": ["live-cssom"] if live else [],
        "fallbackDerivedFrom": css_sources,
        "diagnostics": {
            "liveCssom": live_diagnostics,
            "downloadedCssRuleCount": len(fallback),
        },
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "artifact": str(OUT_PATH),
        "count": len(rules),
        "liveRules": len(live),
        "fallbackRules": len(fallback),
    }, indent=2))
    return 0 if has_evidence else 4


if __name__ == "__main__":
    raise SystemExit(main())
PY
