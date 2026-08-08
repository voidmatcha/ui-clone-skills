import json
import os
import re
import sys
import unicodedata
from pathlib import Path

scaffold_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
out_path = Path(sys.argv[3]) if sys.argv[3] else None


# Build allowlist from scaffold: collect every `text` field from the tree.
scaffold = json.loads(scaffold_path.read_text(encoding="utf-8"))
allowed_strings = set()
required_strings = set()

SKIP_TEXT_TAGS = {"script", "style", "noscript", "template"}
OVERLAY_RE = re.compile(
    r"(cookiebot|cookie[-_\s]?consent|consent|onetrust|iubenda|osano|"
    r"(?<![a-z0-9])cky(?![a-z0-9]))",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", text).strip())


def is_cjk_letter(char: str) -> bool:
    """Return whether a Unicode letter belongs to a CJK writing system."""
    codepoint = ord(char)
    in_cjk_range = (
        0x1100 <= codepoint <= 0x11FF
        or 0x3040 <= codepoint <= 0x30FF
        or 0x3100 <= codepoint <= 0x312F
        or 0x3130 <= codepoint <= 0x318F
        or 0x31A0 <= codepoint <= 0x31BF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF66 <= codepoint <= 0xFF9D
        or 0x20000 <= codepoint <= 0x2FA1F
    )
    return in_cjk_range and unicodedata.category(char).startswith("L")


def fidelity_tokens(text: str) -> list[str]:
    """Tokenize English as before while retaining unsegmented CJK letters."""
    normalized = normalize_text(text).lower()
    ascii_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9']+", normalized)
        if len(token) >= 3
    ]
    return ascii_tokens + [char for char in normalized if is_cjk_letter(char)]


def is_overlay_node(node: dict) -> bool:
    haystack = " ".join(
        str(node.get(key, ""))
        for key in ("id", "class", "className", "selector", "aria-label", "role")
    )
    return bool(OVERLAY_RE.search(haystack))


def add_text(text: str, *, required: bool) -> None:
    norm = normalize_text(text)
    if not norm:
        return
    allowed_strings.add(norm)
    if required:
        required_strings.add(norm)
    # Also allow individual newline-split lines (some JSX renders
    # "Real Food Wins" as two lines: "Real Food" and "Wins").
    for line in re.split(r"[\r\n]+", text):
        line = normalize_text(line)
        if line:
            allowed_strings.add(line)
            if required:
                required_strings.add(line)


