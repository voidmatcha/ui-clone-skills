# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

if sys.argv[1] == "--missing-required":
    out_path = Path(sys.argv[2])
    payload = {
        "schemaVersion": 1,
        "status": "fail",
        "reason": "required-media.json absent — extractor (Step 6b-bis) has not run; required media coverage cannot be proven",
        "implRoot": sys.argv[3],
        "implDir": sys.argv[4],
        "implSrcDir": sys.argv[5],
        "implPublicDir": sys.argv[6],
        "implPkgJson": sys.argv[7],
        "missing": {"video": [], "lottie": [], "svg": []},
        "fix": "Run scripts/extract/required-media.sh <ref-dir> before required-media-coverage-check.sh, even when it emits zero required media.",
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sys.exit(0)

if sys.argv[1] == "--no-impl":
    out_path = Path(sys.argv[2])
    out_path.write_text(
        """{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "implRoot": "",
  "implDir": "",
  "implSrcDir": "",
  "implPublicDir": "",
  "implPkgJson": "",
  "missing": {"video": [], "lottie": []}
}
""",
        encoding="utf-8",
    )
    sys.exit(0)

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

required_path = ref_dir / "required-media.json"
required = json.loads(required_path.read_text(encoding="utf-8"))

impl_src_dir = next(
    (impl_root / name for name in ("src", "app", "pages") if (impl_root / name).is_dir()),
    impl_root / "src",
)
path_fields = {
    "implRoot": str(impl_root),
    "implDir": str(impl_root),
    "implSrcDir": str(impl_src_dir),
    "implPublicDir": str(impl_root / "public"),
    "implPkgJson": str(impl_root / "package.json"),
}

JsonObject = dict[str, Any]


def _as_list(value: object) -> list[JsonObject]:
    return cast(list[JsonObject], value) if isinstance(value, list) else []


videos = _as_list(required.get("videos"))
lottie_urls = _as_list(required.get("lottie"))
svg_urls = _as_list(required.get("svgs"))

# If ref has neither video, Lottie, nor SVG, this gate is a no-op.
if not videos and not lottie_urls and not svg_urls:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        **path_fields,
        "reason": "ref has no required video, Lottie, or SVG media",
        "totals": {"video": 0, "lottie": 0, "svg": 0},
        "missing": {"video": [], "lottie": [], "svg": []},
    }, indent=2) + "\n", encoding="utf-8")
    print("required-media-coverage: pass (no required media)")
    sys.exit(0)


# Build the basename → relative-path map for impl/public/ files.
public_files: dict[str, list[str]] = {}
public_dir = impl_root / "public"
if public_dir.is_dir():
    for p in public_dir.rglob("*"):
        if p.is_file():
            name = p.name.lower()
            rel = str(p.relative_to(impl_root))
            public_files.setdefault(name, []).append(rel)


# Collect impl source text for reference scanning.
SRC_EXCLUDE = {"node_modules", ".next", "dist", "build", ".turbo", ".cache"}
SRC_SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs",
                ".css", ".scss", ".html", ".vue", ".svelte", ".json"}
src_blobs: dict[str, str] = {}
for sub in ("src", "app", "pages"):
    sub_dir = impl_root / sub
    if not sub_dir.is_dir():
        continue
    for p in sub_dir.rglob("*"):
        if not p.is_file() or p.suffix not in SRC_SUFFIXES:
            continue
        if any(part in SRC_EXCLUDE for part in p.parts):
            continue
        try:
            src_blobs[str(p.relative_to(impl_root))] = p.read_text(
                encoding="utf-8", errors="ignore",
            )
        except OSError:
            continue


# --- Load-bearing reference classifier -------------------------------------
#
# A raw `needle in blob` substring match credits any mention of the asset path,
# so a dead `void LOTTIE.outroPc` expression statement or a
# `// referenced so required-media-coverage passes` comment satisfies coverage
# without the asset ever being wired into runtime. This classifier only credits
# a media path when the reference is LOAD-BEARING:
#
#   * DIRECT — the path literal sits in a JSX/HTML src attr, an import / url() /
#     new URL() / fetch() / require() argument, a lottie loadAnimation({path})
#     call, or an imperative `.src =` / setAttribute('src', ...); OR
#   * BINDING — the path is assigned to a named binding (a const-map entry like
#     `intro: '/img/x.json'` or `const HERO = '/x.mp4'`) AND that binding is
#     itself passed to a real call / JSX attribute somewhere in the same module.
#
# A comment, a bare `void x;` expression statement, or a const the module never
# consumes does NOT count. We lex once so string / comment spans can never
# masquerade as code, and we prefer a false-uncovered over a false-covered.

