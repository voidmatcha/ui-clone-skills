#!/usr/bin/env bash
# lottie-scroll-scrub-check.sh — Require frame control for scroll-scrubbed Lottie.
#
# Usage:
#   bash lottie-scroll-scrub-check.sh <ref-dir> <impl-root> [<ref-url> <impl-url> <session>]
#
# Output:
#   <ref-dir>/lottie-scroll-scrub.json

set -uo pipefail

REF_DIR="${1:?Usage: lottie-scroll-scrub-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?Missing impl-root}"
REF_URL="${3:-}"
IMPL_URL="${4:-}"
SESSION="${5:-lottie-scroll-scrub-$$}"
OUT="$REF_DIR/lottie-scroll-scrub.json"
mkdir -p "$REF_DIR"

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT" "$REF_URL" "$IMPL_URL" "$SESSION" <<'PY'
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])
ref_url = sys.argv[4]
impl_url = sys.argv[5]
session = sys.argv[6]

SKIP_DIRS = {"node_modules", ".next", "dist", "build", "coverage", ".git"}
REF_EXTS = {".js", ".json", ".css", ".html", ".txt"}
IMPL_EXTS = {".js", ".jsx", ".ts", ".tsx", ".json"}
LOTTIE_RUNTIME_RE = re.compile(
    r"\b(?:lottie|bodymovin)\.(?:loadAnimation|setSubframe|goToAndStop|playSegments)"
    r"|(?:from|require\()\s*['\"][^'\"]*(?:lottie-web|lottie-react|bodymovin|dotlottie)[^'\"]*"
    r"|<\s*Lottie\b|dotlottie|lottie-player|\.lottie\b",
    re.IGNORECASE,
)
LOTTIE_ASSET_RE = re.compile(
    r'(?:["\']lottie["\']|(?:^|[,{])\s*lottie)\s*:\s*(?!null\b)(?:\{|\[|["\'][^"\']+)',
    re.IGNORECASE,
)
SCROLL_RE = re.compile(
    r"ScrollTrigger|scrollYProgress|useScroll|scrub\s*:\s*true|onUpdate\s*:"
    r"|addEventListener\(\s*['\"]scroll|onscroll|window\.scroll|document\.scroll"
    r"|scrollTo\s*\(|scrollIntoView\s*\(|\bscroll[YX]\b",
    re.IGNORECASE,
)
FRAME_CONTROL_RE = re.compile(
    r"goToAndStop|playSegments|setSubframe|currentFrame|totalFrames|seek\s*\(|setFrame|setSeeker",
    re.IGNORECASE,
)
LOTTIE_USAGE_RE = re.compile(r"lottie-web|lottie-react|loadAnimation|<\s*Lottie\b|dotlottie", re.IGNORECASE)
AUTOPLAY_LOOP_RE = re.compile(r"\bautoplay\b|\bloop\b|autoplay\s*[:=]\s*true|loop\s*[:=]\s*true", re.IGNORECASE)
CONTAINER_ID_RES = [
    re.compile(r"container\s*:\s*document\.getElementById\(\s*['\"](?P<id>[^'\"]+)['\"]\s*\)", re.IGNORECASE),
    re.compile(r"container\s*:\s*document\.querySelector\(\s*['\"]#(?P<id>[^'\"]+)['\"]\s*\)", re.IGNORECASE),
    re.compile(r"\b(?P<id>[A-Za-z_$][\w$]*Lottie\w*)\s*=\s*(?:lottie|bodymovin)\.loadAnimation\s*\(", re.IGNORECASE),
]
IMPL_LOTTIE_CONTAINER_RE = re.compile(
    r"(?:loadAnimation\s*\(|<\s*Lottie\b|lottie-player|dotlottie-player|\blottie\s*:)",
    re.IGNORECASE,
)


