#!/usr/bin/env bash
# duration-easing-grounding-check.sh — enforce that impl transition
# durations and easings trace back to ref-measured values.
#
# Usage:
#   duration-easing-grounding-check.sh <ref-dir> <impl-root>
#
# 2026-05-22 SKILL.md Tier 3 rule:
#   "Duration / easing / threshold values are extracted from ref
#    artifacts, bundles, or runtime measurements — never guessed."
#
# Gate logic:
#   1. Read ref's transition-spec.json. For each entry, collect any
#      "duration" / "easing" / "delay" / "timing" / "ease" fields.
#   2. Walk impl src/ for transition rules. Find duration values in
#      CSS (transition-duration, animation-duration), inline styles,
#      Tailwind config (theme.transitionDuration), Framer Motion
#      props (duration={...}), and timeline scripts (gsap, lenis).
#   3. Each impl duration/easing value must either:
#      - Exactly match a ref-spec entry's duration/easing, OR
#      - Match a value present in ref bundles or ref CSS, OR
#      - Be in the standard-token allowlist (CSS keywords like
#        `ease`, `ease-in-out`, `linear`; common round numbers
#        like 200ms, 300ms, 500ms when ref has no signal for that
#        element).
#   4. FAIL when impl uses non-allowlisted duration/easing not in
#      ref data. The failing values are likely freehand/guessed picks
#      ungrounded in the reference.
#
# Skips when:
#   - ref has no transition-spec entries with timing
#   - impl has no transition declarations
#
# Writes:
#   <ref-dir>/duration-easing-grounding.json
#
# Exit 0 on pass/skip, 1 on too many invented values, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: duration-easing-grounding-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?impl-root required}"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

OUT="$REF_DIR/duration-easing-grounding.json"
MAX_INVENTED="${DURATION_GROUNDING_MAX_INVENTED:-scale}"

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT" "$MAX_INVENTED" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir, impl_root, out_path, max_inv_arg = sys.argv[1:5]
ref_p = Path(ref_dir)
impl_p = Path(impl_root)
out_p = Path(out_path)

# ── Standard-token allowlist (CSS keyword easings + universal durations)
ALLOW_EASINGS = {
    "linear", "ease", "ease-in", "ease-out", "ease-in-out",
    "cubic-bezier",  # any cubic-bezier(...) — too varied to enumerate
    "step-start", "step-end", "steps",
}
# Common short durations agents legitimately use as defaults; we
# don't flag these unless the ref explicitly signaled a different value
# for the SAME selector class (out of scope for this static gate).
ALLOW_DURATIONS_MS = {100, 150, 200, 250, 300, 400, 500, 600, 700, 800, 1000}

# ── Collect ref durations/easings ─────────────────────────────────────
ref_durations: set[int] = set()
ref_easings: set[str] = set()

def norm_duration_to_ms(v: str | int | float) -> int | None:
    if isinstance(v, (int, float)):
        # Heuristic: numeric without unit, < 50 probably seconds, else ms
        if v < 50:
            return int(v * 1000)
        return int(v)
    s = str(v).strip().lower()
    m = re.match(r"^([\d.]+)\s*(ms|s)?$", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2) or "ms"
    return int(n * 1000) if unit == "s" else int(n)

def norm_easing(v: str) -> str:
    s = str(v).strip().lower()
    if s.startswith("cubic-bezier"):
        return "cubic-bezier"
    return s

# Read transition-spec.json
spec_p = ref_p / "transition-spec.json"
if spec_p.exists():
    try:
        spec = json.loads(spec_p.read_text(encoding="utf-8"))
        entries = spec.get("transitions") or spec.get("entries") or []

        def collect_spec_timing(obj: object) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("duration", "delay"):
                        n = norm_duration_to_ms(v)
                        if n is not None:
                            ref_durations.add(n)
                    if k in ("easing", "ease", "timingFunction") and isinstance(v, str):
                        ref_easings.add(norm_easing(v))
                    collect_spec_timing(v)
            elif isinstance(obj, list):
                for item in obj:
                    collect_spec_timing(item)

        for e in entries:
            collect_spec_timing(e)
    except Exception:
        pass