_K_CODE = "c"
_K_STR = "s"
_K_COMMENT = "/"


def lex_kinds(text: str) -> str:
    """Label every character 'c' (code), 's' (string), or '/' (comment).
    Handles ' " ` literals with backslash escapes, // and /* */ comments."""
    n = len(text)
    kinds = bytearray(n)
    code = ord(_K_CODE)
    string = ord(_K_STR)
    comment = ord(_K_COMMENT)
    i = 0
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


def code_only_view(text: str, kinds: str) -> str:
    """Comment and string spans -> spaces (newlines preserved for line math);
    code kept verbatim. Used for structural scans (brace/paren matching,
    assignment + callee detection) so string / comment content is inert."""
    out = []
    for ch, k in zip(text, kinds):
        if k == _K_CODE:
            out.append(ch)
        else:
            out.append("\n" if ch == "\n" else " ")
    return "".join(out)


# Tokens that make the path literal's own line/context a runtime wiring site.
_LOAD_BEARING_DIRECT = re.compile(
    r"loadAnimation\s*\(|<\s*(?:video|source|img|image|lottie|player)\b"
    r"|\bsrc\s*=|\bsrc\s*:|\bposter\s*[:=]|\bpath\s*:|\bhref\s*[:=]"
    r"|new\s+URL\s*\(|\bfetch\s*\(|\brequire\s*\(|\bimport\b|\burl\s*\("
    r"|\.src\s*=|\bsetAttribute\s*\(|\bpreload\s*\(",
    re.IGNORECASE,
)

_VAR_DECL_ASSIGN = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*$")
_OBJ_KEY_ASSIGN = re.compile(r"([A-Za-z_$][\w$]*)\s*:\s*$")
_PLAIN_ASSIGN = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*$")

# Identifiers before `(` that are NOT real consumers — a member reference
# inside `void(...)` / a control-flow head is not "passed to a call".
_NON_CALL_CALLEES = {
    "void", "return", "if", "for", "while", "switch", "typeof", "delete",
    "await", "yield", "catch", "super", "do", "else", "in", "of", "case",
}


def _enclosing_bracket(code: str, pos: int) -> tuple[str, int]:
    """Return (bracket_char, index) of the innermost still-open bracket to the
    left of `pos`, or ("", -1) at top level. A single depth counter across
    ()[]{} — enough to tell a call argument from an object/array literal."""
    depth = 0
    i = pos - 1
    while i >= 0:
        c = code[i]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                return c, i
            depth -= 1
        i -= 1
    return "", -1


def _callee_before(code: str, paren_idx: int) -> str:
    """The identifier immediately preceding a `(` (the call target)."""
    j = paren_idx - 1
    while j >= 0 and code[j] in " \t\n":
        j -= 1
    end = j + 1
    while j >= 0 and (code[j].isalnum() or code[j] in "_$"):
        j -= 1
    return code[j + 1:end]


def _binding_has_consuming_use(code: str, binding: str) -> bool:
    """True when `binding` appears (as a whole token, at a code position) as an
    argument of a real call `f(...binding...)` or a JSX attribute value
    `attr={...binding...}` — excluding its own declaration site and bare
    `void binding` / `binding;` expression statements. Module-scoped: the
    consuming use must live in the same file as the assignment."""
    if len(binding) < 3:
        return False
    for m in re.finditer(r"\b" + re.escape(binding) + r"\b", code):
        idx = m.start()
        # Skip the declaration / assignment-target site itself: `binding :`
        # (object key) or `binding =` (decl / reassignment). A single `=` that
        # is really `==`/`=>` is a comparison/arrow, not a target — allow it.
        after = code[m.end():m.end() + 8]
        if re.match(r"\s*:", after) or re.match(r"\s*=(?![=>])", after):
            continue
        bracket, bidx = _enclosing_bracket(code, idx)
        if bracket == "(":
            callee = _callee_before(code, bidx)
            if callee and callee not in _NON_CALL_CALLEES:
                return True
        elif bracket == "{":
            # JSX expression container bound to an attribute: `attr={ binding }`.
            if code[:bidx].rstrip().endswith("="):
                return True
    return False


