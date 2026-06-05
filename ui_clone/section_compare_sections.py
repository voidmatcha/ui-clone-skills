from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ui_clone.section_capture import safe_section_name

Section = dict[str, Any]

_MIN_VISIBLE_HEIGHT = 50
_SEMANTIC_TAGS = {"main", "section", "header", "footer", "nav", "article"}


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _tag(row: Section) -> str:
    return str(row.get("tag") or "").lower()


def _section_id(row: Section) -> str:
    value = row.get("id") or row.get("name") or ""
    return str(value).strip()


def _class_name(row: Section) -> str:
    value = row.get("className") or row.get("cls") or row.get("class") or ""
    return str(value).strip()


def _class_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"\s+", value.strip()) if len(token) >= 4}


def _height(row: Section) -> int:
    rect = row.get("rect")
    if isinstance(rect, dict):
        return _as_int(rect.get("height"))
    return _as_int(row.get("height") or row.get("h"))


def _visible(row: Section) -> bool:
    return _height(row) >= _MIN_VISIBLE_HEIGHT


def _section_map_candidate(row: Section) -> bool:
    return _tag(row) in _SEMANTIC_TAGS and _visible(row) and bool(
        _section_id(row) or _class_tokens(_class_name(row))
    )


def _identity_matches(section_map_row: Section, runtime_row: Section) -> bool:
    target_tag = _tag(section_map_row)
    runtime_tag = _tag(runtime_row)
    target_id = _section_id(section_map_row)

    if target_id:
        if _section_id(runtime_row) != target_id:
            return False
        return not target_tag or not runtime_tag or target_tag == runtime_tag

    target_tokens = _class_tokens(_class_name(section_map_row))
    if not target_tokens:
        return False
    if target_tag and runtime_tag and target_tag != runtime_tag:
        return False
    return bool(target_tokens & _class_tokens(_class_name(runtime_row)))


def _copy_with_index(row: Section, index: int) -> Section:
    copied = dict(row)
    copied["index"] = index
    return copied


def augment_impl_sections_from_section_map(
    section_map: Section,
    impl_sections: list[Section],
    semantic_candidates: list[Section],
) -> list[Section]:
    """Restore impl semantic wrappers that section-map synthesized for ref.

    `section-compare.sh` can replace ref runtime enumeration with
    section-map.json rows. When the impl runtime enumerator descends through a
    jumbo semantic wrapper, matching becomes asymmetric: ref keeps the wrapper,
    impl keeps only its children. This function appends matching impl DOM
    candidates for section-map rows that have stable identity and are missing
    from runtime impl sections.
    """
    raw_sections = section_map.get("sections")
    if not isinstance(raw_sections, list):
        return [dict(row) for row in impl_sections]

    augmented = [dict(row) for row in impl_sections]
    max_index = max((_as_int(row.get("index"), -1) for row in augmented), default=-1)
    next_index = max_index + 1

    sorted_sections = sorted(
        (row for row in raw_sections if isinstance(row, dict)),
        key=lambda row: _as_int(row.get("index"), _as_int(row.get("top") or row.get("y"))),
    )

    for section_row in sorted_sections:
        if not _section_map_candidate(section_row):
            continue
        if any(_identity_matches(section_row, row) for row in augmented):
            continue

        match = next(
            (
                row
                for row in semantic_candidates
                if _visible(row) and _identity_matches(section_row, row)
            ),
            None,
        )
        if match is None:
            continue

        augmented.append(_copy_with_index(match, next_index))
        next_index += 1

    return augmented


_GENERIC_TAG_TOKENS = {
    "section", "header", "footer", "article", "aside", "main", "nav", "figure",
}


def _make_name(item: Section, fallback_prefix: str) -> str:
    raw = item.get("id") or ""
    if not raw and item.get("className"):
        raw = str(item["className"]).split()[0]
    if not raw:
        raw = f"{fallback_prefix}-{item['index']}"
    return safe_section_name(raw, max_length=40)


def _dedup_name(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    n = f"{base}-{i}"
    used.add(n)
    return n


def _norm_key(s: Section) -> list[str]:
    raw = " ".join(str(s.get(k) or "") for k in ("id", "tag", "className"))
    tokens = [
        t for t in "".join(c if c.isalnum() else " " for c in raw.lower()).split()
        if len(t) >= 4
    ]
    return [t for t in tokens if t not in _GENERIC_TAG_TOKENS]


def _has_identity_overlap(a: Section, b: Section) -> bool:
    return bool(set(_norm_key(a)) & set(_norm_key(b)))


# Function words carry no section identity — two unrelated sections both say
# "the", "and", "of". Strip them before measuring text overlap so the signal
# reflects distinctive content words ("pyramid", "resources", "faq").
_TEXT_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "our", "with",
    "that", "this", "from", "has", "have", "was", "were", "all", "can", "will",
    "its", "his", "her", "their", "they", "them", "out", "who", "what", "when",
    "how", "why", "into", "than", "then", "now", "get", "got", "use", "used",
    "been", "being", "more", "most", "some", "any", "each", "every", "about",
    "over", "under", "also", "just", "only", "very", "such", "these", "those",
}

