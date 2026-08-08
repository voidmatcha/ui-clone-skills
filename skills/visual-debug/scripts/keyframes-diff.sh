#!/usr/bin/env bash
# keyframes-diff.sh — Compare @keyframes declarations between ref and impl
#
# Extracts all @keyframes rules from both pages' stylesheets and diffs:
#   1. Keyframes only on one side (missing/extra animations)
#   2. Same-name keyframes with different step properties
#
# Catches:
#   - Missing entrance animations (hero text fade-in)
#   - Different timing curves baked into @keyframes
#   - Wrong @keyframes referenced via animation-name
#
# Usage: bash keyframes-diff.sh <session> <orig-url> <impl-url> [out-dir]

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found"; exit 2
fi

SESSION="${1:?Usage: keyframes-diff.sh <session> <orig-url> <impl-url> [out-dir]}"
ORIG_URL="${2:?Missing orig-url}"
IMPL_URL="${3:?Missing impl-url}"
OUT_DIR="${4:-tmp/keyframes-diff}"

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-3000}"

mkdir -p "$OUT_DIR"

REF_SESS="${SESSION}-kf-ref"
IMPL_SESS="${SESSION}-kf-impl"

# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
TMP_IMPL="$(mktemp /tmp/kf-impl-XXXXXX)"
mv "$TMP_IMPL" "${TMP_IMPL}.json"
TMP_IMPL="${TMP_IMPL}.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
TMP_REF="$(mktemp /tmp/kf-ref-XXXXXX)"
mv "$TMP_REF" "${TMP_REF}.json"
TMP_REF="${TMP_REF}.json"

cleanup() {
  agent-browser --session "$REF_SESS" close >/dev/null 2>&1 || true
  agent-browser --session "$IMPL_SESS" close >/dev/null 2>&1 || true
  rm -f "$TMP_IMPL" "$TMP_REF"
}
trap cleanup EXIT

echo "═══ Keyframes Diff ═══"
echo "  orig: $ORIG_URL"
echo "  impl: $IMPL_URL"
echo ""

agent-browser --session "$REF_SESS" open "$ORIG_URL" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" open "$IMPL_URL" >/dev/null 2>&1
agent-browser --session "$REF_SESS"  set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$IMPL_SESS" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$REF_SESS"  wait "$WAIT_MS" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" wait "$WAIT_MS" >/dev/null 2>&1

EXTRACT_JS=$(cat <<'JSEOF'
(() => {
  const out = {};
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    if (!rules) continue;
    for (const rule of rules) {
      if (rule.type !== CSSRule.KEYFRAMES_RULE && rule.constructor.name !== 'CSSKeyframesRule') continue;
      const name = rule.name;
      const steps = [];
      for (const kf of rule.cssRules) {
        const obj = { stop: kf.keyText };
        const decl = kf.style;
        for (let i = 0; i < decl.length; i++) {
          const prop = decl[i];
          obj[prop] = decl.getPropertyValue(prop).trim();
        }
        steps.push(obj);
      }
      out[name] = steps;
    }
  }
  return JSON.stringify(out);
})()
JSEOF
)

agent-browser --session "$REF_SESS"  eval "$EXTRACT_JS" > "$TMP_REF"  2>&1
agent-browser --session "$IMPL_SESS" eval "$EXTRACT_JS" > "$TMP_IMPL" 2>&1

if [ ! -s "$TMP_REF" ] || [ ! -s "$TMP_IMPL" ]; then
  echo "ERROR: keyframes extraction returned empty"; exit 2
fi

python3 - "$TMP_REF" "$TMP_IMPL" "$OUT_DIR" <<'PYEOF'
import json, sys, os

def parse(path):
    with open(path) as f: raw = f.read().strip()
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(json.loads(raw))
    return json.loads(raw)

ref  = parse(sys.argv[1])
impl = parse(sys.argv[2])
out_dir = sys.argv[3]

ref_names  = set(ref.keys())
impl_names = set(impl.keys())

only_ref  = sorted(ref_names  - impl_names)
only_impl = sorted(impl_names - ref_names)
shared    = sorted(ref_names & impl_names)

