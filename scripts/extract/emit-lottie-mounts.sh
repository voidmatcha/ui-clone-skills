#!/usr/bin/env bash
# emit-lottie-mounts.sh — deterministic Lottie slot-mount codegen.
#
# Why it matters:
#   transition-spec.json already carries the complete slot->asset map for every
#   Lottie mount the reference makes (container selector, path, mobilePath,
#   loop, autoplay, trigger). Left to the LLM, that data is re-improvised: the
#   navercorp clone mounted only 2 of 4 slots, inverted autoplay/loop flags, and
#   invented a splash overlay. Mounting is not a judgement call — it is
#   mechanical codegen. This script emits impl/src/generated/lottie-mounts.ts
#   with one loadAnimation() per spec entry using the EXACT flags/paths, so the
#   agent only wires triggers (event play, scroll progress) and never authors
#   the slot->asset bindings.
#
#   The static counterpart is lottie-slot-identity-check.sh, which fails when an
#   impl mount's (container, path, loop, autoplay) does not match the spec.
#
# Usage:
#   emit-lottie-mounts.sh <ref-dir> <impl-dir>
#     <ref-dir>   tmp/ref/<component>/ containing transition-spec.json
#     <impl-dir>  impl root (contains src/); module -> src/generated/lottie-mounts.ts
#
# Output:
#   <impl-dir>/src/generated/lottie-mounts.ts   (only when >=1 lottie entry)
#   <ref-dir>/lottie-mounts-emitted.json        {mounted:[...], skipped:[...]}
#
# Exit: 0 always (a spec with no lottie entries is a valid no-op); 2 on setup
#       error (missing spec / impl dir).

set -euo pipefail

REF_DIR="${1:?Usage: emit-lottie-mounts.sh <ref-dir> <impl-dir>}"
IMPL_DIR="${2:?Usage: emit-lottie-mounts.sh <ref-dir> <impl-dir>}"

SPEC="$REF_DIR/transition-spec.json"
if [ ! -f "$SPEC" ]; then
  echo "▸ emit-lottie-mounts: SKIP — no transition-spec.json in $REF_DIR"
  exit 0