# Also scan transition-coverage / motion / runtime-spec artifacts
for name in ("transition-coverage.json", "runtime-spec-coverage.json",
            "animations-detected.json", "motion-coverage.json"):
    p = ref_p / name
    if not p.exists():
        continue
    try:
        text = p.read_text(encoding="utf-8")
        # Pull every numeric ms-ish value out
        for m in re.finditer(r"\"(?:duration|delay)\"\s*:\s*([\d.]+)\s*(ms|s)?", text):
            try:
                n = float(m.group(1))
                unit = m.group(2) or "ms"
                ref_durations.add(int(n * 1000) if unit == "s" else int(n))
            except ValueError:
                continue
        for m in re.finditer(r"\"(?:easing|ease|timingFunction)\"\s*:\s*\"([^\"]+)\"", text):
            ref_easings.add(norm_easing(m.group(1)))
    except Exception:
        continue

# Full ref CSS corpus — spec/coverage artifacts sample only the transitions
# the extractor noticed; durations/easings copied verbatim from raw ref CSS
# must also count as grounded (mirrors color-token-grounding css scan).
css_dir = ref_p / "css"
if css_dir.is_dir():
    for css_file in sorted(css_dir.glob("*.css")):
        try:
            css_text = css_file.read_text(encoding="utf-8", errors="ignore")[:4_000_000]
        except OSError:
            continue
        for m in re.finditer(r"\b([\d.]+)(ms|s)\b", css_text):
            try:
                n = float(m.group(1))
                ref_durations.add(int(n * 1000) if m.group(2) == "s" else int(n))
            except ValueError:
                continue
        for m in re.finditer(
            r"\b(linear|ease|ease-in|ease-out|ease-in-out|step-start|step-end|"
            r"steps\([^)]*\)|cubic-bezier\([^)]*\))", css_text,
        ):
            ref_easings.add(norm_easing(m.group(1)))

if not ref_durations and not ref_easings:
    out_p.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reasons": ["ref has no duration/easing signal — gate does not apply"],
        "rule": (
            "SKILL.md Tier 3: duration/easing values must be extracted from "
            "ref artifacts, never guessed. Gate skips when ref has no timing "
            "signal to compare against."
        ),
    }, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "skip", "out": str(out_p)}))
    sys.exit(0)

# ── Walk impl source ──────────────────────────────────────────────────
IMPL_EXT = {".css", ".scss", ".sass", ".tsx", ".jsx", ".ts", ".js"}
SCAN_DIRS = [impl_p / d for d in ("src", "app", "styles", "components", "lib")]
SCAN_DIRS = [d for d in SCAN_DIRS if d.exists()]
if not SCAN_DIRS:
    SCAN_DIRS = [impl_p]

