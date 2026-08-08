# mypy: disable-error-code="arg-type"

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ref_dir = Path(sys.argv[1])
impl_arg_raw = sys.argv[2]
out_path = Path(sys.argv[3])


def write(payload: dict[str, Any], code: int) -> None:
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sys.exit(code)


if not impl_arg_raw:
    write(
        {
            "schemaVersion": 1,
            "status": "skip",
            "checked": 0,
            "missingPlacements": [],
            "implRoot": "",
            "implSrcDir": "",
            "reason": "impl root not found",
        },
        0,
    )

impl_arg = Path(impl_arg_raw)
if impl_arg.name in {"src", "app", "pages"}:
    impl_root = impl_arg.parent
    impl_src = impl_arg
else:
    impl_root = impl_arg
    impl_src = impl_root / "src" if (impl_root / "src").is_dir() else impl_root

visible_path = ref_dir / "visible-images.json"
section_map_path = ref_dir / "section-map.json"
component_map_path = ref_dir / "component-map.json"
missing_inputs = [str(p.name) for p in (visible_path, section_map_path, component_map_path) if not p.is_file()]
if missing_inputs:
    write(
        {
            "schemaVersion": 1,
            "status": "skip",
            "checked": 0,
            "missingPlacements": [],
            "implRoot": str(impl_root),
            "implSrcDir": str(impl_src),
            "reason": f"missing inputs: {', '.join(missing_inputs)}",
        },
        0,
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def list_payload(raw: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


def first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Read coordinates from flat rows or extractor rect/bbox payloads."""
    for key in keys:
        value = row.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    for container_key in ("rect", "bbox", "bounds", "frame"):
        container = row.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def pascal_from_slug(value: str) -> str:
    words = [word for word in re.split(r"[^a-zA-Z0-9]+", value) if word]
    return "".join(word[:1].upper() + word[1:] for word in words)


# A CSS-module-scoped class hash (e.g. `Hero_root__hUKXz`, `Card_root__a1B2`,
# `styles-3f9d2`). Requiring this shape — rather than any >=4-char substring —
# keeps a generic class like "card" or "active" from matching half the codebase
# and turning the section-scoped controller fold into a near-global src sweep.
_SCOPED_CLASS_RE = re.compile(r"(?:__[A-Za-z0-9]+|[A-Za-z]-?[0-9]{3,}|[0-9]{4,})")


def scoped_source_class(value: Any) -> set[str]:
    """Return the set of module-scoped class tokens in `value`.

    A sourceClass may be a multi-class string like "section Hero_root__hUKXz":
    only the module-scoped token(s) (those carrying a hash separator / digit run)
    anchor per-section asset provenance. Plain semantic tokens ("section", "card")
    are too generic and are dropped. Returns an empty set when no token qualifies.
    """
    if not isinstance(value, str):
        return set()
    tokens: set[str] = set()
    for token in value.split():
        token = token.strip()
        if len(token) < 4:
            continue
        if _SCOPED_CLASS_RE.search(token):
            tokens.add(token)
    return tokens


try:
    visible = list_payload(load_json(visible_path), ("images", "visible", "entries", "items"))
    sections = list_payload(load_json(section_map_path), ("sections", "items"))
    component_raw = load_json(component_map_path)
    components = list_payload(component_raw, ("sections", "components", "items"))
except (OSError, json.JSONDecodeError) as exc:
    write(
        {
            "schemaVersion": 1,
            "status": "fail",
            "checked": 0,
            "missingPlacements": [{"reason": f"malformed input: {exc}"}],
            "implRoot": str(impl_root),
            "implSrcDir": str(impl_src),
        },
        1,
    )

section_rows: list[dict[str, Any]] = []
section_id_to_index: dict[str, int] = {}
section_source_classes: dict[int, set[str]] = {}
for ordinal, row in enumerate(sections):
    if not isinstance(row, dict):
        continue
    top = first_number(row, ("top", "y"))
    height = first_number(row, ("height", "h"))
    bottom = first_number(row, ("bottom", "b"))
    if top is None:
        continue
    if height is None and bottom is not None:
        height = bottom - top
    if height is None:
        continue
    top_i = int(top)
    height_i = int(height)
    section_index = row.get("index", row.get("i", row.get("section_idx", ordinal)))
    try:
        section_index_i = int(section_index)
    except (TypeError, ValueError):
        section_index_i = ordinal
    section_id = row.get("id", row.get("sectionId", row.get("name")))
    section_rows.append({
        "index": section_index_i,
        "top": top_i,
        "bottom": top_i + max(height_i, 1),
        "id": section_id,
        "position": str(row.get("position") or "").strip().lower(),
        "ordinal": ordinal,
    })
    for value in (section_id, row.get("name"), row.get("sourceClass")):
        key = normalize_name(value)
        if key:
            section_id_to_index[key] = section_index_i
    scoped_classes = scoped_source_class(row.get("sourceClass"))
    if scoped_classes:
        section_source_classes.setdefault(section_index_i, set()).update(scoped_classes)

component_files_by_index: dict[int, set[str]] = {}
source_files: list[Path] = []
if impl_src.is_dir():
    for suffix in ("*.tsx", "*.jsx", "*.ts", "*.js"):
        source_files.extend(path for path in impl_src.rglob(suffix) if path.is_file())


def resolve_section_index(row: dict[str, Any], ordinal: int) -> int:
    section_index = row.get("index", row.get("i", row.get("section_idx")))
    try:
        return int(section_index)
    except (TypeError, ValueError):
        pass
    for key in ("sectionId", "id", "sourceSection", "sourceClass", "name"):
        lookup = normalize_name(row.get(key))
        if lookup in section_id_to_index:
            return section_id_to_index[lookup]
    return ordinal


def relative_to_impl(path: Path) -> str:
    try:
        return str(path.relative_to(impl_root))
    except ValueError:
        try:
            return str(path.relative_to(impl_src))
        except ValueError:
            return str(path)


def inferred_component_files(row: dict[str, Any]) -> set[str]:
    bases: set[str] = set()
    for key in ("file", "path", "sectionId", "id", "sourceClass", "componentName", "name"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        variants = {value, pascal_from_slug(value)}
        for suffix in ("Section", "Component", "View"):
            if value.endswith(suffix):
                variants.add(value[: -len(suffix)])
        for variant in variants:
            norm = normalize_name(variant)
            if norm:
                bases.add(norm)
    if not bases:
        return set()
    matches: set[str] = set()
    for path in source_files:
        if normalize_name(path.stem) in bases:
            matches.add(relative_to_impl(path))
    return matches


for ordinal, row in enumerate(components):
    if not isinstance(row, dict):
        continue
    section_index_i = resolve_section_index(row, ordinal)
    scoped_classes = scoped_source_class(row.get("sourceClass"))
    if scoped_classes:
        section_source_classes.setdefault(section_index_i, set()).update(scoped_classes)
    file_value = row.get("file")
    if isinstance(file_value, str) and file_value.strip():
        component_files_by_index.setdefault(section_index_i, set()).add(file_value)
        continue
    inferred = inferred_component_files(row)
    if inferred:
        component_files_by_index.setdefault(section_index_i, set()).update(inferred)


def section_for_top(top_value: Any) -> int | None:
    try:
        top = int(float(top_value))
    except (TypeError, ValueError):
        return None
    matches = [row for row in section_rows if row["top"] <= top < row["bottom"]]
    if matches:
        def match_rank(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
            position = row["position"]
            if position in {"static", "relative", ""}:
                position_rank = 0
            elif position == "absolute":
                position_rank = 1
            else:
                # Fixed/sticky overlays often span the viewport and overlap the
                # content section beneath them. Keep them eligible when they
                # are the only match, but prefer document-flow sections.
                position_rank = 2
            return (
                position_rank,
                row["bottom"] - row["top"],
                abs(top - row["top"]),
                row["index"],
                row["ordinal"],
            )

        return int(min(matches, key=match_rank)["index"])
    # Tolerate small extraction jitter just above/below a section edge.
    nearest = sorted(section_rows, key=lambda row: min(abs(top - row["top"]), abs(top - row["bottom"])))
    return int(nearest[0]["index"]) if nearest else None


def component_path(file_value: str) -> Path:
    rel = Path(file_value)
    if rel.is_absolute():
        return rel
    if rel.parts and rel.parts[0] in {"src", "app", "pages"}:
        return impl_root / rel
    return impl_src / rel


def asset_needles(src: str) -> list[str]:
    # Derive needles from BOTH the encoded and decoded URL forms. For
    # percent-encoded (e.g. Korean) asset names the impl may reference either
    # form; deriving from only one made this check and image-fidelity-check
    # mutually unsatisfiable (decoded-only here vs encoded-only there).
    needles: list[str] = []
    for candidate in dict.fromkeys((unquote(src), src)):
        parsed = urlparse(candidate)
        base = os.path.basename(parsed.path)
        if "/cdn-cgi/image/" in parsed.path:
            # Cloudflare optimizer paths still end with the original asset basename.
            base = os.path.basename(parsed.path)
        if not base:
            continue
        stem = os.path.splitext(base)[0]
        if base not in needles:
            needles.append(base)
        if len(stem) >= 4 and stem not in needles:
            needles.append(stem)
    return needles


def resolve_import_path(from_path: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    base = (from_path.parent / specifier).resolve()
    candidates = [base]
    for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        candidates.append(base.with_suffix(suffix))
    for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        candidates.append(base / f"index{suffix}")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def extract_named_exports(module_text: str, names: set[str]) -> str:
    parts: list[str] = []
    for name in sorted(names):
        escaped = re.escape(name)
        patterns = (
            rf"(?:export\s+)?(?:const|let|var)\s+{escaped}\s*=\s*\[[\s\S]*?\]\s*;",
            rf"(?:export\s+)?(?:const|let|var)\s+{escaped}\s*=[\s\S]*?;",
        )
        for pattern in patterns:
            match = re.search(pattern, module_text)
            if match:
                parts.append(match.group(0))
                break
    return "\n".join(parts)


def imported_named_export_text(component_path_value: Path, component_text: str) -> str:
    parts: list[str] = []
    import_re = re.compile(r"import\s*\{(?P<names>[^}]+)\}\s*from\s*['\"](?P<specifier>[^'\"]+)['\"]")
    for match in import_re.finditer(component_text):
        specifier = match.group("specifier")
        module_path = resolve_import_path(component_path_value, specifier)
        if module_path is None:
            continue
        names: set[str] = set()
        for raw_name in match.group("names").split(","):
            local = raw_name.strip().split(" as ")[0].strip()
            if local:
                names.add(local)
        if not names:
            continue
        try:
            module_text = module_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts.append(extract_named_exports(module_text, names))
    return "\n".join(part for part in parts if part)


# A module counts as an asset-bearing runtime controller only if it assigns an
# <img> src or holds asset paths — not if it merely mentions the section class.
_CONTROLLER_ASSET_RE = re.compile(
    r"\.src\s*=|\bsrc\s*[:=]|setAttribute\(\s*['\"]src['\"]"
    r"|\.(?:webp|png|jpe?g|avif|gif|svg)\b",
    re.IGNORECASE,
)


# --- Lexical JS/TS scanner -------------------------------------------------
#
# Earlier revisions classified positions with raw `str.find` / `"//" in line`
# heuristics. Those bypass on real code:
#   * braces inside string literals or comments (minified bundles) merged
#     unrelated blocks into one whole-file block → whole-file credit;
#   * a `//` anywhere earlier on a line (e.g. inside a "https://" URL literal)
#     wrongly flagged real wiring as a comment;
#   * a class name appearing only as a string-map VALUE looked the same as a
#     real CSS-selector wiring.
#
# `lex_kinds` walks the source ONCE and labels every character as one of
# 'c' (code), 's' (string), or '/' (comment). All downstream logic — class-wiring
# detection, brace matching, and comment detection — is keyed off this array,
# so string/comment content can never masquerade as code.

# Codes are characters so a kinds string can be sliced/indexed cheaply.
_K_CODE = "c"
_K_STR = "s"
_K_COMMENT = "/"


def lex_kinds(text: str) -> str:
    """Return a string the same length as `text` where each position is labelled
    'c' (code), 's' (string body / quotes), or '/' (comment body / markers).

    Handles ' " ` string literals with backslash escapes, // line comments, and
    /* */ block comments. Template-literal `${...}` interpolations are treated as
    string for simplicity — class wirings never live inside a template hole, and
    keeping them string-tagged only makes the gate stricter, never looser."""
    n = len(text)
    kinds = bytearray(n)
    i = 0
    code = ord(_K_CODE)
    string = ord(_K_STR)
    comment = ord(_K_COMMENT)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                kinds[i] = comment
                i += 1
            continue
        if ch == "/" and nxt == "*":
            kinds[i] = comment
            kinds[i + 1] = comment
            i += 2
            while i < n:
                kinds[i] = comment
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    kinds[i + 1] = comment
                    i += 2
                    break
                i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            kinds[i] = string
            i += 1
            while i < n:
                kinds[i] = string
                if text[i] == "\\":
                    if i + 1 < n:
                        kinds[i + 1] = string
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        kinds[i] = code
        i += 1
    return kinds.decode("latin-1")


def pos_is_comment(kinds: str, pos: int) -> bool:
    """True when `pos` falls inside a // or /* */ comment, per the lexer.
    Replaces the old raw `//` find so a `//` inside a string literal (a URL) is
    no longer mistaken for a comment."""
    return 0 <= pos < len(kinds) and kinds[pos] == _K_COMMENT


def strip_comments(text: str) -> str:
    """Blank comment spans to spaces (newlines preserved), leaving code and
    STRING literals intact — the asset needle lives inside a string, so string
    content must survive. A `// hero.webp` note or a `void`-adjacent `/* ...
    referenced so the gate passes */` comment must no longer satisfy the raw
    `needle in evidence_text` placement match."""
    kinds = lex_kinds(text)
    return "".join(
        ch if kinds[i] != _K_COMMENT else ("\n" if ch == "\n" else " ")
        for i, ch in enumerate(text)
    )


def class_is_wired(text: str, kinds: str, idx: int, cls: str) -> bool:
    """True when the class occurrence at `idx` is a real DOM-targeting wiring in
    CODE, not a comment, a string literal, or a plain string-map value.

    A genuine runtime controller references the class as a CSS selector
    (`.Hero_root__hUKXz`) inside querySelector/closest/matches, via
    className / classList, or as a JSX `className=` literal — all of which place
    the class text at a CODE position immediately after a `.` or on a line that
    names a class-targeting API at a CODE position. A central manifest that maps
    `footer: "Hero_root__hUKXz"` keeps the class inside a STRING literal, which
    the lexer marks 's', so it is rejected here. The selector form
    `querySelectorAll('.Hero_root__hUKXz')` keeps the `.<cls>` itself inside a
    string, so we allow the dot-prefixed selector form even when string-tagged,
    but ONLY when the enclosing string is used as a selector argument (a leading
    `.` and a class-targeting API at a code position on the same line)."""
    if pos_is_comment(kinds, idx):
        return False
    line_start = text.rfind("\n", 0, idx) + 1
    line_end = text.find("\n", idx)
    line_end = len(text) if line_end == -1 else line_end

    # CSS-selector form: a `.` immediately precedes the class. The `.<cls>` lives
    # inside a selector string (querySelector('.x')) or in a CSS/JSX selector, so
    # the dot must itself be a real selector dot — accept when a class-targeting
    # DOM API or className/classList appears at a CODE position on this line.
    if idx > 0 and text[idx - 1] == ".":
        if _line_has_class_api(text, kinds, line_start, line_end):
            return True
        # Bare CSS-selector dot in a stylesheet-like context (no API on the line)
        # only counts when at a CODE position (e.g. a real `.cls {` rule), never
        # inside a string-map value.
        if kinds[idx] == _K_CODE:
            return True
        return False

    # className / classList / class= / getElementsByClassName wiring: the class
    # may sit inside a className="..." string, but the API token must be CODE.
    if _line_has_class_api(text, kinds, line_start, line_end):
        return True
    return False


_CLASS_API_RE = re.compile(
    r"querySelector|querySelectorAll|getElementsByClassName|closest|matches"
    r"|className|classList|class\s*="
)


def _line_has_class_api(text: str, kinds: str, line_start: int, line_end: int) -> bool:
    """True when a class-targeting DOM API / attribute token appears at a CODE
    position within [line_start, line_end). Keyed off the lexer so an API-looking
    word inside a string or comment never counts as wiring."""
    line = text[line_start:line_end]
    for m in _CLASS_API_RE.finditer(line):
        if kinds[line_start + m.start()] == _K_CODE:
            return True
    return False


def enclosing_block(text: str, kinds: str, pos: int) -> str:
    """Return the smallest brace-balanced block `{...}` that encloses `pos`,
    counting ONLY braces at CODE positions (per the lexer).

    A runtime controller wires the section class and its asset list inside the
    same function / object body, so that body is the provenance unit. Counting
    raw `{`/`}` chars let braces inside string literals / comments (common in
    minified bundles) merge two distinct blocks into a whole-file span,
    reintroducing whole-file credit. Lexical brace matching keeps the blocks
    distinct. If `pos` sits at module top level, fall back to the surrounding
    blank-line-separated statement so two independent top-level declarations are
    never treated as the same region."""

    def is_code_brace(k: int) -> bool:
        return kinds[k] == _K_CODE

    # Walk outward to the nearest enclosing CODE '{'.
    depth = 0
    start = -1
    i = pos
    while i >= 0:
        ch = text[i]
        if is_code_brace(i):
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    start = i
                    break
                depth -= 1
        i -= 1
    if start == -1:
        # Top-level: bound by surrounding blank-line-separated statement.
        left = text.rfind("\n\n", 0, pos)
        left = 0 if left == -1 else left + 2
        right = text.find("\n\n", pos)
        right = len(text) if right == -1 else right
        return text[left:right]
    # Find the matching CODE close brace for `start`.
    depth = 0
    j = start
    n = len(text)
    while j < n:
        ch = text[j]
        if is_code_brace(j):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : j + 1]
        j += 1
    return text[start:]


def controller_evidence_for_section(source_classes: set[str]) -> str:
    """Fold in only the block(s) of any impl/src module where one of this
    section's source classes CO-OCCURS with an image-src assignment / asset list —
    i.e. the runtime controller body (e.g. a setInterval carousel) that wires the
    section's cards. Section-scoped on the literal class string AND region-scoped
    on the enclosing block, so:

      * a controller for a DIFFERENT section's class never contributes;
      * a central manifest that names this section's class in one region and lists
        an unrelated asset in another region does NOT credit that asset — the
        class reference and the asset needle must land in the SAME block;
      * an asset wired to the wrong cards (present in a sibling block that targets
        a different class) is not mis-attributed to this section.

    Without this fold, a static mapped component (e.g. Footer.tsx) that delegates
    its imagery to a sibling controller (e.g. EatRealCarousel.tsx) false-fails —
    the controller's own asset list IS the placement evidence. The block scope is
    what keeps that credit honest."""
    if not source_classes:
        return ""
    parts: list[str] = []
    seen: set[tuple[str, int, int]] = set()  # (path, class-hit-pos, block-hash)
    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kinds = lex_kinds(text)
        path_key = str(path)
        for cls in source_classes:
            search_from = 0
            while True:
                idx = text.find(cls, search_from)
                if idx == -1:
                    break
                search_from = idx + len(cls)
                # The class must be an actual DOM-targeting wiring at this site —
                # a CSS selector / className at a CODE position — not a comment or
                # a string-map value (which the lexer marks as string).
                if not class_is_wired(text, kinds, idx, cls):
                    continue
                block = enclosing_block(text, kinds, idx)
                # Only the block that BOTH wires the class and assigns an image
                # src counts as a provenance region; a class mention with no
                # co-located src assignment is not placement evidence.
                if not _CONTROLLER_ASSET_RE.search(block):
                    continue
                # Dedupe identical blocks reached via multiple class hits.
                norm_key = (path_key, idx, hash(block))
                if norm_key in seen:
                    continue
                seen.add(norm_key)
                parts.append(block)
    return "\n".join(parts)


checked = 0
missing: list[dict[str, Any]] = []
skipped = 0

for entry in visible:
    if not isinstance(entry, dict):
        continue
    src = entry.get("src") or entry.get("url")
    if not isinstance(src, str) or not src.startswith(("http://", "https://", "//")):
        continue
    section_index = section_for_top(first_number(entry, ("top", "y")))
    if section_index is None:
        skipped += 1
        continue
    files = sorted(component_files_by_index.get(section_index) or [])
    if not files:
        skipped += 1
        continue
    needles = asset_needles(src)
    if not needles:
        skipped += 1
        continue
    checked += 1
    section_ok = False
    missing_files: list[str] = []
    # Runtime controllers (e.g. a setInterval carousel) live in sibling modules
    # the mapped component never imports. Fold in any controller that targets THIS
    # section's source class AND assigns image srcs — scoped per section so it
    # cannot mask a genuinely-missing asset in a different section.
    controller_text = controller_evidence_for_section(
        section_source_classes.get(section_index, set())
    )
    for file_value in files:
        path = component_path(file_value)
        if not path.is_file():
            missing_files.append(file_value)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing_files.append(file_value)
            continue
        evidence_text = strip_comments(
            text
            + "\n"
            + imported_named_export_text(path.resolve(), text)
            + "\n"
            + controller_text
        )
        if any(needle in evidence_text for needle in needles):
            section_ok = True
            break
    # Mapped file unreadable/missing but a section-scoped controller references
    # the asset → still valid placement evidence.
    if not section_ok and controller_text and any(
        needle in strip_comments(controller_text) for needle in needles
    ):
        section_ok = True
    if not section_ok:
        missing.append(
            {
                "src": src,
                "sectionIndex": section_index,
                "componentFile": files[0] if len(files) == 1 else files,
                "missingComponentFiles": missing_files,
                "needles": needles,
                "reason": "asset not referenced by the component mapped to its ref section",
            }
        )

status = "fail" if missing else ("pass" if checked else "skip")
payload = {
    "schemaVersion": 1,
    "status": status,
    "checked": checked,
    "skipped": skipped,
    "missingPlacements": missing,
    "implRoot": str(impl_root),
    "implSrcDir": str(impl_src),
    "rule": "visible-images.json assets with section coordinates must be referenced by the generated component mapped to that section, not merely somewhere in impl/src.",
}
if status == "skip":
    payload["reason"] = "no section-mappable visible assets"
write(payload, 1 if missing else 0)