fi
if [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl dir not found: $IMPL_DIR" >&2
  exit 2
fi

python3 - "$SPEC" "$IMPL_DIR" "$REF_DIR" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
ref_dir = Path(sys.argv[3])

try:
    spec = json.loads(spec_path.read_text())
except (OSError, json.JSONDecodeError) as e:
    print(f"ERROR: cannot read {spec_path}: {e}", file=sys.stderr)
    sys.exit(2)

transitions = spec.get("transitions") if isinstance(spec, dict) else None
transitions = transitions if isinstance(transitions, list) else []


def _is_lottie(entry: dict) -> bool:
    anim = entry.get("animation")
    if isinstance(anim, dict):
        if str(anim.get("type", "")).lower() == "lottie":
            return True
        if str(anim.get("library", "")).lower() in ("bodymovin", "lottie", "lottie-web"):
            return True
        if isinstance(anim.get("path"), str) and anim["path"].lower().endswith(".json") \
                and "lottie" in anim["path"].lower():
            return True
    return False


lottie_entries = [t for t in transitions if isinstance(t, dict) and _is_lottie(t)]

report = {"schemaVersion": 1, "module": "src/generated/lottie-mounts.ts",
          "mounted": [], "skipped": []}


def _pascal(s: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", s or "")
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Slot"


def _ts_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _bool(v) -> str:
    return "true" if bool(v) else "false"


# Classify each entry's trigger and collect codegen rows.
rows = []
for e in lottie_entries:
    eid = str(e.get("id") or "").strip()
    anim = e.get("animation") if isinstance(e.get("animation"), dict) else {}
    target = e.get("target") or e.get("selector") or ""
    path = anim.get("path")
    if not eid:
        report["skipped"].append({"id": None, "reason": "entry has no id"})
        continue
    if not isinstance(target, str) or not target.strip():
        report["skipped"].append({"id": eid, "reason": "no container target selector"})
        continue
    if not isinstance(path, str) or not path.strip():
        report["skipped"].append({"id": eid, "reason": "no animation.path"})
        continue

    mobile_path = anim.get("mobilePath")
    mobile_path = mobile_path if isinstance(mobile_path, str) and mobile_path.strip() else None
    loop = bool(anim.get("loop"))
    autoplay = bool(anim.get("autoplay"))
    renderer = str(anim.get("renderer") or "svg")
    trig_top = str(e.get("trigger") or "").lower()
    trig_anim = str(anim.get("trigger") or "").lower()
    trig_text = f"{trig_top} {trig_anim}"

    if "gotoandstop" in trig_text.replace(" ", "") or "scroll" in trig_text:
        kind = "scroll-scrub"
    elif not autoplay and ("play" in trig_text or "event" in trig_text):
        kind = "manual-play"
    elif autoplay:
        kind = "autoplay"
    else:
        # autoplay:false with no play/scroll hint — still a manual slot the agent
        # must start; expose a play handle rather than silently never playing.
        kind = "manual-play"

    rows.append({
        "id": eid, "target": target.strip(), "path": path.strip(),
        "mobilePath": mobile_path, "loop": loop, "autoplay": autoplay,
        "renderer": renderer, "kind": kind,
    })
    report["mounted"].append({
        "id": eid, "container": target.strip(), "path": path.strip(),
        "mobilePath": mobile_path, "loop": loop, "autoplay": autoplay,
        "triggerKind": kind,
    })

# Always write the report (even for zero mounts) so downstream can observe intent.
(ref_dir / "lottie-mounts-emitted.json").write_text(
    json.dumps(report, indent=2) + "\n", encoding="utf-8"
)

if not rows:
    print("▸ emit-lottie-mounts: no lottie entries in spec — nothing emitted")
    sys.exit(0)

ids_csv = ", ".join(r["id"] for r in rows)
L = []
L.append("// AUTO-GENERATED by scripts/extract/emit-lottie-mounts.sh — DO NOT EDIT.")
L.append("// Deterministic Lottie slot mounts derived from transition-spec.json.")
L.append("// One loadAnimation() per spec lottie entry with the EXACT container /")
L.append("// path / loop / autoplay from the spec. Wiring (event play, scroll")
L.append("// progress) is the agent's job; the bindings below are the ground-truth")
L.append("// slot->asset map and must not be edited.")
L.append("//")
L.append(f"// Spec entries: {ids_csv}")
L.append("import lottie from 'lottie-web';")
L.append("import type { AnimationItem } from 'lottie-web';")
L.append("")
L.append("export type LottieMounts = Record<string, AnimationItem>;")
L.append("")
L.append("const MOBILE_QUERY = '(max-width: 768px)';")
L.append("")
L.append("/** Mount every Lottie slot present in the DOM. Returns slot-id -> instance. */")
L.append("export function initLottieMounts(): LottieMounts {")
L.append("  const mounts: LottieMounts = {};")
for r in rows:
    L.append("")
    L.append(f"  // {r['id']} — {r['target']} — {r['kind']}")
    L.append("  {")
    L.append(f"    const container = document.querySelector<HTMLElement>({_ts_str(r['target'])});")
    L.append("    if (container) {")
    L.append(f"      mounts[{_ts_str(r['id'])}] = lottie.loadAnimation({{")
    L.append("        container,")
    L.append(f"        renderer: {_ts_str(r['renderer'])},")
    L.append(f"        loop: {_bool(r['loop'])},")
    L.append(f"        autoplay: {_bool(r['autoplay'])},")
    if r["mobilePath"]:
        L.append("        path: window.matchMedia(MOBILE_QUERY).matches")
        L.append(f"          ? {_ts_str(r['mobilePath'])}")
        L.append(f"          : {_ts_str(r['path'])},")
    else:
        L.append(f"        path: {_ts_str(r['path'])},")
    L.append("      });")
    L.append("    }")
    L.append("  }")
L.append("")
L.append("  return mounts;")
L.append("}")

# Per-slot trigger helpers: play handle for manual slots, scroll driver skeleton
# for scroll-scrub slots. The agent wires the call site; the binding is generated.
for r in rows:
    pas = _pascal(r["id"])
    if r["kind"] == "manual-play":
        L.append("")
        L.append(f"/** {r['id']} has autoplay:false — call to start it on its trigger event. */")
        L.append(f"export function play{pas}(mounts: LottieMounts): void {{")
        L.append(f"  mounts[{_ts_str(r['id'])}]?.play();")
        L.append("}")
    elif r["kind"] == "scroll-scrub":
        L.append("")
        L.append(f"/**")
        L.append(f" * {r['id']} is scroll-scrubbed (goToAndStop on scroll state). Wire `progress`")
        L.append(f" * (0..1) from your scroll controller; the frame binding is generated.")
        L.append(f" */")
        L.append(f"export function drive{pas}OnScroll(mounts: LottieMounts, progress: number): void {{")
        L.append(f"  const anim = mounts[{_ts_str(r['id'])}];")
        L.append("  if (!anim) return;")
        L.append("  const p = Math.max(0, Math.min(1, progress));")
        L.append("  anim.goToAndStop(p * (anim.totalFrames || 0), true);")
        L.append("}")

gen_dir = impl_dir / "src" / "generated"
gen_dir.mkdir(parents=True, exist_ok=True)
out = gen_dir / "lottie-mounts.ts"
out.write_text("\n".join(L) + "\n", encoding="utf-8")

rel = os.path.relpath(out, impl_dir)
print(f"▸ emit-lottie-mounts: emitted {len(rows)} slot(s) -> {rel}")
for r in rows:
    print(f"    {r['id']:<18} {r['target']:<16} loop={r['loop']} autoplay={r['autoplay']} [{r['kind']}]")
PY

exit 0