# Diff shared keyframes step-by-step
shared_diffs = []
for name in shared:
    rs = ref[name]; ist = impl[name]
    rs_by_stop  = {s["stop"]: s for s in rs}
    ist_by_stop = {s["stop"]: s for s in ist}
    stops = sorted(set(rs_by_stop) | set(ist_by_stop))
    diffs = []
    for stop in stops:
        rs_step  = rs_by_stop.get(stop, {})
        is_step  = ist_by_stop.get(stop, {})
        keys = (set(rs_step) | set(is_step)) - {"stop"}
        for k in sorted(keys):
            rv = rs_step.get(k, "")
            iv = is_step.get(k, "")
            if rv != iv:
                diffs.append({"stop": stop, "prop": k, "ref": rv, "impl": iv})
    if diffs:
        shared_diffs.append({"name": name, "diffs": diffs})

# ── Markdown ──
md = os.path.join(out_dir, "keyframes-diff.md")
with open(md, "w") as f:
    f.write("# Keyframes Diff Report\n\n")
    f.write(f"**Ref keyframes**: {len(ref_names)}  ")
    f.write(f"**Impl keyframes**: {len(impl_names)}  ")
    f.write(f"**Only ref**: {len(only_ref)}  ")
    f.write(f"**Only impl**: {len(only_impl)}  ")
    f.write(f"**Shared with diffs**: {len(shared_diffs)}\n\n")

    if only_ref:
        f.write("## Only in ref (missing from impl)\n\n")
        for n in only_ref:
            f.write(f"- `{n}` ({len(ref[n])} steps)\n")
        f.write("\n")
    if only_impl:
        f.write("## Only in impl (extra)\n\n")
        for n in only_impl:
            f.write(f"- `{n}` ({len(impl[n])} steps)\n")
        f.write("\n")
    if shared_diffs:
        f.write("## Shared keyframes with property diffs\n\n")
        for sd in shared_diffs:
            f.write(f"### `@keyframes {sd['name']}`\n\n")
            f.write("| Stop | Property | Ref | Impl |\n|---|---|---|---|\n")
            for d in sd["diffs"][:30]:
                f.write(f"| {d['stop']} | `{d['prop']}` | {d['ref']} | {d['impl']} |\n")
            f.write("\n")

# ── JSON ──
payload = {
    "only_ref": only_ref,
    "only_impl": only_impl,
    "shared_diffs": shared_diffs,
}
js = os.path.join(out_dir, "keyframes-diff.json")
with open(js, "w") as f:
    json.dump(payload, f, indent=2)

# run-required-checks dispatches this check into <ref-dir>/transitions because
# verification-plan declares transitions/keyframes-diff-result.txt. Keep the
# legacy root JSON too so transition-proof-rollup's historical read path
# (<ref-dir>/keyframes-diff.json) remains populated.
if os.path.basename(out_dir) == "transitions":
    root_js = os.path.join(os.path.dirname(out_dir), "keyframes-diff.json")
    with open(root_js, "w") as f:
        json.dump(payload, f, indent=2)

# ── Canonical result artifact ──
# verification-plan declares this check's artifact as
# transitions/keyframes-diff-result.txt; without writing it the gate keeps
# reporting MISSING_ARTIFACT even after the check runs (producer/plan
# filename drift). Verdict follows the plan's "warn" severity contract:
# PASS when ref and impl keyframes match, WARN when names are missing or
# shared keyframes diverge.
result = os.path.join(out_dir, "keyframes-diff-result.txt")
status = "PASS" if not only_ref and not shared_diffs else "WARN"
with open(result, "w") as f:
    f.write(
        f"keyframes-diff: ref={len(ref_names)} impl={len(impl_names)} "
        f"only_ref={len(only_ref)} only_impl={len(only_impl)} "
        f"shared_diffs={len(shared_diffs)} -> {status}\n"
    )
    for n in only_ref:
        f.write(f"MISSING {n}\n")
    for sd in shared_diffs:
        f.write(f"DIFF {sd['name']} ({len(sd['diffs'])} property diff(s))\n")

print(f"  Ref: {len(ref_names)} keyframes, Impl: {len(impl_names)} keyframes")
print(f"  Only ref:  {len(only_ref)}  (missing from impl)")
print(f"  Only impl: {len(only_impl)} (extra)")
print(f"  Shared with diffs: {len(shared_diffs)}")
print(f"  Report: {md}")

if only_ref or shared_diffs:
    print()
    if only_ref[:6]:
        print("  Missing keyframes (top 6):")
        for n in only_ref[:6]:
            print(f"    - {n}")
    if shared_diffs[:3]:
        print("  Diffed keyframes (top 3):")
        for sd in shared_diffs[:3]:
            print(f"    - {sd['name']}: {len(sd['diffs'])} prop diffs")

sys.exit(1 if (only_ref or shared_diffs) else 0)
PYEOF
PYEXIT=$?
exit $PYEXIT