DURATION_PAT = re.compile(
    r"(?:transition-duration|animation-duration|"
    r"duration\s*[:=]|delay\s*[:=]|"
    r"--[a-z-]*-duration|--[a-z-]*-delay)"
    r"[\s:={'\"]*([\d.]+)\s*(ms|s)?",
    re.IGNORECASE,
)
EASING_PAT = re.compile(
    # Framework union: CSS, Framer Motion (ease), GSAP (ease:), anime.js
    # (easing:), motion-one (easing:). All share the same shape.
    r"(?:transition-timing-function|animation-timing-function|"
    r"transitionTimingFunction|animationTimingFunction|"
    r"easing\s*[:=]|ease\s*[:=]|"
    r"--[a-z-]*-easing|--[a-z-]*-timing)"
    r"[\s:={]*(['\"]?)([a-zA-Z0-9._-]+(?:\([^)]*\))?)",
    re.IGNORECASE,
)
CONST_STRING_PAT = re.compile(
    r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*['\"]([^'\"]+)['\"]",
)
CONST_TUPLE_PAT = re.compile(
    r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*\[([^\]]*)\]",
)
SCRIPT_EASING_PROP_PAT = re.compile(
    r"\b(?:transitionTimingFunction|animationTimingFunction|easing|ease)\s*[:=]\s*",
    re.IGNORECASE,
)
CSS_IN_JS_EASING_PAT = re.compile(
    r"\b(?:transition-timing-function|animation-timing-function)\s*:\s*([^;\n`]+)",
    re.IGNORECASE,
)
TEMPLATE_LITERAL_PAT = re.compile(r"`(?:\\.|[^`])*`", re.DOTALL)
TEMPLATE_INTERPOLATION_PAT = re.compile(r"\$\{\s*([^{}]+?)\s*\}")
EXACT_IDENT_PAT = re.compile(r"[A-Za-z_$][\w$]*")
IDENT_PAT = re.compile(r"\b([A-Za-z_$][\w$]*)\b")
MEMBER_INDEX_PAT = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\[\s*(\d+)\s*\]")
QUOTED_VALUE_PAT = re.compile(r"['\"]([^'\"]+)['\"]")
QUOTED_MEMBER_KEY_PAT = re.compile(r"\[\s*(['\"])[^'\"]*\1\s*\]")
QUOTED_COMPARISON_RHS_PAT = re.compile(
    r"((?:===?|!==?)\s*)['\"][^'\"]*['\"]",
)
QUOTED_COMPARISON_LHS_PAT = re.compile(
    r"['\"][^'\"]*['\"](\s*(?:===?|!==?))",
)
SPRING_PAT = re.compile(
    r"(?:type\s*[:=]\s*['\"]spring['\"]|"
    r"\bstiffness\s*[:=]\s*[\d.]+|"
    r"\bdamping\s*[:=]\s*[\d.]+|"
    r"\bmass\s*[:=]\s*[\d.]+|"
    r"\btension\s*[:=]\s*[\d.]+|"
    r"\bfriction\s*[:=]\s*[\d.]+|"
    r"\belastic\.(?:in|out|inOut)|"
    r"\bspring\.(?:in|out|inOut))",
    re.IGNORECASE,
)

impl_durations: set[int] = set()
impl_easings: set[str] = set()
impl_spring_uses = 0
files_scanned = 0
ignored_reference_mirror_files: list[str] = []


def is_script_source(path: Path) -> bool:
    return path.suffix.lower() in {".tsx", ".jsx", ".ts", ".js"}


def bounded_expr(text: str, start: int) -> str:
    end = start
    quote = ""
    bracket_depth = 0
    paren_depth = 0
    brace_depth = 0
    while end < len(text):
        ch = text[end]
        prev = text[end - 1] if end > start else ""
        if quote:
            if ch == quote and prev != "\\":
                quote = ""
            end += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            end += 1
            continue
        if ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            if brace_depth == 0:
                break
            brace_depth -= 1
        elif (
            ch in {",", ";", "\n"}
            and bracket_depth == 0
            and paren_depth == 0
            and brace_depth == 0
        ):
            break
        end += 1
    return text[start:end]


def split_easing_values(value: str) -> list[str]:
    values: list[str] = []
    part_start = 0
    paren_depth = 0
    for index, ch in enumerate(value):
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "," and paren_depth == 0:
            values.append(value[part_start:index].strip())
            part_start = index + 1
    values.append(value[part_start:].strip())
    return [norm_easing(part) for part in values if part]


def add_easing_value(values: set[str], value: str) -> None:
    for part in split_easing_values(value):
        values.add(part)