def classify_needle_in_file(
    text: str, kinds: str, code: str, needle: str,
) -> tuple[str, str, str] | None:
    """Classify the strongest occurrence of `needle` in one file.

    Returns (verdict, reason, binding) where verdict is:
      * "covered" — a direct load-bearing site (binding == "");
      * "assign"  — the path is assigned to `binding` (needs a 2nd-pass
                    consuming-use check);
      * "reject"  — comment-only / bare non-load-bearing expression.
    Returns None when `needle` does not occur in the file."""
    best: tuple[str, str, str] | None = None
    start = 0
    while True:
        p = text.find(needle, start)
        if p == -1:
            break
        start = p + len(needle)
        if kinds[p] == _K_COMMENT:
            best = best or ("reject", "only-in-comment", "")
            continue
        line_start = text.rfind("\n", 0, p) + 1
        prev_start = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
        line_end = text.find("\n", p)
        line_end = len(text) if line_end == -1 else line_end
        # Window = the path literal's line plus one line of lookback, so a
        # `src={` / `<video` / `loadAnimation({` opener on the line above still
        # counts as direct wiring.
        if _LOAD_BEARING_DIRECT.search(code[prev_start:line_end]):
            return ("covered", "direct-load-bearing", "")
        # Assignment to a named binding? Inspect the code before the literal
        # (two-line lookback handles `const X =\n  '/path'`).
        prefix = code[prev_start:p].rstrip()
        binding = ""
        mv = _VAR_DECL_ASSIGN.search(prefix)
        if mv:
            binding = mv.group(1)
        else:
            mo = _OBJ_KEY_ASSIGN.search(prefix)
            if mo:
                binding = mo.group(1)
            else:
                mp = _PLAIN_ASSIGN.search(prefix)
                if mp:
                    binding = mp.group(1).split(".")[-1]
        if binding:
            best = ("assign", "assigned-to-binding", binding)
            continue
        best = best or ("reject", "not-load-bearing-expression", "")
    return best


def url_basename(u: str) -> str:
    parsed = urlparse(u)
    path = parsed.path or u
    name = path.rstrip("/").split("/")[-1].split("?")[0]
    return name.lower()


def is_in_public(basename: str) -> list[str]:
    return public_files.get(basename, [])


# Lex every source file once: rel -> (kinds, code-only view).
src_lexed: dict[str, tuple[str, str]] = {}
for _rel, _txt in src_blobs.items():
    _kinds = lex_kinds(_txt)
    src_lexed[_rel] = (_kinds, code_only_view(_txt, _kinds))


def is_referenced_in_src(needles: list[str]) -> tuple[bool, str | None, str]:
    """(covered, referencing_file, reason). A needle is covered when it appears
    at a direct load-bearing site, or is assigned to a binding that is actually
    consumed by a call / JSX attribute. Comment-only, void, and dead-const
    references are rejected — with the reason surfaced per asset."""
    assign_candidates: list[tuple[str, str, str]] = []  # (rel, code, binding)
    reject_file: str | None = None
    reject_reason = "not-found"
    for rel, (kinds, code) in src_lexed.items():
        text = src_blobs[rel]
        for needle in needles:
            if not needle:
                continue
            res = classify_needle_in_file(text, kinds, code, needle)
            if res is None:
                continue
            verdict, reason, binding = res
            if verdict == "covered":
                return True, rel, reason
            if verdict == "assign":
                assign_candidates.append((rel, code, binding))
            else:
                reject_file, reject_reason = rel, reason
    # 2nd pass: an assigned path counts only if its binding is consumed by a
    # real call / JSX attribute in the same module.
    for rel, code, binding in assign_candidates:
        if _binding_has_consuming_use(code, binding):
            return True, rel, f"binding-consumed:{binding}"
    if assign_candidates:
        bindings = ",".join(sorted({b for _, _, b in assign_candidates}))
        return False, assign_candidates[0][0], f"binding-unused:{bindings}"
    return False, reject_file, reject_reason


missing_videos: list[JsonObject] = []
for v in videos:
    src = v.get("src", "")
    if not src:
        continue
    basename = url_basename(src)
    public_hits = is_in_public(basename)
    # Build needle list — basename plus a normalized impl path
    # (everything Vite/Next would emit when import-pathed: /<sub>/name).
    needles = [basename, src.rsplit("/", 1)[-1].split("?")[0]]
    for hit in public_hits:
        # Reference can be either the basename or a path starting at
        # /<sub>/... (the public-served path).
        needles.append("/" + hit.split("public/", 1)[-1])
    needles = list({n for n in needles if n})
    ref_ok, ref_file, ref_reason = is_referenced_in_src(needles)
    if not public_hits or not ref_ok:
        missing_videos.append({
            "section": v.get("section"),
            "src": src,
            "basename": basename,
            "publicHit": public_hits[0] if public_hits else None,
            "referencedIn": ref_file,
            "refRejectReason": ref_reason,
            "kind": (
                "missing-from-public" if not public_hits
                else "not-referenced-in-src"
            ),
        })


