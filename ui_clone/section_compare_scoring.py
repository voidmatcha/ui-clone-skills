"""Section naming, text/identity similarity, and anchor scoring (section-compare).

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

from ui_clone.section_capture import safe_section_name
from ui_clone.section_compare_common import (
    _GENERIC_IDENTITY_ANCHOR_TOKENS,
    Section,
    _as_int,
    _class_name,
    _class_tokens,
    _lockable_class_tokens,
    _section_id,
    _top_distance,
)

_GENERIC_TAG_TOKENS = {
    "section", "header", "footer", "article", "aside", "main", "nav", "figure",
}


def _make_name(item: Section, fallback_prefix: str) -> str:
    raw = item.get("captureName") or item.get("id") or ""
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


def _rect_size_sim(a: Section, b: Section) -> float:
    """Scale-robust similarity of two section boxes by width+height ratio.

    Absolute coordinates drift between ref/impl (different scroll phase), so we
    compare SIZE (min/max ratio per dimension), not position — a same-shaped
    footer scores ~1.0 regardless of where each side captured it.
    """
    ra = a.get("rect") if isinstance(a.get("rect"), dict) else {}
    rb = b.get("rect") if isinstance(b.get("rect"), dict) else {}

    def _dim(rect: object, key: str) -> float:
        if not isinstance(rect, dict):
            return 0.0
        try:
            return float(rect.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    sims: list[float] = []
    for key in ("width", "height"):
        x, y = _dim(ra, key), _dim(rb, key)
        hi = max(x, y)
        sims.append((min(x, y) / hi) if hi > 0 else 1.0)
    return sum(sims) / len(sims)


def _identity_pair_score(r: Section, im: Section) -> float:
    """Composite identity-pairing score.

    A single shared id/class token (e.g. a generic "footer" id on two distinct
    sections) cannot disambiguate which impl a ref pairs to. Blend id-exact +
    class-token Jaccard (weighted — the class signature is the decider) + rect
    SIZE similarity + DOM-order proximity so the correct same-id section wins
    over a nearest-by-index blank. Pairing only; the AE/dssim/structure compare
    downstream is unchanged, so better pairing yields more accurate measurement,
    never an easier pass.
    """
    score = 0.0
    rid, iid = _section_id(r), _section_id(im)
    if rid and rid == iid:
        score += 1.0
    rt, it = _class_tokens(_class_name(r)), _class_tokens(_class_name(im))
    if rt and it:
        inter = rt & it
        if inter:
            score += 2.0 * (len(inter) / len(rt | it))
    score += _rect_size_sim(r, im)
    # Order proximity: prefer the same-identity candidate that sits at the same
    # place. Use document position (viewport-scaled) when both sides have a
    # rect — exact in self-pass, order-preserving in a faithful clone — and fall
    # back to DOM-index distance only when a rect is missing. This is what
    # disambiguates two id="footer" sections with distinct class signatures that
    # share the generic id token; it never outweighs the id-exact (+1.0) or
    # class-signature (+2.0) terms above.
    td = _top_distance(r, im)
    if td != float("inf"):
        score += 0.25 / (1.0 + td / 800.0)
    else:
        score += 0.25 / (1.0 + abs(_as_int(r.get("index")) - _as_int(im.get("index"))))
    return score


# ── Anchor-first / Y-order-stable pairing (rank-3) ─────────────────────────
# A faithful clone of a CSS-modules reference reuses some of the ref's compiled
# class names on a few sections (hero / stats / footer) while rendering the rest
# with empty className/id (Tailwind). The verified geometry is monotonic in Y:
# impl.top = ref.top + a small, growing global drift. The global, order-blind
# text/identity pairing below RESHUFFLES on any section-count change — a ref with
# an incidental text/token collision steals the impl that belongs to its
# Y-neighbour, leaving a real section MISSING and fabricating an EXTRA twin.
#
# Phase A locks the high-confidence identity anchors that ALSO preserve Y-order;
# Phase B fills the gaps between locked anchors by monotonic Y-order so each ref
# pairs to the impl that sits in the same Y-band; only genuine orphans fall back
# to the text/identity heuristic (Phase C, the legacy stages). This is a PAIRING
# signal only — the AE/dssim/structure compare downstream is unchanged, so a more
# position-consistent pairing yields more accurate measurement, never an easier
# pass. NO-OP-equivalent in ref-vs-ref self-pass: identity is present on every
# section, Y-order is perfect, drift ~0, so Phase A locks all pairs 1:1.

# An identity anchor must clear this composite score to be eligible for locking.
# id-exact alone contributes +1.0 and a full class-signature match +2.0, so the
# floor (1.5) demands more than a single shared generic token (e.g. two sections
# sharing only a "section" token score ~0.7 from a partial Jaccard and never
# lock). Tuned against one observed site: hero/stats/winning/end/cta/eatReal all clear it;
# the spurious faqs<->cta "section"-token overlap (score ~1.93 but Y-order
# inconsistent) is rejected by the monotonic-chain selection, not this floor.
_ANCHOR_SCORE_FLOOR = 1.5


def _anchor_score(r: Section, im: Section) -> float | None:
    """Identity-anchor strength for a (ref, impl) pair, or None when no identity.

    Returns the composite `_identity_pair_score` only when the pair shares a
    distinctive exact id or class token. Generic carousel/library state tokens
    are intentionally ignored here so they cannot become locked anchors; the
    later identity stage remains unchanged. PAIRING ONLY.
    """
    rid, iid = _section_id(r), _section_id(im)
    id_match = (
        bool(rid)
        and rid == iid
        and rid.lower() not in _GENERIC_IDENTITY_ANCHOR_TOKENS
    )
    r_tokens = _lockable_class_tokens(_class_name(r))
    im_tokens = _lockable_class_tokens(_class_name(im))
    tok_match = bool(r_tokens & im_tokens)
    if not id_match and not tok_match:
        return None
    return _identity_pair_score(r, im)