def resolve_template_interpolations(
    raw_value: str,
    const_string_values: dict[str, str],
) -> str | None:
    chunks: list[str] = []
    cursor = 0
    found = False
    for interpolation in TEMPLATE_INTERPOLATION_PAT.finditer(raw_value):
        found = True
        chunks.append(raw_value[cursor:interpolation.start()])
        expr = interpolation.group(1).strip()
        resolved = None
        if EXACT_IDENT_PAT.fullmatch(expr):
            resolved = const_string_values.get(expr)
        if resolved is None:
            return None
        chunks.append(resolved)
        cursor = interpolation.end()
    if not found:
        return None if "${" in raw_value else raw_value
    chunks.append(raw_value[cursor:])
    resolved_value = "".join(chunks)
    return None if "${" in resolved_value else resolved_value


def collect_script_easing_values(
    text: str,
    const_string_values: dict[str, str],
    const_tuple_values: dict[str, list[str]],
) -> set[str]:
    values: set[str] = set()
    for template in TEMPLATE_LITERAL_PAT.findall(text):
        for raw_css_value in CSS_IN_JS_EASING_PAT.findall(template):
            resolved_css_value = resolve_template_interpolations(
                raw_css_value,
                const_string_values,
            )
            if resolved_css_value is not None:
                add_easing_value(values, resolved_css_value)
    for prop in SCRIPT_EASING_PROP_PAT.finditer(text):
        expr = bounded_expr(text, prop.end())
        literal_expr = QUOTED_MEMBER_KEY_PAT.sub("[]", expr)
        literal_expr = QUOTED_COMPARISON_RHS_PAT.sub(r"\1 ", literal_expr)
        literal_expr = QUOTED_COMPARISON_LHS_PAT.sub(r" \1", literal_expr)
        for quoted in QUOTED_VALUE_PAT.findall(literal_expr):
            add_easing_value(values, quoted)
        for name, index_text in MEMBER_INDEX_PAT.findall(expr):
            tuple_values = const_tuple_values.get(name)
            if tuple_values is None:
                continue
            index = int(index_text)
            if index < len(tuple_values):
                add_easing_value(values, tuple_values[index])
        without_literals = QUOTED_VALUE_PAT.sub(" ", literal_expr)
        without_members = MEMBER_INDEX_PAT.sub(" ", without_literals)
        for ident in IDENT_PAT.findall(without_members):
            resolved = const_string_values.get(ident)
            if resolved is not None:
                add_easing_value(values, resolved)
    return values


def is_reference_mirror(path: Path) -> bool:
    """Return True for captured reference CSS copied into the impl tree."""
    try:
        rel = path.relative_to(impl_p)
    except ValueError:
        rel = path
    lower_parts = {part.lower() for part in rel.parts}
    return (
        bool(lower_parts & {"ref-css", "reference-css", "ref_css", "reference_css"})
        or rel.name.lower() in {"reference.css", "ref.css"}
    )


for d in SCAN_DIRS:
    for path in d.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix.lower() not in IMPL_EXT:
            continue
        if "node_modules" in path.parts or ".next" in path.parts or "dist" in path.parts:
            continue
        if is_reference_mirror(path):
            try:
                ignored_reference_mirror_files.append(
                    str(path.relative_to(impl_p))
                )
            except ValueError:
                ignored_reference_mirror_files.append(str(path))
            continue
        files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        const_string_values = {
            name: norm_easing(value)
            for name, value in CONST_STRING_PAT.findall(text)
        }
        const_tuple_values = {
            name: [norm_easing(value) for value in QUOTED_VALUE_PAT.findall(body)]
            for name, body in CONST_TUPLE_PAT.findall(text)
        }
        for m in DURATION_PAT.finditer(text):
            try:
                n = float(m.group(1))
                unit = m.group(2) or "ms"
                impl_durations.add(int(n * 1000) if unit == "s" else int(n))
            except ValueError:
                continue
        if is_script_source(path):
            for val in collect_script_easing_values(
                text,
                const_string_values,
                const_tuple_values,
            ):
                if val in {"none", "var", "inherit", "initial", "unset"}:
                    continue
                impl_easings.add(norm_easing(val))
            impl_spring_uses += len(SPRING_PAT.findall(text))
            continue
        for m in EASING_PAT.finditer(text):
            raw = m.group(2)
            val = raw.lower()
            if val in {"none", "var", "inherit", "initial", "unset"}:
                continue
            impl_easings.add(norm_easing(val))
        # Spring physics detection — count occurrences but don't fault them
        # when ref also signals spring use (rare to extract from artifacts
        # so we treat impl spring presence as informational pass).
        impl_spring_uses += len(SPRING_PAT.findall(text))