def read_limited(path: Path, limit: int = 1_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def iter_files(root: Path, exts: set[str]) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in exts:
            out.append(path)
    return out

ref_file_texts = [read_limited(path, 250_000) for path in iter_files(ref_dir, REF_EXTS)]
ref_text = "\n".join(ref_file_texts)
has_lottie = bool(LOTTIE_RUNTIME_RE.search(ref_text) or LOTTIE_ASSET_RE.search(ref_text))
co_located = any(
    (LOTTIE_RUNTIME_RE.search(text) or LOTTIE_ASSET_RE.search(text)) and SCROLL_RE.search(text)
    for text in ref_file_texts
)
requires = bool(has_lottie and co_located)
impl_text = "\n".join(read_limited(path, 400_000) for path in iter_files(impl_root, IMPL_EXTS))
frame_control = bool(FRAME_CONTROL_RE.search(impl_text))
lottie_usage = bool(LOTTIE_USAGE_RE.search(impl_text))
autoplay_loop_only = bool(AUTOPLAY_LOOP_RE.search(impl_text) and not frame_control)
expected_containers: list[str] = []
seen_containers: set[str] = set()
for text in ref_file_texts:
    if not (LOTTIE_RUNTIME_RE.search(text) or LOTTIE_ASSET_RE.search(text)):
        continue
    for rx in CONTAINER_ID_RES:
        for match in rx.finditer(text):
            container_id = match.group("id")
            if container_id and container_id not in seen_containers:
                seen_containers.add(container_id)
                expected_containers.append(container_id)
impl_container_mentions = {
    container_id: bool(
        re.search(rf"(?:^|[^\w$]){re.escape(container_id)}(?:$|[^\w$])", impl_text)
        or re.search(rf"#{re.escape(container_id)}(?:['\".\s#\]])", impl_text)
    )
    for container_id in expected_containers
}
impl_lottie_container_count = len(IMPL_LOTTIE_CONTAINER_RE.findall(impl_text))
issues: list[dict[str, object]] = []
runtime_probe: dict[str, object] = {"status": "not-attempted", "reason": "ref_url/impl_url not supplied"}

if requires:
    if not lottie_usage:
        issues.append({"kind": "missing-lottie-runtime", "message": "Reference uses scroll-scrubbed Lottie but impl has no Lottie runtime usage."})
    if not frame_control:
        issues.append({
            "kind": "missing-scroll-scrubbed-frame-control",
            "message": "Scroll-scrubbed Lottie requires goToAndStop/playSegments/currentFrame-style frame control, not autoplay/loop only.",
        })
    if autoplay_loop_only:
        issues.append({"kind": "autoplay-loop-only", "message": "Autoplay/loop plays time-based animation instead of binding frames to scroll progress."})
    missing_containers = [
        container_id
        for container_id, present in impl_container_mentions.items()
        if not present
    ]
    if missing_containers:
        issues.append({
            "kind": "missing-expected-lottie-container",
            "missing": missing_containers,
            "message": "Reference binds scroll-scrubbed Lottie instances to named containers that are absent from the implementation.",
        })
    if len(expected_containers) >= 2 and impl_lottie_container_count < len(expected_containers):
        issues.append({
            "kind": "lottie-container-count-mismatch",
            "expected": len(expected_containers),
            "actual": impl_lottie_container_count,
            "message": "Reference has multiple scroll-scrubbed Lottie containers; implementation appears to mount fewer Lottie instances.",
        })

    if ref_url and impl_url:
        runtime_probe = {"status": "not-attempted", "reason": "agent-browser unavailable"}
        try:
            subprocess.run(["agent-browser", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
            has_agent_browser = True
        except (OSError, subprocess.SubprocessError):
            has_agent_browser = False

        if has_agent_browser:
            probe_js = r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const scrollRatios = [0, 0.25, 0.5, 0.75, 1];
  const maxScroll = () => Math.max(0, Math.max(
    document.documentElement ? document.documentElement.scrollHeight : 0,
    document.body ? document.body.scrollHeight : 0
  ) - window.innerHeight);
  const labelFor = (el) => {
    const id = el.id || el.getAttribute("data-lottie-id") || el.getAttribute("data-animation-path") || el.getAttribute("data-lottie-src");
    if (id) return String(id).replace(/^#/, "");
    const cls = typeof el.className === "string" ? el.className.split(/\s+/).find((c) => /lottie/i.test(c)) : "";
    if (cls) return "." + cls;
    return el.tagName.toLowerCase();
  };
  const findInstance = (el) => {
    try {
      if (el.getLottie) return el.getLottie();
      if (el._lottie) return el._lottie;
      const regs = (window.lottie && (window.lottie._animations || (window.lottie.getRegisteredAnimations && window.lottie.getRegisteredAnimations()))) || [];
      for (const a of Array.from(regs || [])) {
        if (a && (a.wrapper === el || (el.contains && a.wrapper && el.contains(a.wrapper)))) return a;
      }
    } catch (e) {}
    return null;
  };
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return rect.width > 2 && rect.height > 2 &&
      rect.bottom > 0 && rect.top < window.innerHeight &&
      cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity || 1) > 0.05;
  };
  const containers = () => Array.from(new Set([
    ...document.querySelectorAll("lottie-player, dotlottie-player"),
    ...document.querySelectorAll("[data-lottie], [data-animation-path], [data-lottie-src]"),
    ...document.querySelectorAll("[id*=\"lottie\" i], [class*=\"lottie\" i]"),
  ]));
  // lottie-scroll-state-mismatch can fail when the probe samples lottie
  // currentFrame too soon after
  // scrollTo. ScrollTrigger callbacks run inside requestAnimationFrame, and
  // GSAP-driven lottie scrubbing typically needs 1-3 frames after the scroll
  // event before the .lottie instance's currentFrame settles. 220ms (≈13
  // frames at 60fps) sounds long but in practice Lenis-wrapped pages or
  // GSAP scroll-pin contexts can defer the update further. Bump to a
  // double-RAF + 500ms wait which is empirically safe for the worst case.
  const waitForLottieSettle = async () => {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    await sleep(500);
  };
  const samples = [];
  for (const ratio of scrollRatios) {
    window.scrollTo({ top: Math.round(maxScroll() * ratio), behavior: "instant" });
    await waitForLottieSettle();
    const entries = containers().map((el) => {
      const rect = el.getBoundingClientRect();
      const inst = findInstance(el);
      const totalFrames = inst && typeof inst.totalFrames === "number" ? inst.totalFrames : null;
      const currentFrame = inst && typeof inst.currentFrame === "number" ? inst.currentFrame : null;
      return {
        label: labelFor(el),
        visible: visible(el),
        hasPaint: !!el.querySelector("svg,canvas") || el.childElementCount > 0,
        currentFrame,
        totalFrames,
        progress: currentFrame != null && totalFrames ? Number((currentFrame / totalFrames).toFixed(4)) : null,
        bbox: { top: Math.round(rect.top), height: Math.round(rect.height) },
      };
    });
    samples.push({
      ratio,
      scrollY: Math.round(window.scrollY),
      visibleCount: entries.filter((e) => e.visible).length,
      activeCount: entries.filter((e) => e.visible && e.hasPaint).length,
      entries,
    });
  }
  return { scrollRatios, samples };
})()
"""

            def run_probe(url: str, suffix: str) -> dict[str, object]:
                probe_session = f"{session}-{suffix}"
                with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                try:
                    subprocess.run(["agent-browser", "--session", probe_session, "open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30)
                    subprocess.run(["agent-browser", "--session", probe_session, "wait", "1200"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10)
                    with tmp_path.open("w", encoding="utf-8") as fh:
                        subprocess.run(["agent-browser", "--session", probe_session, "eval", "--json", probe_js], stdout=fh, stderr=subprocess.DEVNULL, check=False, timeout=40)
                    raw = tmp_path.read_text(encoding="utf-8", errors="ignore").strip()
                    for line in reversed(raw.splitlines()):
                        line = line.strip()
                        if not (line.startswith("{") or line.startswith('"{')):
                            continue
                        value = json.loads(line)
                        if isinstance(value, str):
                            value = json.loads(value)
                        if isinstance(value, dict) and "data" in value and isinstance(value["data"], dict) and "result" in value["data"]:
                            value = value["data"]["result"]
                        if isinstance(value, dict):
                            return value
                    return {"error": "probe-parse-failed", "raw": raw[-500:]}
                finally:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    subprocess.run(["agent-browser", "--session", probe_session, "close"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)

            ref_probe = run_probe(ref_url, "ref")
            impl_probe = run_probe(impl_url, "impl")
            runtime_probe = {"status": "pass", "ref": ref_probe, "impl": impl_probe}
            if "error" in ref_probe or "error" in impl_probe:
                runtime_probe["status"] = "fail"
                runtime_probe["reason"] = "runtime probe could not be parsed"
                issues.append({
                    "kind": "lottie-scroll-runtime-probe-failed",
                    "message": "Scroll-position Lottie runtime probe could not be parsed; visible/active parity is unproven.",
                    "refError": ref_probe.get("error"),
                    "implError": impl_probe.get("error"),
                })
            else:
                mismatches: list[dict[str, object]] = []
                for ref_sample, impl_sample in zip(ref_probe.get("samples", []), impl_probe.get("samples", [])):
                    ratio = ref_sample.get("ratio")
                    ref_visible = int(ref_sample.get("visibleCount") or 0)
                    impl_visible = int(impl_sample.get("visibleCount") or 0)
                    ref_active = int(ref_sample.get("activeCount") or 0)
                    impl_active = int(impl_sample.get("activeCount") or 0)
                    if impl_visible < ref_visible or impl_active < ref_active:
                        mismatches.append({
                            "ratio": ratio,
                            "refVisible": ref_visible,
                            "implVisible": impl_visible,
                            "refActive": ref_active,
                            "implActive": impl_active,
                        })
                if mismatches:
                    runtime_probe["status"] = "fail"
                    runtime_probe["mismatches"] = mismatches
                    issues.append({
                        "kind": "lottie-scroll-state-mismatch",
                        "message": "Implementation has fewer visible/active Lottie containers than reference at one or more scroll ratios.",
                        "mismatches": mismatches[:10],
                    })
        else:
            issues.append({
                "kind": "lottie-scroll-runtime-probe-unavailable",
                "message": "ref-url and impl-url were provided but agent-browser is unavailable; visible/active Lottie parity is unproven.",
            })

status = "fail" if issues else "pass"
if not requires:
    status = "skip"

artifact = {
    "schemaVersion": 1,
    "status": status,
    "requiresScrollScrubbedLottie": requires,
    "hasLottieSignal": has_lottie,
    "coLocatedScrollLottieSignal": co_located,
    "lottieUsage": lottie_usage,
    "frameControl": frame_control,
    "autoplayLoopOnly": autoplay_loop_only,
    "expectedContainers": expected_containers,
    "implContainerMentions": impl_container_mentions,
    "implLottieContainerCount": impl_lottie_container_count,
    "runtimeProbe": runtime_probe,
    "requiredSignals": ["goToAndStop", "playSegments", "currentFrame", "totalFrames", "seek(progress)"],
    "issues": issues,
}
out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
if status == "fail":
    print(f"❌ Lottie scroll scrub: FAIL ({len(issues)} issue(s))")
    sys.exit(1)
print(f"✅ Lottie scroll scrub: {status.upper()}")
PY