def walk(node: object, depth: int = 0) -> None:
    # structure.json already has a bounded extraction depth, but real React
    # component trees can legitimately reach 18+ levels. The old depth-12
    # guard discarded most card/navigation copy from the fidelity allowlist,
    # making faithful generated text look fabricated. Keep a generous safety
    # ceiling for malformed hand-authored artifacts without truncating normal
    # captured trees.
    if depth > 64 or not isinstance(node, dict):
        return
    # Symmetric to the impl-side <script> strip below: dom-scaffold.json
    # captures every node's text, including Next.js RSC payloads and runtime
    # polyfill bodies inside <script> tags. Those bodies (e.g.
    # `self.__next_f.push(...)`, `$RB=[];$RV=function...`) are not
    # user-visible content the impl is expected to reproduce, but without
    # this filter the bidirectional check flags them as "missing" forever.
    # Mirror the dom-extraction skip list (script/style/noscript/template).
    tag = node.get("tag", "")
    if isinstance(tag, str) and tag.lower() in SKIP_TEXT_TAGS:
        return
    # Cookie/consent overlays are intentionally stripped by visual comparison
    # and are not clone targets. Do not require their vendor copy in JSX.
    if is_overlay_node(node):
        return
    text = node.get("text")
    text_full = node.get("textFull")
    if isinstance(text_full, str) and text_full.strip():
        # Mid-text-span paragraph (loop-e2e-9): `text` is the DIRECT text
        # nodes joined with the inline-span content dropped ("treating
        # —much") — an extraction artifact no faithful impl can render.
        # Require the LIVE order (textFull); keep the joined fragments as
        # allowlist-only evidence so faithful partial fragments never flag
        # as fabrication.
        add_text(text_full, required=True)
        if isinstance(text, str) and text.strip():
            add_text(text, required=False)
    elif isinstance(text, str) and text.strip():
        add_text(text, required=True)
    # Accessibility/asset copy is scanned on the implementation side as
    # fabrication evidence, so the captured counterpart must be eligible as
    # allowlist evidence too. It is not required rendered DOM text.
    for key in ("aria-label", "alt", "title", "placeholder"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            add_text(value, required=False)
    for child in node.get("children", []) or []:
        walk(child, depth + 1)


walk(scaffold.get("tree", {}))

# element-roles.json is a broader rendered-text capture than dom-scaffold.json
# for Readymag-like runtimes. Use it as fabrication allowlist evidence, while
# keeping missing-text requirements tied to dom-scaffold leaf text.
roles_path = scaffold_path.parent / "element-roles.json"
if roles_path.exists():
    try:
        roles_data = json.loads(roles_path.read_text(encoding="utf-8"))
    except Exception:
        roles_data = {}
    for el in roles_data.get("elements", []) if isinstance(roles_data, dict) else []:
        if not isinstance(el, dict) or is_overlay_node(el):
            continue
        tag = el.get("tag", "")
        if isinstance(tag, str) and tag.lower() in SKIP_TEXT_TAGS:
            continue
        text = el.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        norm = normalize_text(text)
        if len(norm) > 220:
            continue
        if any(marker in norm for marker in ("@keyframes", "@font-face", "{", "}")):
            continue
        add_text(norm, required=False)

# Also accept short/common strings — these are noise from the allowlist diff
# perspective (numbers, single words like "Get started", etc.). We're after
# real semantic phrases the agent could fabricate.
def is_meaningful(s: str) -> bool:
    """Filter for strings worth checking — long enough to be content,
    not punctuation or boilerplate."""
    # Extraction-only media markers describe structure; they are not visible
    # UI copy and must never become required JSX text.
    if re.fullmatch(r"\{\{[A-Za-z][A-Za-z0-9_-]*\}\}", s):
        return False
    if sum(1 for char in s if is_cjk_letter(char)) >= 2:
        return True
    if len(s) < 8:
        return False
    if not re.search(r"[A-Za-z]{4,}", s):
        return False
    # Skip JSX attribute boilerplate.
    if re.fullmatch(r"[\w-]+", s):
        return False
    return True


# Extract JSX text-position strings from each impl component.
# JSX text positions are content between `>` and `<` that isn't itself a tag,
# OR content inside `{"..."}` expressions used for verbatim render. Common
# patterns:
#   <h1>Real Food Wins</h1>         → "Real Food Wins"
#   <p>{"America is..."}</p>        → "America is..."
#   <p>{`Multi\nline`}</p>          → "Multi\nline"
#   alt="hero image"                → "hero image"  (attribute evidence only)
#   title="..."                     → "..."         (attribute evidence only)
#
# We use regexes (not a TSX parser) to keep this dependency-free. Conservative:
# only match patterns we're confident are visible text.
JSX_TEXT_TAGS = {
    "a",
    "abbr",
    "address",
    "article",
    "aside",
    "b",
    "blockquote",
    "br",
    "button",
    "caption",
    "cite",
    "code",
    "dd",
    "del",
    "details",
    "dfn",
    "div",
    "dt",
    "em",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "i",
    "ins",
    "kbd",
    "label",
    "legend",
    "li",
    "main",
    "mark",
    "nav",
    "ol",
    "option",
    "p",
    "pre",
    "q",
    "s",
    "samp",
    "section",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "svg",
    "td",
    "th",
    "time",
    "u",
    "ul",
    "var",
}
JSX_TEXT_TAG_PATTERN = "(?:" + "|".join(sorted(JSX_TEXT_TAGS)) + ")"
JSX_TEXT_PATTERNS = [
    # Plain JSX text: >Some Text<, including generator-formatted multiline
    # text nodes. The previous single-line regex missed most link/footer copy
    # even though the browser rendered it verbatim.
    # Avoid capturing JSX expressions ({foo}), JSX comments, or attribute fragments.
    # Trailing whitespace before the closing `<` is allowed: a mid-text inline
    # child (`treating <span>`) otherwise drops the ENTIRE leading fragment
    # from both the fabrication scan and the missing-side word set (loop-e2e-9).
    # The lookahead keeps whitespace-only gaps out while allowing a one-codepoint
    # punctuation/emoji node needed to reconstruct adjacent visible copy.
    # Include the actual HTML tag boundary instead of accepting any `>...<`
    # pair. TypeScript generic calls such as
    # `querySelectorAll<HTMLElement>(...)` otherwise turn arbitrary source code
    # between adjacent generics into fabricated "visible text".
    (
        re.compile(
            rf"</?{JSX_TEXT_TAG_PATTERN}(?:\s[^<>]*?)?>"
            r"\s*((?=[^<>{}]*\S)[^<>{}]+?)\s*(?=<)",
            re.DOTALL | re.IGNORECASE,
        ),
        True,
    ),
    # JSX inline string literals: >{"some text"}< or >{'some text'}<
    (re.compile(r"\{\s*[\"']([^\"'{}\n]+)[\"']\s*\}"), True),
    # JSX attributes/custom-component props are fabrication/accessibility
    # evidence only. Even text-like props are not rendered DOM-text nodes and
    # must never alter the ordered stream used to reconstruct split copy.
    (re.compile(r"\b(?:alt|title|aria-label|placeholder)\s*=\s*[\"']([^\"'\n]+)[\"']"), False),
    (re.compile(r"\b(?:label|heading|subheading|title|subtitle|description|caption|name|content|copy|message|text)\s*=\s*[\"']([^\"'\n]+)[\"']"), False),
]

STATIC_STRING_CONST_RE = re.compile(
    r"""\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(?:
        "((?:\\.|[^"\\])*)" |
        '((?:\\.|[^'\\])*)' |
        `((?:\\.|[^`\\])*)`
    )\s*;""",
    re.DOTALL | re.VERBOSE,
)
JSX_STRING_CONST_CHILD_RE = re.compile(
    r">\s*\{\s*([A-Za-z_$][\w$]*)\s*\}\s*<",
    re.DOTALL,
)


def decode_static_js_string(raw: str) -> str:
    """Decode the small escape subset used by static JSX copy constants."""
    return re.sub(
        r"\\(u[0-9A-Fa-f]{4}|n|r|t|\\|\"|'|`)",
        lambda match: (
            chr(int(match.group(1)[1:], 16))
            if match.group(1).startswith("u")
            else {
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "\\": "\\",
                '"': '"',
                "'": "'",
                "`": "`",
            }[match.group(1)]
        ),
        raw,
    )


SCAN_EXCLUDE = {"node_modules", ".next", "dist", "build", ".turbo"}
all_components = []
src_root = impl_dir / "src"
if src_root.is_dir():
    for pattern in ("*.tsx", "*.jsx"):
        for p in src_root.rglob(pattern):
            if any(part in SCAN_EXCLUDE for part in p.parts):
                continue
            all_components.append(p)
impl_components = sorted(all_components)
required_meaningful = sorted(s for s in required_strings if is_meaningful(s))
if not impl_components:
    status = "fail" if required_meaningful else "pass"
    out = {
        "status": status,
        "reason": (
            "no components found but scaffold has meaningful text"
            if required_meaningful else "no components yet — no meaningful scaffold text"
        ),
        "components_checked": 0,
        "required_meaningful_strings": len(required_meaningful),
        "missing_count": len(required_meaningful),
        "missing": [{"text": s[:160]} for s in required_meaningful[:50]],
        "fabrications": [],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0 if status == "pass" else 1)


fabrications = []
impl_strings = []
impl_fragments = []
total_meaningful = 0


for comp_path in impl_components:
    body = comp_path.read_text(encoding="utf-8")
    # Strip JSX comments and JS line/block comments — cheap regex pass.
    body_clean = re.sub(r"/\*[\s\S]*?\*/", "", body)
    # Strip actual full-line comments only. A blanket `//...` regex truncates
    # JSX lines at `https://`, deleting the link's visible text from the scan.
    body_clean = re.sub(r"(?m)^[ \t]*//[^\n]*", "", body_clean)
    # Validation run finding: Next.js App Router RSC hydration payloads
    # appear inside <script> tags as `self.__next_f.push([1, "..."])`
    # — large fragments of JSON-encoded server output that text-
    # fidelity flagged as non-verbatim impl text. Strip <script> and
    # <style> blocks before extracting JSX text positions; these
    # blocks never carry user-visible copy.
    body_clean = re.sub(
        r"<script\b[^>]*>[\s\S]*?</script\s*>", "", body_clean,
        flags=re.IGNORECASE,
    )
    body_clean = re.sub(
        r"<style\b[^>]*>[\s\S]*?</style\s*>", "", body_clean,
        flags=re.IGNORECASE,
    )
    # Treat literal JSX whitespace expressions as the whitespace they render.
    # Otherwise `</svg>{' '}No</label>` has no `>` boundary immediately
    # before the visible copy, so the plain-text matcher silently drops it.
    body_clean = re.sub(r"""\{\s*(["'])\s+\1\s*\}""", " ", body_clean)

    fragment_matches = []
    static_string_consts = {}
    for const_match in STATIC_STRING_CONST_RE.finditer(body_clean):
        raw_value = next(
            value for value in const_match.groups()[1:] if value is not None
        )
        # Template interpolation is runtime-dependent, so only treat a
        # backtick literal as captured copy when it is genuinely static.
        if "${" in raw_value:
            continue
        static_string_consts[const_match.group(1)] = decode_static_js_string(raw_value)

    # React clones commonly hoist long accessibility/body copy into a local
    # string constant and render it as `<p>{DESCRIPTION}</p>`. Keep the gate
    # fail-closed by accepting the value only when that exact constant is used
    # as a direct JSX child; an unrelated source constant remains invisible.
    for child_match in JSX_STRING_CONST_CHILD_RE.finditer(body_clean):
        raw = static_string_consts.get(child_match.group(1))
        norm = normalize_text(raw) if raw is not None else ""
        if norm:
            fragment_matches.append(
                (child_match.start(1), child_match.end(1), norm, True)
            )

    for pat, in_dom_text_stream in JSX_TEXT_PATTERNS:
        for m in pat.finditer(body_clean):
            raw = m.group(1)
            norm = normalize_text(raw)
            if norm:
                fragment_matches.append(
                    (m.start(1), m.end(1), norm, in_dom_text_stream)
                )

    seen_spans = set()
    seen_meaningful = set()
    for start, end, norm, in_dom_text_stream in sorted(fragment_matches):
        span = (start, end)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        # Preserve punctuation/emoji-only JSX fragments for reconstructing
        # required copy split across adjacent nodes. Attribute strings remain
        # fabrication/accessibility evidence, but they are not rendered DOM
        # text and must not alter the ordered text stream.
        if in_dom_text_stream:
            impl_fragments.append(norm)
        if not is_meaningful(norm):
            continue
        if norm in seen_meaningful:
            continue
        seen_meaningful.add(norm)
        impl_strings.append(norm)
        total_meaningful += 1
        # Substring tolerance: scaffold has "Real Food Wins"; impl may
        # split into "Real Food" + "Wins". CJK containment is directional:
        # an impl fragment may belong to captured copy, but captured copy
        # appearing inside a longer impl string does not authorize the
        # extra visible text. Ignore whitespace for split CJK JSX nodes.
        ok = False
        for allowed in allowed_strings:
            if any(is_cjk_letter(char) for char in norm):
                compact_norm = re.sub(r"\s+", "", norm)
                compact_allowed = re.sub(r"\s+", "", allowed)
                if compact_norm == compact_allowed or compact_norm in compact_allowed:
                    ok = True
                    break
            elif norm == allowed or norm in allowed or allowed in norm:
                ok = True
                break
        if not ok:
            fabrications.append({
                "component": comp_path.name,
                "text": norm[:160],
            })


impl_blob = " ".join(impl_fragments)
impl_word_set: set[str] = set()
for s in impl_strings:
    impl_word_set.update(fidelity_tokens(s))
missing = []
for required in required_meaningful:
    # Exact/source-order preservation check. The full required phrase must
    # appear in one rendered text node or across adjacent rendered text nodes.
    if required in impl_strings or required in impl_blob:
        continue
    # CJK does not use spaces consistently, and adjacent JSX nodes can split a
    # faithful phrase at any character boundary. Ignore source-only whitespace
    # while preserving exact character order. Character-set coverage would let
    # reordered CJK copy pass, so the relaxed token fallback remains ASCII-only.
    if any(is_cjk_letter(char) for char in required):
        compact_required = re.sub(r"\s+", "", required)
        compact_impl = re.sub(r"\s+", "", impl_blob)
        if compact_required in compact_impl:
            continue
        missing.append({"text": required[:160]})
        continue
    # Relaxed: 90%+ token coverage across the impl src tree. Catches
    # split-but-rendered phrases without admitting omissions.
    required_words = fidelity_tokens(required)
    if required_words:
        hits = sum(1 for w in required_words if w in impl_word_set)
        if hits / len(required_words) >= 0.9:
            continue
    missing.append({"text": required[:160]})


# Degenerate-scaffold guard: dom-scaffold is the authoritative "required" text
# ground truth. If extraction yielded ~no meaningful scaffold text (a JS-heavy
# site captured pre-hydration, or a text walk that missed every leaf) yet the
# impl renders substantial text, the missing-side check is vacuous and silently
# under-reports — and if the fabricated strings happen to land in the
# element-roles allowlist there are 0 fabrications too, so the gate FALSE-PASSES
# a clone built on no text ground truth (observed: a JS-heavy reference site
# whose body copy was fabricated yet passed). Refuse to validate; fail loudly and demand
# re-extraction instead of giving false confidence. Same class as the blank-ref
# refStd guard for the perceptual section gate. Env escape hatch for genuine
# JS-only-text architectures.
scaffold_text_floor = int(os.environ.get("TEXT_FIDELITY_SCAFFOLD_FLOOR", "1"))
impl_text_floor = int(os.environ.get("TEXT_FIDELITY_IMPL_FLOOR", "5"))
degenerate_scaffold = (
    len(required_meaningful) < scaffold_text_floor
    and total_meaningful >= impl_text_floor
)

status = "fail" if fabrications or missing or degenerate_scaffold else "pass"
out = {
    "status": status,
    "degenerate_scaffold": degenerate_scaffold,
    "degenerate_reason": (
        f"ref dom-scaffold has {len(required_meaningful)} meaningful text leaves "
        f"(< {scaffold_text_floor}) but impl renders {total_meaningful} meaningful "
        "strings — extraction likely failed; text fidelity cannot be validated. "
        "Re-extract dom-scaffold before trusting this clone."
    ) if degenerate_scaffold else "",
    "components_checked": len(impl_components),
    "total_meaningful_strings": total_meaningful,
    "required_meaningful_strings": len(required_meaningful),
    "allowlist_size": len(allowed_strings),
    "fabrications_count": len(fabrications),
    "fabrications": fabrications[:50],  # cap output
    "missing_count": len(missing),
    "missing": missing[:50],
    "rule": (
        "Every meaningful JSX text-position string in impl/src/ must appear "
        "in captured rendered-text evidence (dom-scaffold.json plus "
        "element-roles.json allowlist), and every meaningful non-overlay "
        "dom-scaffold text string must be rendered by the impl. Invented text "
        "and omitted source text both fail this gate."
    ),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
if out_path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if status == "pass" else 1)