# ── Match impl → ref ──────────────────────────────────────────────────
invented_dur: list[int] = []
matched_dur: list[int] = []
for d in sorted(impl_durations):
    if d in ref_durations:
        matched_dur.append(d)
    elif d in ALLOW_DURATIONS_MS:
        matched_dur.append(d)
    else:
        # Within 50ms tolerance of any ref duration counts as match
        if any(abs(d - r) <= 50 for r in ref_durations):
            matched_dur.append(d)
        else:
            invented_dur.append(d)

spring_family_pat = re.compile(
    r"^(spring|elastic|bounce|back)(\.|$)", re.IGNORECASE
)

invented_eas: list[str] = []
matched_eas: list[str] = []
for e in sorted(impl_easings):
    if e in ref_easings or e in ALLOW_EASINGS:
        matched_eas.append(e)
    elif impl_spring_uses > 0 and spring_family_pat.match(e):
        matched_eas.append(e)  # spring-family easing on a spring-using impl
    else:
        invented_eas.append(e)

if max_inv_arg == "scale":
    max_invented = max(2, (len(ref_durations) + len(ref_easings)) // 4)
else:
    try:
        max_invented = int(max_inv_arg)
    except ValueError:
        max_invented = 2

total_invented = len(invented_dur) + len(invented_eas)
reasons: list[str] = []
if total_invented > max_invented:
    status = "fail"
    if invented_dur:
        reasons.append(
            f"{len(invented_dur)} duration(s) not in ref (allowed: "
            f"{max_invented} total). Examples: " + ", ".join(f"{d}ms" for d in invented_dur[:5])
        )
    if invented_eas:
        reasons.append(
            f"{len(invented_eas)} easing(s) not in ref. Examples: "
            + ", ".join(invented_eas[:5])
        )
else:
    status = "pass"

payload = {
    "schemaVersion": 1,
    "status": status,
    "refDurations": sorted(ref_durations),
    "refEasings": sorted(ref_easings),
    "implDurations": sorted(impl_durations),
    "implEasings": sorted(impl_easings),
    "implSpringUses": impl_spring_uses,
    "matchedDurations": sorted(matched_dur),
    "matchedEasings": sorted(matched_eas),
    "inventedDurations": sorted(invented_dur),
    "inventedEasings": sorted(invented_eas),
    "maxInvented": max_invented,
    "filesScanned": files_scanned,
    "ignoredReferenceMirrorFiles": sorted(set(ignored_reference_mirror_files)),
    "reasons": reasons,
    "nextAction": (
        "Replace impl duration/easing values with ref-measured values from "
        "transition-spec.json. Read the ref's compiled JS bundle for the "
        "exact ms / cubic-bezier values rather than guessing 'plausible' "
        "values like 300ms ease-out."
        if (status == "fail") else "duration / easing values grounded in ref data"
    ),
    "rule": (
        "Every impl transition duration / easing must match a value present "
        "in ref's transition-spec / transition-coverage / runtime-spec / "
        "motion-coverage artifacts, or be within 50ms of a ref duration. "
        "Standard CSS keywords (ease, ease-in-out, linear, cubic-bezier(...)) "
        "and round-number defaults (100/150/200/.../1000ms) are allowlisted."
    ),
}

out_p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "invented": total_invented, "out": str(out_p)}, ensure_ascii=False))
sys.exit(0 if status in ("pass", "skip") else 1)
PY