# Pairing requires text similarity at or above this floor. Tuned so a short,
# distinctive ref seed fully contained in a long impl section (containment 1.0)
# anchors, while incidental single-stopword overlaps (stripped above) do not.
_STRONG_TEXT_SIM = 0.4


def _text_word_set(row: Section) -> set[str]:
    """Distinctive visible-text words for a section.

    Prefers the full normalized innerText (`textWords`); falls back to the
    legacy `fingerprint` (first-100-char text OR class-derived seed) so the
    matcher still works on artifacts captured before `textWords` existed.
    """
    raw = str(row.get("textWords") or row.get("fingerprint") or "")
    normalized = "".join(c if c.isalnum() else " " for c in raw.lower())
    return {
        w for w in normalized.split()
        if len(w) >= 3 and w not in _TEXT_STOPWORDS
    }


def text_similarity(a: Section, b: Section) -> float:
    """Content similarity by what two sections SAY, not what they are named.

    Returns max(Jaccard, containment) over distinctive word sets. Containment
    (|A∩B| / min(|A|,|B|)) handles the asymmetric case where one side is a
    short label/seed and the other is the full rendered paragraph — a faithful
    clone of a CSS-Modules reference where class signatures share nothing.
    """
    wa = _text_word_set(a)
    wb = _text_word_set(b)
    if not wa or not wb:
        return 0.0
    inter = wa & wb
    if not inter:
        return 0.0
    jaccard = len(inter) / len(wa | wb)
    containment = len(inter) / min(len(wa), len(wb))
    sim = max(jaccard, containment)
    # A single shared word across two large vocabularies is weak evidence;
    # damp it unless one side is a short, focused label.
    if len(inter) == 1 and min(len(wa), len(wb)) > 4:
        sim *= 0.5
    return sim


def pair_sections(ref: list[Section], impl: list[Section]) -> list[Section]:
    """Pair ref sections to impl sections, returning a matches list.

    Pairing order, strongest signal first:
    (1) TEXT-CONTENT similarity — what a section SAYS. This runs first because
        a faithful Tailwind clone of a CSS-Modules reference shares no class
        tokens, and the class/id identity heuristics below can mis-fire on
        incidental token collisions (a ref id "pyramid" colliding with an
        unrelated impl token). Strong text agreement is the most reliable
        signal, so it claims its pairs before identity can mis-anchor them.
    (2) semantic-key identity overlap (id/class tokens), for sections with no
        distinctive text overlap.
    (3) className-exact tokens.
    (4) text similarity again with a same-tag + DOM-order fallback tiebreaker.

    This is a PAIRING signal only — the AE/dssim/structure comparison
    downstream is unchanged, so better pairing yields more accurate
    measurement, never an easier pass.
    """
    matches: list[Section] = []
    used_impl: set[int] = set()
    used_names: set[str] = set()
    preferred_impl: dict[int, int] = {}

    # ── TEXT-CONTENT pre-pass (strongest signal, runs first) ──
    # Collect every (ref, impl) candidate at or above the strong-text floor and
    # assign 1:1, globally-best-first. Ties break deterministically on index
    # proximity then index (no RNG/clock — repo scripts forbid them).
    text_paired: dict[int, float] = {}
    text_candidates: list[tuple[float, int, int, int]] = []
    for r in ref:
        for im in impl:
            if im["index"] in used_impl:
                continue
            sim = text_similarity(r, im)
            if sim >= _STRONG_TEXT_SIM:
                text_candidates.append(
                    (sim, abs(r["index"] - im["index"]), r["index"], im["index"])
                )
    text_candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3]))
    for sim, _dist, r_idx, im_idx in text_candidates:
        if r_idx in preferred_impl or im_idx in used_impl:
            continue
        preferred_impl[r_idx] = im_idx
        used_impl.add(im_idx)
        text_paired[r_idx] = sim

    semantic_key_paired: set[int] = set()
    for r in ref:
        if r["index"] in preferred_impl:
            continue
        candidates = [
            im for im in impl
            if im["index"] not in used_impl and _has_identity_overlap(r, im)
        ]
        if candidates:
            chosen = min(candidates, key=lambda im: abs(r["index"] - im["index"]))
            preferred_impl[r["index"]] = chosen["index"]
            used_impl.add(chosen["index"])
            semantic_key_paired.add(r["index"])

    def class_tokens(s: object) -> set[str]:
        return {t for t in str(s or "").split() if t and len(t) >= 4}

    for r in ref:
        if r["index"] in preferred_impl:
            continue
        r_tokens = class_tokens(r.get("className"))
        if not r_tokens:
            continue
        for im in impl:
            if im["index"] in used_impl:
                continue
            if r_tokens & class_tokens(im.get("className")):
                preferred_impl[r["index"]] = im["index"]
                used_impl.add(im["index"])
                break

    for r in ref:
        if r["index"] in preferred_impl:
            anchored = next(
                (x for x in impl if x["index"] == preferred_impl[r["index"]]), None
            )
            if anchored:
                name = _dedup_name(_make_name(r, "section"), used_names)
                if r["index"] in semantic_key_paired:
                    pairing_kind = "semantic-key"
                    score_val: float = 1.0
                elif r["index"] in text_paired:
                    pairing_kind = "text-content"
                    score_val = round(text_paired[r["index"]], 3)
                else:
                    pairing_kind = "className-exact"
                    score_val = 1.0
                matches.append({
                    "name": name,
                    "score": score_val,
                    "ref": r,
                    "impl": anchored,
                    "pairing": pairing_kind,
                })
                continue

        # Fallback: no identity or strong-text anchor. Text similarity is the
        # primary signal; same-tag + DOM order survive only as a final
        # tiebreaker (the +0.1 nudge cannot outweigh any real text overlap).
        best_score = 0.0
        best_impl: Section | None = None
        for im in impl:
            if im["index"] in used_impl:
                continue
            score = text_similarity(r, im)
            if r.get("tag") == im.get("tag"):
                score += 0.1
            if score > best_score:
                best_score = score
                best_impl = im

        if best_impl and best_score > 0.05:
            used_impl.add(best_impl["index"])
            name = _dedup_name(_make_name(r, "section"), used_names)
            is_wrapper = (
                not str(r.get("fingerprint", "")).strip()
                and _as_int(r.get("childCount")) <= 1
            )
            entry: Section = {
                "name": name,
                "score": round(best_score, 3),
                "ref": r,
                "impl": best_impl,
            }
            if is_wrapper:
                entry["wrapper"] = True
            matches.append(entry)
        else:
            name = _dedup_name(_make_name(r, "section"), used_names)
            matches.append({
                "name": name,
                "score": 0,
                "ref": r,
                "impl": None,
                "status": "UNMATCHED",
            })

    for im in impl:
        if im["index"] not in used_impl:
            name = _dedup_name(_make_name(im, "impl-section"), used_names)
            matches.append({
                "name": name,
                "score": 0,
                "ref": None,
                "impl": im,
                "status": "EXTRA_IN_IMPL",
            })

    return matches


