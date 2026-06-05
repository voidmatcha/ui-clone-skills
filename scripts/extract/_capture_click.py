#!/usr/bin/env python3
"""Agent-browser click-state capture with navigation guards."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

DISCOVERY_JS = r"""(() => {
  const CAP = 25;
  const selectors = [
    "button",
    "a[href]",
    "[role='button']",
    "summary",
    "[aria-expanded]",
    "[data-state]",
    "[onclick]",
    "input[type='checkbox']",
    "input[type='radio']",
    "[tabindex]:not([tabindex='-1'])"
  ];
  const seen = new Set();
  const out = [];
  const esc = (value) => {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  };
  const cssPath = (el) => {
    if (el.id) return "#" + esc(el.id);
    const attrKeys = ["data-testid", "data-test", "data-state", "aria-controls", "aria-label"];
    for (const key of attrKeys) {
      const value = el.getAttribute(key);
      if (value) return el.tagName.toLowerCase() + "[" + key + "='" + value.replace(/'/g, "\\'") + "']";
    }
    const cls = typeof el.className === "string"
      ? el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2)
      : [];
    let selector = el.tagName.toLowerCase() + cls.map(c => "." + esc(c)).join("");
    if (document.querySelectorAll(selector).length === 1) return selector;
    let nth = 1;
    let sib = el;
    while ((sib = sib.previousElementSibling)) {
      if (sib.tagName === el.tagName) nth++;
    }
    selector += ":nth-of-type(" + nth + ")";
    return selector;
  };
  for (const sel of selectors) {
    let nodes;
    try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of nodes) {
      if (seen.has(el)) continue;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (rect.width < 2 || rect.height < 2 || style.visibility === "hidden" || style.display === "none") {
        continue;
      }
      const selector = cssPath(el);
      const href = el.href || el.getAttribute("href") || "";
      const target = el.getAttribute("target") || "";
      const download = el.hasAttribute("download");
      const expanded = el.getAttribute("aria-expanded");
      const dataState = el.getAttribute("data-state");
      const triggerType = href ? "click-navigation" :
        (expanded !== null || dataState !== null ? "click-toggle" : "click-action");
      out.push({
        id: "",
        name: (el.getAttribute("aria-label") || el.textContent || selector).trim().slice(0, 80),
        selector,
        triggerType,
        href,
        target,
        download,
      });
      if (out.length >= CAP) break;
    }
    if (out.length >= CAP) break;
  }
  return { candidates: out, candidatesFound: out.length, candidatesCappedAt: CAP };
})()"""


SNAPSHOT_JS = r"""(() => {
  const cheapHash = (str) => {
    let h = 5381;
    for (let i = 0; i < str.length; i++) h = ((h << 5) + h) + str.charCodeAt(i);
    return h >>> 0;
  };
  const body = document.body || { className: "", outerHTML: "", innerText: "" };
  const html = document.documentElement;
  const visibleSections = [];
  const nodes = document.querySelectorAll("section,[data-section],main > *,dialog,[role='dialog'],[aria-expanded]");
  for (const el of nodes) {
    try {
      const r = el.getBoundingClientRect();
      if (r.bottom > 0 && r.top < window.innerHeight && r.width > 20 && r.height > 10) {
        const id = el.id ? "#" + el.id : "";
        const cls = typeof el.className === "string"
          ? "." + el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".")
          : "";
        visibleSections.push({
          selector: el.tagName.toLowerCase() + id + cls,
          top: Math.round(r.top),
          height: Math.round(r.height),
          ariaExpanded: el.getAttribute("aria-expanded"),
          dataState: el.getAttribute("data-state")
        });
        if (visibleSections.length >= 30) break;
      }
    } catch (e) {}
  }
  return {
    url: location.href,
    bodyClass: body.className || "",
    htmlClass: html.className || "",
    domHash: cheapHash(html.outerHTML || ""),
    domLength: (html.outerHTML || "").length,
    visibleTextHash: cheapHash((body.innerText || "").slice(0, 5000)),
    visibleSections,
  };
})()"""


def _run_agent(session: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["agent-browser", "--session", session, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"agent-browser {' '.join(args[:2])} failed for session={session}: {proc.stderr}"
        )
    return proc


def _peel_json(raw: str) -> Any:
    data = json.loads(raw)
    if isinstance(data, dict) and isinstance(data.get("data"), dict) and "result" in data["data"]:
        data = data["data"]["result"]
        if isinstance(data, str):
            data = json.loads(data)
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], dict | str):
        data = data["result"]
        if isinstance(data, str):
            data = json.loads(data)
    return data


def _eval_json(session: str, js: str) -> dict[str, Any]:
    proc = _run_agent(session, "eval", "--json", js)
    data = _peel_json(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError(f"agent-browser eval returned non-object payload: {type(data).__name__}")
    return data


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return (parsed.scheme, parsed.hostname or "", parsed.port)


def _without_fragment(url: str) -> str:
    return urldefrag(url).url


def _classify_navigation(ref_url: str, after_url: str) -> str:
    if not after_url:
        return "unknown"
    if _origin(ref_url) != _origin(after_url):
        return "external"
    if _without_fragment(ref_url) != _without_fragment(after_url):
        return "same-origin-navigation"
    if ref_url != after_url:
        return "hash-navigation"
    return "same-page"


def _skip_reason(candidate: dict[str, Any]) -> str | None:
    href = str(candidate.get("href") or "").strip()
    if bool(candidate.get("download")):
        return "download"
    target = str(candidate.get("target") or "").lower()
    if target == "_blank":
        return "new-tab"
    scheme = urlparse(href).scheme.lower()
    if scheme in {"mailto", "tel", "sms", "javascript", "data", "blob", "file"}:
        return f"non-http-scheme:{scheme or 'relative'}"
    return None


def _safe_id(candidate: dict[str, Any], index: int) -> str:
    raw = str(candidate.get("id") or "").strip()
    if raw:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in raw)
        return safe[:40] or f"click-{index}"
    selector = str(candidate.get("selector") or f"click-{index}")
    return hashlib.sha256(selector.encode("utf-8")).hexdigest()[:8]


def _dom_changed(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, list[str]]:
    signals: list[str] = []
    for key in ("domHash", "domLength", "visibleTextHash", "bodyClass", "htmlClass", "visibleSections"):
        if before.get(key) != after.get(key):
            signals.append(key)
    return bool(signals), signals


def _restore_if_needed(session: str, ref_url: str, after_url: str) -> bool:
    if not after_url or after_url == ref_url:
        return True
    _run_agent(session, "back", check=False)
    _run_agent(session, "wait", "500", check=False)
    current = _run_agent(session, "get", "url", check=False).stdout.strip()
    if _origin(ref_url) != _origin(current) or _without_fragment(ref_url) != _without_fragment(current):
        _run_agent(session, "open", ref_url, "--wait", "500", check=False)
        current = _run_agent(session, "get", "url", check=False).stdout.strip()
    return _origin(ref_url) == _origin(current) and _without_fragment(ref_url) == _without_fragment(current)


def _navigation_type_for_skip(reason: str) -> str:
    if reason == "download":
        return "download-navigation"
    if reason == "new-tab":
        return "new-tab-navigation"
    if reason.startswith("non-http-scheme:"):
        return "non-http-navigation"
    return "unknown"


def capture(url: str, base_session: str, ref_dir: Path) -> int:
    if shutil.which("agent-browser") is None:
        print("capture-click: agent-browser not found in PATH", file=sys.stderr)
        return 2
    outdir = ref_dir / "states" / "click"
    outdir.mkdir(parents=True, exist_ok=True)

    discovery_session = f"{base_session}-click-discovery"
    _run_agent(discovery_session, "open", url, "--wait", "1500")
    discovery = _eval_json(discovery_session, DISCOVERY_JS)
    candidates = discovery.get("candidates") or []
    if not isinstance(candidates, list):
        candidates = []
    _run_agent(discovery_session, "close", check=False)

    manifest_entries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[: int(discovery.get("candidatesCappedAt") or 25)]):
        if not isinstance(candidate, dict):
            continue
        selector = str(candidate.get("selector") or "")
        if not selector:
            continue
        candidate_id = _safe_id(candidate, index)
        skip_reason = _skip_reason(candidate)
        if skip_reason is not None:
            navigation_type = _navigation_type_for_skip(skip_reason)
            record = {
                "id": candidate_id,
                "name": candidate.get("name", ""),
                "selector": selector,
                "triggerType": candidate.get("triggerType") or "click-navigation",
                "href": candidate.get("href") or "",
                "target": candidate.get("target") or "",
                "eventDriver": "agent-browser.click.skipped",
                "navigationType": navigation_type,
                "navigationOnly": True,
                "declaredOnly": True,
                "clickSucceeded": False,
                "guard": {
                    "isolatedSession": True,
                    "session": None,
                    "urlBefore": url,
                    "urlAfter": None,
                    "restored": True,
                    "skippedReason": skip_reason,
                },
                "bodyClassBefore": "",
                "bodyClassAfter": "",
                "htmlClassBefore": "",
                "htmlClassAfter": "",
                "domMutation": {"changed": False, "signals": []},
                "schemaVersion": 1,
            }
            file_name = f"click-{candidate_id}.json"
            (outdir / file_name).write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            records.append(record)
            manifest_entries.append({
                "id": candidate_id,
                "file": file_name,
                "selector": selector,
                "triggerType": record["triggerType"],
                "navigationType": navigation_type,
                "navigationOnly": True,
                "changedCount": 0,
                "schemaVersion": 1,
            })
            continue
        session = f"{base_session}-click-{index}"
        _run_agent(session, "open", url, "--wait", "1500")
        before = _eval_json(session, SNAPSHOT_JS)
        click_proc = _run_agent(session, "click", selector, check=False)
        _run_agent(session, "wait", "500", check=False)
        after_url = _run_agent(session, "get", "url", check=False).stdout.strip() or before.get("url") or url
        navigation_type = _classify_navigation(url, after_url)
        try:
            after = _eval_json(session, SNAPSHOT_JS)
        except Exception:
            after = {"url": after_url}
        restored = _restore_if_needed(session, url, after_url)
        _run_agent(session, "close", check=False)

        navigation_only = navigation_type in {"external", "same-origin-navigation", "hash-navigation"}
        changed, signals = _dom_changed(before, after)
        if navigation_only:
            changed = False
            signals = []
        record = {
            "id": candidate_id,
            "name": candidate.get("name", ""),
            "selector": selector,
            "triggerType": candidate.get("triggerType") or "click-action",
            "href": candidate.get("href") or "",
            "target": candidate.get("target") or "",
            "eventDriver": "agent-browser.click",
            "navigationType": navigation_type,
            "navigationOnly": navigation_only,
            "clickSucceeded": click_proc.returncode == 0,
            "guard": {
                "isolatedSession": True,
                "session": session,
                "urlBefore": url,
                "urlAfter": after_url,
                "restored": restored,
            },
            "bodyClassBefore": before.get("bodyClass", ""),
            "bodyClassAfter": after.get("bodyClass", ""),
            "htmlClassBefore": before.get("htmlClass", ""),
            "htmlClassAfter": after.get("htmlClass", ""),
            "domMutation": {
                "changed": changed,
                "signals": signals,
                "domHashBefore": before.get("domHash"),
                "domHashAfter": after.get("domHash"),
                "domLengthBefore": before.get("domLength"),
                "domLengthAfter": after.get("domLength"),
            },
            "schemaVersion": 1,
        }
        file_name = f"click-{candidate_id}.json"
        (outdir / file_name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        records.append(record)
        manifest_entries.append({
            "id": candidate_id,
            "file": file_name,
            "selector": selector,
            "triggerType": record["triggerType"],
            "navigationType": navigation_type,
            "navigationOnly": navigation_only,
            "changedCount": 1 if changed else 0,
            "schemaVersion": 1,
        })

    manifest = {"entries": manifest_entries, "schemaVersion": 1}
    summary = {
        "checked": True,
        "candidatesFound": int(discovery.get("candidatesFound") or len(candidates)),
        "candidatesProcessed": len(records),
        "candidatesCappedAt": int(discovery.get("candidatesCappedAt") or 25),
        "externalNavigation": sum(1 for record in records if record["navigationType"] == "external"),
        "skippedNavigation": sum(1 for record in records if record.get("declaredOnly")),
        "samePageMutations": sum(1 for record in records if record["domMutation"]["changed"]),
        "schemaVersion": 1,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    spec_py = Path(__file__).with_name("state-structure-spec.py")
    if spec_py.is_file():
        subprocess.run(["python3", str(spec_py), str(ref_dir)], check=True)

    print(f"capture-click: wrote {len(records)} click candidate(s) to {outdir}/", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("Usage: capture-click.sh <url> <session> <ref_dir>", file=sys.stderr)
        return 1
    return capture(argv[1], argv[2], Path(argv[3]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