missing_lottie: list[JsonObject] = []
for lottie_entry in lottie_urls:
    path = lottie_entry.get("path", "")
    if not path:
        continue
    basename = url_basename(path)
    public_hits = is_in_public(basename)
    needles = [basename, path.rsplit("/", 1)[-1].split("?")[0]]
    for hit in public_hits:
        needles.append("/" + hit.split("public/", 1)[-1])
    needles = list({n for n in needles if n})
    ref_ok, ref_file, ref_reason = is_referenced_in_src(needles)
    if not public_hits or not ref_ok:
        missing_lottie.append({
            "path": path,
            "basename": basename,
            "evidenceFile": lottie_entry.get("evidenceFile"),
            "publicHit": public_hits[0] if public_hits else None,
            "referencedIn": ref_file,
            "refRejectReason": ref_reason,
            "kind": (
                "missing-from-public" if not public_hits
                else "not-referenced-in-src"
            ),
        })


# Detect Lottie runtime package — even if URLs match, missing the
# runtime means the .json files just sit on disk. Reuse the
# lottie-runtime-check semantics minimally: parse impl/package.json.
lottie_pkg_ok = True
if lottie_urls:
    pkg_json = impl_root / "package.json"
    if pkg_json.is_file():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            all_deps: dict[str, str] = {}
            for k in ("dependencies", "devDependencies"):
                d = pkg_data.get(k) or {}
                if isinstance(d, dict):
                    all_deps.update({kk: str(vv) for kk, vv in d.items()})
            lottie_pkgs = {
                "lottie-web", "lottie-react", "@lottiefiles/react-lottie-player",
                "@lottiefiles/lottie-player", "@dotlottie/react-player",
                "@lottiefiles/dotlottie-react", "bodymovin",
            }
            lottie_pkg_ok = any(p in all_deps for p in lottie_pkgs)
        except (OSError, ValueError):
            lottie_pkg_ok = False
    else:
        lottie_pkg_ok = False


# SVG coverage — same transfer + reference check as video / Lottie.
missing_svgs: list[JsonObject] = []
for s in svg_urls:
    src = s.get("src", "")
    if not src or src.startswith("data:"):
        continue
    basename = url_basename(src)
    public_hits = is_in_public(basename)
    needles = [basename, src.rsplit("/", 1)[-1].split("?")[0]]
    for hit in public_hits:
        needles.append("/" + hit.split("public/", 1)[-1])
    needles = list({n for n in needles if n})
    ref_ok, ref_file, ref_reason = is_referenced_in_src(needles)
    if not public_hits or not ref_ok:
        missing_svgs.append({
            "section": s.get("section"),
            "src": src,
            "basename": basename,
            "kind_origin": s.get("kind"),
            "evidenceFile": s.get("evidenceFile"),
            "publicHit": public_hits[0] if public_hits else None,
            "referencedIn": ref_file,
            "refRejectReason": ref_reason,
            "kind": (
                "missing-from-public" if not public_hits
                else "not-referenced-in-src"
            ),
        })


total_missing = (
    len(missing_videos) + len(missing_lottie) + len(missing_svgs)
)
runtime_missing = (lottie_urls and not lottie_pkg_ok)
status = "fail" if (total_missing or runtime_missing) else "pass"

result = {
    "schemaVersion": 1,
    "status": status,
    **path_fields,
    "totals": {
        "videoRequired": len(videos),
        "lottieRequired": len(lottie_urls),
        "svgRequired": len(svg_urls),
        "videoMissing": len(missing_videos),
        "lottieMissing": len(missing_lottie),
        "svgMissing": len(missing_svgs),
    },
    "lottieRuntimePackageInstalled": lottie_pkg_ok,
    "missing": {
        "video": missing_videos[:30],
        "lottie": missing_lottie[:30],
        "svg": missing_svgs[:30],
    },
    "rule": (
        "Every entry in required-media.json (videos from html/*.json + "
        "Lottie paths from bundles/*.js + SVG URLs from <img>/<use>/CSS "
        "url(...svg) captures) must be transferred to impl/public/ AND "
        "referenced in impl source. If ref has Lottie URLs, "
        "impl/package.json must declare a Lottie runtime "
        "(lottie-web / lottie-react / @lottiefiles/* / etc)."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"required-media-coverage: "
    f"video {len(videos) - len(missing_videos)}/{len(videos)}, "
    f"lottie {len(lottie_urls) - len(missing_lottie)}/{len(lottie_urls)}, "
    f"svg {len(svg_urls) - len(missing_svgs)}/{len(svg_urls)}, "
    f"runtime-pkg={lottie_pkg_ok} → {status}"
)
sys.exit(0 if status == "pass" else 1)
