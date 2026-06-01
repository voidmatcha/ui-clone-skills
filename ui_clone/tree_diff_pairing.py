"""Content + structure based element pairing for tree-diff.sh.

tree-diff walks every visible element on the impl page and must pair each one
with its true counterpart on the ref page before running the per-element
computed-style diff. The original pairing used screen coordinates
(`elementFromPoint` at the impl element's center). That breaks the moment the
clone-in-progress has a different layout/height than the ref — the element at
the same screen Y is a different element, so pairs are wrong and most elements
go unpaired. This contradicts the skill's own "anchor to content, not the
y-coordinate" principle.

This module pairs by, strongest signal first:
  1. TEXT — distinctive visible text (normalized, stopword-stripped, Jaccard /
     containment), regardless of screen position.
  2. STRUCTURE — for text-less elements (wrappers/images/svg): tag + role +
     src/alt + box size/aspect + DOM structural-path similarity.
  3. COORDINATE — a final tiebreaker only, and SECTION/SCROLL-RELATIVE
     (each side's Y normalized by that side's own content height), never an
     absolute page Y across differently-tall pages.

Pairing is 1:1 and deterministic (no RNG / clock — repo scripts forbid them).
Genuinely-absent elements are left unpaired rather than force-paired to noise.

This is a PAIRING signal only. The per-pair computed-style diff, the severity
classification, and every threshold in tree-diff.sh are unchanged — better
pairing yields more accurate deltas, never an easier pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

Element = dict[str, Any]

# Function words carry no identity — strip before measuring text overlap so the
# signal reflects distinctive content words. Mirrors section_compare_sections.
_TEXT_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "our", "with",
    "that", "this", "from", "has", "have", "was", "were", "all", "can", "will",
    "its", "his", "her", "their", "they", "them", "out", "who", "what", "when",
    "how", "why", "into", "than", "then", "now", "get", "got", "use", "used",
    "been", "being", "more", "most", "some", "any", "each", "every", "about",
    "over", "under", "also", "just", "only", "very", "such", "these", "those",
}

# Pairing requires text similarity at or above this floor. Tuned so a short,
# distinctive label fully contained in a longer paragraph (containment 1.0)
# anchors, while incidental single-stopword overlaps do not.
_STRONG_TEXT_SIM = 0.5

# Structural-pairing floor for text-less elements. Combines tag/role/src/alt/
# box/path agreement. Below this we leave the element for the coordinate
# tiebreaker or unpaired rather than force a weak structural pair.
_STRONG_STRUCT = 0.5

# Coordinate tiebreaker only fires when section-relative position is very close
# AND tags agree. It is a last resort, never a primary signal.
_REL_POS_MAX = 0.06


def _text_words(el: Element) -> set[str]:
    raw = str(el.get("txt") or "")
    normalized = "".join(c if c.isalnum() else " " for c in raw.lower())
    return {
        w for w in normalized.split()
        if len(w) >= 3 and w not in _TEXT_STOPWORDS
    }


def _text_scores(a_words: set[str], b_words: set[str]) -> tuple[float, float]:
    """Return (sim, jaccard).

    sim = max(Jaccard, containment) — containment handles the asymmetric case
    where one side is a short label and the other a longer rendered string. A
    single shared word across two large vocabularies is weak evidence, so damp
    it. jaccard is returned separately so the caller can break sim-ties toward
    the most word-EQUAL candidate — that prefers a true leaf↔leaf pair over a
    leaf↔container pair (the container merely *contains* the leaf's text, so its
    containment also hits 1.0 but its Jaccard is lower).
    """
    if not a_words or not b_words:
        return 0.0, 0.0
    inter = a_words & b_words
    if not inter:
        return 0.0, 0.0
    jaccard = len(inter) / len(a_words | b_words)
    containment = len(inter) / min(len(a_words), len(b_words))
    sim = max(jaccard, containment)
    if len(inter) == 1 and min(len(a_words), len(b_words)) > 4:
        sim *= 0.5
        jaccard *= 0.5
    return sim, jaccard


def text_similarity(a_words: set[str], b_words: set[str]) -> float:
    """max(Jaccard, containment) over distinctive word sets."""
    return _text_scores(a_words, b_words)[0]


def _num(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _basename(src: object) -> str:
    s = str(src or "").split("?")[0].split("#")[0].rstrip("/")
    return s.rsplit("/", 1)[-1].lower()


def _path_tokens(el: Element) -> list[str]:
    return [t for t in str(el.get("path") or "").split(">") if t]


def _path_similarity(a: Element, b: Element) -> float:
    """Suffix overlap of the two DOM structural paths (nth-of-type chains).

    Shared trailing path segments mean the elements occupy structurally
    equivalent positions in their subtrees.
    """
    pa, pb = _path_tokens(a), _path_tokens(b)
    if not pa or not pb:
        return 0.0
    n = 0
    for ta, tb in zip(reversed(pa), reversed(pb)):
        if ta != tb:
            break
        n += 1
    return n / max(len(pa), len(pb))


def _box_similarity(a: Element, b: Element) -> float:
    """Size + aspect-ratio agreement in [0, 1]."""
    aw, ah = _num(a.get("w")), _num(a.get("h"))
    bw, bh = _num(b.get("w")), _num(b.get("h"))
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    size = min(aw, bw) / max(aw, bw) * (min(ah, bh) / max(ah, bh))
    ar_a, ar_b = aw / ah, bw / bh
    aspect = min(ar_a, ar_b) / max(ar_a, ar_b)
    return (size + aspect) / 2


def _structural_score(a: Element, b: Element) -> float:
    """Identity score for text-less elements: tag + role + src/alt + box + path."""
    score = 0.0
    if str(a.get("tag") or "").upper() == str(b.get("tag") or "").upper():
        score += 0.35
    a_role, b_role = str(a.get("role") or ""), str(b.get("role") or "")
    if a_role and a_role == b_role:
        score += 0.1
    a_src, b_src = _basename(a.get("src")), _basename(b.get("src"))
    if a_src and a_src == b_src:
        score += 0.4
    a_alt, b_alt = str(a.get("alt") or "").strip().lower(), str(b.get("alt") or "").strip().lower()
    if a_alt and a_alt == b_alt:
        score += 0.2
    score += 0.3 * _path_similarity(a, b)
    score += 0.3 * _box_similarity(a, b)
    return score


def _rel_y(el: Element, max_bottom: float) -> float:
    if max_bottom <= 0:
        return 0.0
    return (_num(el.get("top")) + _num(el.get("h")) / 2) / max_bottom


def _max_bottom(els: list[Element]) -> float:
    return max((_num(e.get("top")) + _num(e.get("h")) for e in els), default=1.0) or 1.0


def _paired_record(impl_index: int, ref_el: Element, kind: str) -> Element:
    rec = dict(ref_el)
    rec["i"] = impl_index
    rec["pairKind"] = kind
    return rec


def pair_elements(impl: list[Element], ref: list[Element]) -> list[Element]:
    """Pair each impl element to its ref counterpart by content + structure.

    Returns one record per impl element, in impl order, in the same shape
    tree-diff.sh's downstream diff already consumes: a copy of the matched ref
    element's fields tagged with ``i`` = the impl index (plus ``pairKind`` for
    transparency), or ``{"i": idx, "miss": True}`` when no counterpart is found.
    """
    n = len(impl)
    matched_ref: dict[int, int] = {}   # impl_index -> ref_index
    used_ref: set[int] = set()

    impl_words = [_text_words(e) for e in impl]
    ref_words = [_text_words(e) for e in ref]
    impl_max_bottom = _max_bottom(impl)
    ref_max_bottom = _max_bottom(ref)

    # ── Stage 1: TEXT (strongest). Global-best-first 1:1 assignment. Ties on
    #    sim break toward higher Jaccard (most word-equal → true leaf↔leaf, not
    #    leaf↔container), then section-relative position, then index. Fully
    #    deterministic — no RNG / clock. ──
    text_candidates: list[tuple[float, float, float, int, int]] = []
    for ii in range(n):
        if not impl_words[ii]:
            continue
        rel_i = _rel_y(impl[ii], impl_max_bottom)
        for ri in range(len(ref)):
            if not ref_words[ri]:
                continue
            sim, jac = _text_scores(impl_words[ii], ref_words[ri])
            if sim >= _STRONG_TEXT_SIM:
                rel_dist = abs(rel_i - _rel_y(ref[ri], ref_max_bottom))
                text_candidates.append((sim, jac, rel_dist, ii, ri))
    text_candidates.sort(key=lambda t: (-t[0], -t[1], t[2], t[3], t[4]))
    for _sim, _jac, _rd, ii, ri in text_candidates:
        if ii in matched_ref or ri in used_ref:
            continue
        matched_ref[ii] = ri
        used_ref.add(ri)

    # ── Stage 2: STRUCTURE — for TEXT-LESS impl elements only (wrappers /
    #    images / svg) paired by tag/role/src/alt/box/path identity. A
    #    text-bearing element that found no text match has no reliable
    #    structural counterpart, so it is NOT force-paired here — it falls
    #    through to "unpaired", which is the honest outcome. ──
    struct_candidates: list[tuple[float, float, int, int]] = []
    for ii in range(n):
        if ii in matched_ref or impl_words[ii]:
            continue
        rel_i = _rel_y(impl[ii], impl_max_bottom)
        for ri in range(len(ref)):
            if ri in used_ref or ref_words[ri]:
                continue
            score = _structural_score(impl[ii], ref[ri])
            if score >= _STRONG_STRUCT:
                rel_dist = abs(rel_i - _rel_y(ref[ri], ref_max_bottom))
                struct_candidates.append((score, rel_dist, ii, ri))
    struct_candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3]))
    for _score, _rd, ii, ri in struct_candidates:
        if ii in matched_ref or ri in used_ref:
            continue
        matched_ref[ii] = ri
        used_ref.add(ri)

    # ── Stage 3: COORDINATE tiebreaker — section/scroll-relative only, same-tag,
    #    and (like structure) only for TEXT-LESS elements still ambiguous after
    #    structural identity. Last resort; never pairs text-bearing elements. ──
    coord_candidates: list[tuple[float, int, int]] = []
    for ii in range(n):
        if ii in matched_ref or impl_words[ii]:
            continue
        rel_i = _rel_y(impl[ii], impl_max_bottom)
        i_tag = str(impl[ii].get("tag") or "").upper()
        for ri in range(len(ref)):
            if ri in used_ref or ref_words[ri]:
                continue
            if str(ref[ri].get("tag") or "").upper() != i_tag:
                continue
            rel_dist = abs(rel_i - _rel_y(ref[ri], ref_max_bottom))
            if rel_dist <= _REL_POS_MAX:
                coord_candidates.append((rel_dist, ii, ri))
    coord_candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    for _rd, ii, ri in coord_candidates:
        if ii in matched_ref or ri in used_ref:
            continue
        matched_ref[ii] = ri
        used_ref.add(ri)

    # ── Build output in impl order. Unmatched → explicit miss. ──
    out: list[Element] = []
    for ii in range(n):
        ref_idx = matched_ref.get(ii)
        if ref_idx is None:
            out.append({"i": ii, "miss": True})
            continue
        kind = (
            "text" if any(c[3] == ii and c[4] == ref_idx for c in text_candidates)
            else "structure" if any(c[2] == ii and c[3] == ref_idx for c in struct_candidates)
            else "coordinate"
        )
        out.append(_paired_record(ii, ref[ref_idx], kind))
    return out


def _parse(path: str) -> list[Element]:
    with open(path) as f:
        raw = f.read().strip()
    # agent-browser wraps string eval results in an extra JSON quote layer.
    if raw.startswith('"') and raw.endswith('"'):
        data = json.loads(json.loads(raw))
    else:
        data = json.loads(raw)
    return [row for row in data if isinstance(row, dict)]


def _cmd_pair(args: argparse.Namespace) -> int:
    impl = _parse(args.impl)
    ref = _parse(args.ref)
    out = pair_elements(impl, ref)
    Path(args.out).write_text(json.dumps(out), encoding="utf-8")
    paired = len([r for r in out if not r.get("miss")])
    print(f"  paired {paired}/{len(impl)} impl elements (content+structure)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair")
    pair.add_argument("impl")
    pair.add_argument("ref")
    pair.add_argument("out")
    pair.set_defaults(func=_cmd_pair)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