def find_large_extra_sections(
    matches: list[object], floor_px: int
) -> list[tuple[str, int]]:
    """Fix 94 (A3) — impl sections that paired with NO ref (EXTRA_IN_IMPL) and
    render at least floor_px tall. A faithful clone has ~0 of these; a duplicated
    or misplaced impl block (a hero re-rendered at the page bottom, or a
    dedup-renamed "-2" section) surfaces here. Bounded structural-health signal,
    NOT a general order/structural diff.
    """
    out: list[tuple[str, int]] = []
    for x in matches:
        if not isinstance(x, dict):
            continue
        if x.get("status") != "EXTRA_IN_IMPL" or x.get("ref"):
            continue
        im_raw = x.get("impl")
        im = im_raw if isinstance(im_raw, dict) else {}
        rect_raw = im.get("rect")
        rect = rect_raw if isinstance(rect_raw, dict) else {}
        try:
            h = int(rect.get("height") or 0)
        except (TypeError, ValueError):
            h = 0
        if h >= floor_px:
            out.append((str(x.get("name", "?")), h))
    return out


def _load_json(path: Path) -> object | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _cmd_augment_impl(args: argparse.Namespace) -> int:
    section_map_raw = _load_json(Path(args.section_map))
    impl_sections_raw = _load_json(Path(args.impl_sections))
    candidates_raw = _load_json(Path(args.semantic_candidates))

    if not isinstance(section_map_raw, dict):
        return 0
    if not isinstance(impl_sections_raw, list) or not isinstance(candidates_raw, list):
        return 0

    augmented = augment_impl_sections_from_section_map(
        section_map_raw,
        [row for row in impl_sections_raw if isinstance(row, dict)],
        [row for row in candidates_raw if isinstance(row, dict)],
    )
    Path(args.impl_sections).write_text(json.dumps(augmented, indent=2), encoding="utf-8")
    return 0


def _cmd_pair(args: argparse.Namespace) -> int:
    ref_raw = _load_json(Path(args.ref_sections))
    impl_raw = _load_json(Path(args.impl_sections))
    if not isinstance(ref_raw, list) or not isinstance(impl_raw, list):
        return 1

    ref = [row for row in ref_raw if isinstance(row, dict)]
    impl = [row for row in impl_raw if isinstance(row, dict)]
    matches = pair_sections(ref, impl)
    Path(args.out).write_text(json.dumps(matches, indent=2), encoding="utf-8")

    matched = len([m for m in matches if m.get("impl")])
    unmatched_ref = len([m for m in matches if not m.get("impl")])
    extra_impl = len([m for m in matches if not m.get("ref")])
    print(f"  {matched} matched, {unmatched_ref} unmatched ref, {extra_impl} extra impl")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    augment = sub.add_parser("augment-impl")
    augment.add_argument("section_map")
    augment.add_argument("impl_sections")
    augment.add_argument("semantic_candidates")
    augment.set_defaults(func=_cmd_augment_impl)

    pair = sub.add_parser("pair")
    pair.add_argument("ref_sections")
    pair.add_argument("impl_sections")
    pair.add_argument("out")
    pair.set_defaults(func=_cmd_pair)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
