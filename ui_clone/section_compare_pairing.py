"""Anchor-first, Y-order-stable ref<->impl section pairing (section-compare).

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

from ui_clone.section_compare_common import (
    Section,
    _as_int,
    _class_name,
    _class_tokens,
    _top,
    _top_distance,
)
from ui_clone.section_compare_scoring import (
    _ANCHOR_SCORE_FLOOR,
    _STRONG_TEXT_SIM,
    _anchor_score,
    _dedup_name,
    _has_identity_overlap,
    _identity_pair_score,
    _make_name,
    text_similarity,
)


def _lock_identity_anchors(
    ref: list[Section],
    impl: list[Section],
    eligible_ref: list[int],
    used_impl: set[int],
) -> dict[int, int]:
    """Phase A — lock a Y-monotonic chain of high-confidence identity anchors.

    Build every (ref, impl) identity-anchor candidate at or above the score
    floor, then select a subset that (1) is 1:1 and (2) is strictly increasing
    in BOTH ref document-top and impl document-top — i.e. an order-preserving
    assignment. This forbids an anchor that would cross a stronger one out of
    Y-order (the observed site's faqs<->cta "section"-token collision), so a locked
    anchor can never be stolen later nor violate the page's vertical order.

    Greedy-by-score selection with a Y-order feasibility check is deterministic
    and, on the self-pass (impl==ref), trivially locks every section to itself:
    each ref's strongest anchor is its own copy, all are Y-monotonic. Ties break
    on score then ref/impl index (no RNG/clock — repo scripts forbid them).

    Returns the locked {ref_index: impl_index} map and mutates used_impl.
    """
    ref_by_index = {r["index"]: r for r in ref}
    candidates: list[tuple[float, float, int, int]] = []
    for r_idx in eligible_ref:
        r = ref_by_index.get(r_idx)
        if r is None:
            continue
        for im in impl:
            if im["index"] in used_impl:
                continue
            score = _anchor_score(r, im)
            if score is None or score < _ANCHOR_SCORE_FLOOR:
                continue
            candidates.append((score, _top_distance(r, im), r_idx, im["index"]))

    # Strongest-first; ties on smallest top-distance, then indices (determinism).
    candidates.sort(key=lambda c: (-c[0], c[1], c[2], c[3]))

    locked: dict[int, int] = {}
    locked_impl: dict[int, int] = {}  # impl_index -> ref_index (reverse lookup)
    for _score, _td, r_idx, im_idx in candidates:
        if r_idx in locked or im_idx in locked_impl:
            continue
        r_top = _top(ref_by_index[r_idx])
        im_top = _top(next(x for x in impl if x["index"] == im_idx))
        # Y-order feasibility: a ref above an already-locked ref must pair to an
        # impl above that ref's locked impl, and vice-versa. A pair that would
        # cross a locked anchor in Y is rejected (it is a token collision, not a
        # real anchor). When a top is missing fall back to DOM index order.
        if not _y_order_consistent(
            r_idx, im_idx, r_top, im_top, locked, ref_by_index, impl
        ):
            continue
        locked[r_idx] = im_idx
        locked_impl[im_idx] = r_idx
        used_impl.add(im_idx)
    return locked


def _y_order_consistent(
    r_idx: int,
    im_idx: int,
    r_top: float | None,
    im_top: float | None,
    locked: dict[int, int],
    ref_by_index: dict[int, Section],
    impl: list[Section],
) -> bool:
    """True when adding (r_idx -> im_idx) preserves Y-order vs every locked pair.

    For each already-locked (lr -> li): if the new ref sits ABOVE lr it must map
    ABOVE li, and if BELOW lr it must map BELOW li. Ordering uses document-top
    when both sides have a rect (exact in self-pass, order-preserving in a
    faithful clone) and falls back to DOM index when a top is missing.
    """
    impl_top_by_index = {im["index"]: _top(im) for im in impl}

    def _ref_before(a: int, b: int) -> bool:
        ta = _top(ref_by_index[a]) if a in ref_by_index else None
        tb = _top(ref_by_index[b]) if b in ref_by_index else None
        if ta is not None and tb is not None:
            return ta < tb
        return a < b

    def _impl_before(a: int, b: int) -> bool:
        ta, tb = impl_top_by_index.get(a), impl_top_by_index.get(b)
        if ta is not None and tb is not None:
            return ta < tb
        return a < b

    for lr, li in locked.items():
        ref_lt = _ref_before(r_idx, lr)
        impl_lt = _impl_before(im_idx, li)
        if ref_lt != impl_lt:
            return False
    return True


def _fill_between_anchors(
    ref: list[Section],
    impl: list[Section],
    locked: dict[int, int],
    used_impl: set[int],
    eligible_ref: list[int],
) -> dict[int, int]:
    """Phase B — pair remaining ref/impl by monotonic Y-order within anchor gaps.

    The locked anchors from Phase A partition the page into Y-bands. Within each
    band (between two consecutive locked refs, or before the first / after the
    last), the still-free ref sections and still-free impl sections are each in
    Y-order; pair them positionally band-by-band. When a band has more refs than
    free impls, the surplus refs stay unpaired (a genuine enumeration gap — the
    impl rendered fewer sections in that region, e.g. one observed site's card_bg collapsed
    into its pyramid sibling). When it has more impls than refs, the surplus
    impls stay free for Phase C / EXTRA.

    Pairs WITHIN a band by closest drift to the band's anchor drift, so a ref
    pairs to the impl that sits at the corresponding Y-offset rather than blindly
    by within-band rank — this keeps the assignment robust when the band holds an
    unequal count on each side. Order-preserving: never pairs a ref to an impl
    that lies outside (above the upper anchor / below the lower anchor) its band.

    Returns newly-paired {ref_index: impl_index}; mutates used_impl.
    """
    ref_by_index = {r["index"]: r for r in ref}
    impl_by_index = {im["index"]: im for im in impl}

    def _ref_top_or_index(ri: int) -> float:
        t = _top(ref_by_index[ri])
        return t if t is not None else float(ri)

    # Locked refs in Y-order define the band boundaries.
    locked_refs_sorted = sorted(locked, key=_ref_top_or_index)

    def _impl_top(im_idx: int) -> float | None:
        return _top(impl_by_index[im_idx])

    # Boundaries as (lower_ref_top, upper_ref_top, lower_impl_top, upper_impl_top)
    # with -inf/+inf sentinels for the open ends.
    boundaries: list[tuple[float, float, float, float]] = []
    prev_r_top, prev_i_top = float("-inf"), float("-inf")
    for ri in locked_refs_sorted:
        rt = _top(ref_by_index[ri])
        it = _impl_top(locked[ri])
        rt_f = rt if rt is not None else prev_r_top
        it_f = it if it is not None else prev_i_top
        boundaries.append((prev_r_top, rt_f, prev_i_top, it_f))
        prev_r_top, prev_i_top = rt_f, it_f
    boundaries.append((prev_r_top, float("inf"), prev_i_top, float("inf")))

    free_refs = [
        ri for ri in eligible_ref
        if ri not in locked and _top(ref_by_index.get(ri, {})) is not None
    ]
    free_impls = [
        im["index"] for im in impl
        if im["index"] not in used_impl and _top(im) is not None
    ]

    new_pairs: dict[int, int] = {}
    for r_lo, r_hi, i_lo, i_hi in boundaries:
        band_refs = sorted(
            (ri for ri in free_refs if r_lo < (_top(ref_by_index[ri]) or 0) < r_hi),
            key=lambda ri: _top(ref_by_index[ri]) or 0.0,
        )
        band_impls = sorted(
            (ii for ii in free_impls if i_lo < (_impl_top(ii) or 0) < i_hi),
            key=lambda ii: _impl_top(ii) or 0.0,
        )
        if not band_refs or not band_impls:
            continue
        # Reference drift for this band. A faithful clone's drift grows
        # MONOTONICALLY down the page, so a single fixed drift mis-ranks pairs in
        # a tall band where drift climbs from the lower to the upper anchor. Take
        # the MIDPOINT of the two bounding anchors' drifts as the band reference,
        # and size the tolerance to cover the band's drift SPAN (how much drift
        # grows across it) plus the gross-outlier floor. Open-ended bands (before
        # the first / after the last anchor) inherit the single available anchor's
        # drift. This admits every in-band pair (whose drift sits between the two
        # anchors) while still forbidding the cross-band surplus gap (card_bg's
        # ~+4500px jump far exceeds the span+floor tolerance).
        lo_drift = i_lo - r_lo if i_lo != float("-inf") and r_lo != float("-inf") else None
        hi_drift = i_hi - r_hi if i_hi != float("inf") and r_hi != float("inf") else None
        drifts = [d for d in (lo_drift, hi_drift) if d is not None]

        # An anchor on at least one side is REQUIRED. A band with no bounding
        # anchor (the whole page, when Phase A locked nothing) has no reliable
        # drift reference, so Y-order alignment cannot disambiguate which impl a
        # ref pairs to — the text/identity stages (Phase C) handle that case
        # correctly by CONTENT. Defer such bands entirely rather than guess by
        # geometry. (Also keeps Phase B inert on clones that share no class
        # tokens at all, preserving the legacy text-first behavior there.)
        if not drifts:
            continue

        band_drift = sum(drifts) / len(drifts)
        drift_span = (max(drifts) - min(drifts)) if len(drifts) == 2 else 0.0

        # Order-preserving optimal assignment (sequence-alignment DP). When the
        # band holds an unequal count on each side, a greedy top-to-bottom walk
        # can pair the wrong surplus (it forces the LOWER ref to consume the last
        # impl, stranding the section that actually has no partner). The DP picks
        # the monotonic subset of (ref, impl) pairs that MAXIMIZES pairs, breaking
        # ties by MINIMIZING total drift deviation from band_drift, so the
        # genuinely-unmatched section (the one the impl never rendered) falls out
        # as the surplus — e.g. one observed site's card_bg, whose impl collapsed into its
        # pyramid sibling. A pair whose deviation exceeds the band tolerance is
        # forbidden (it is a cross-band mispair, not a real partner). Each pair is
        # rewarded so the DP prefers pairing; the deviation is a tie-break penalty
        # scaled below the reward. Deterministic (no RNG/clock).
        rt_list = [_top(ref_by_index[ri]) or 0.0 for ri in band_refs]
        it_list = [_impl_top(ii) or 0.0 for ii in band_impls]
        n, m = len(band_refs), len(band_impls)
        # A pair must keep its drift within this much of the band reference. The
        # floor + the band's drift span absorb a faithful clone's intra-band
        # growth; it stays well under the surplus gap (card_bg's +4500px).
        band_tol = _DRIFT_OUTLIER_FLOOR_PX + drift_span
        # Maximize pairs (reward 1.0 each), minimize total deviation as a
        # sub-unit tie-break. dp[i][j] = best (pairs, -deviation) for the
        # suffixes; compared lexicographically.
        dp: list[list[tuple[float, float]]] = [
            [(0.0, 0.0)] * (m + 1) for _ in range(n + 1)
        ]
        back = [[0] * (m + 1) for _ in range(n + 1)]  # 0=skip-ref,1=skip-impl,2=pair
        for i in range(n - 1, -1, -1):
            for j in range(m - 1, -1, -1):
                # skip-ref / skip-impl carry their suffix value unchanged.
                skip_ref = dp[i + 1][j]
                skip_impl = dp[i][j + 1]
                best, mv = skip_ref, 0
                if skip_impl > best:
                    best, mv = skip_impl, 1
                dev = abs((it_list[j] - rt_list[i]) - band_drift)
                if dev <= band_tol:
                    sub = dp[i + 1][j + 1]
                    pair = (sub[0] + 1.0, sub[1] - dev)
                    if pair > best:
                        best, mv = pair, 2
                dp[i][j] = best
                back[i][j] = mv
        i = j = 0
        while i < n and j < m:
            mv = back[i][j]
            if mv == 2:
                ri, ii = band_refs[i], band_impls[j]
                new_pairs[ri] = ii
                used_impl.add(ii)
                i += 1
                j += 1
            elif mv == 0:
                i += 1
            else:
                j += 1

    return new_pairs


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


# Gross-outlier floor (px). A pairing whose vertical drift differs from the
# per-page median by MORE than max(this, 3*MAD) is rejected as a mispair. The
# 300px floor is conservative — a faithful clone's section tops cluster within a
# few hundred px of a consistent global offset, so this never fires on normal
# sections. It is also the ref-vs-ref-self-pass safety: in self-pass every drift
# is ~0, so the median is ~0 and no pair can exceed a 300px deviation — the
# repair pass is a strict NO-OP there (achievability meta-gate stays green).
_DRIFT_OUTLIER_FLOOR_PX = 300.0


def _pair_drift(r: Section, im: Section) -> float | None:
    """Vertical drift impl.top - ref.top, or None when either side lacks a top.

    This is the per-pair signal the repair pass clusters on: a faithful clone's
    correct pairings share a near-constant global offset, so a pairing whose
    drift is a gross outlier crops the wrong impl region (BLACK / bogus AE).
    """
    rt, it = _top(r), _top(im)
    if rt is None or it is None:
        return None
    return it - rt


def _repair_drift_outliers(
    ref: list[Section],
    impl: list[Section],
    preferred_impl: dict[int, int],
    used_impl: set[int],
    off_canvas_refs: set[int],
    text_paired: dict[int, float] | None = None,
    semantic_key_paired: set[int] | None = None,
    locked_pairs: dict[int, int] | None = None,
) -> set[int]:
    """Reject gross drift-outlier pairings in favor of position-consistent ones.

    The text/identity stages can pair two sections that SAY the same thing (a
    CTA whose section-map textWords were captured from a shared-base-class FAQ
    sibling) or that share a generic id/class token, even when their document
    positions are wildly inconsistent with the rest of the page. Such a pairing
    crops the wrong impl region and produces a BLACK / bogus-AE verdict.

    A faithful clone's correct pairings share a near-constant vertical offset
    (the impl is uniformly taller/shorter by the intro delta). We compute the
    per-page MEDIAN drift and its MAD, flag any assigned pair whose drift is a
    gross outlier (|drift - median| > max(300px, 3*MAD)), RELEASE it, and try to
    re-pair the freed ref to a still-free impl whose drift is consistent with
    the page median. The released impl re-enters the free pool so a different
    ref (or the order-consistent candidate) can claim it.

    PAIRING ONLY — the downstream AE/dssim/structure compare is unchanged, so a
    more position-consistent pairing yields more accurate measurement, never an
    easier pass. NO-OP in the ref-vs-ref self-pass: every drift is ~0 there, the
    median is ~0, and no pair can exceed the 300px floor.

    Mutates preferred_impl / used_impl (and the text_paired / semantic_key_paired
    label sets, if passed) in place. Returns the set of ref indices that were
    successfully RE-paired to a position-consistent impl, so the caller can label
    them `position-repaired` instead of inheriting a stale text/semantic label.
    Deterministic ordering only.
    """
    ref_by_index = {r["index"]: r for r in ref}
    impl_by_index = {im["index"]: im for im in impl}

    # Drift baseline = every currently-assigned content pair. After Phase A/B
    # these are almost all position-consistent, so the median + MAD capture the
    # page's true (monotonically-growing) drift band. NOTE: do NOT baseline on
    # the Phase-A locked anchors alone — on a tall page the locks straddle the
    # full 0..N px drift growth, making their distribution BIMODAL (half near the
    # top offset, half near the bottom), which collapses the MAD to ~0 and would
    # then flag a perfectly-consistent early-page pair as an outlier. The full
    # assigned set keeps a healthy MAD across the monotonic spread. Skip
    # off-canvas synthetic pairs (they sit at the ref's own off-canvas rect and
    # are not page-flow).
    assigned_drifts: list[float] = []
    for r_idx, im_idx in preferred_impl.items():
        if r_idx in off_canvas_refs:
            continue
        r = ref_by_index.get(r_idx)
        im = impl_by_index.get(im_idx)
        if r is None or im is None:
            continue
        d = _pair_drift(r, im)
        if d is not None:
            assigned_drifts.append(d)

    # Need a stable majority to define "normal" drift. With <3 measurable pairs
    # there is no reliable median to judge an outlier against — leave pairing
    # untouched (the small-page / degenerate case).
    if len(assigned_drifts) < 3:
        return set()

    median_drift = _median(assigned_drifts)
    mad = _median([abs(d - median_drift) for d in assigned_drifts])
    threshold = max(_DRIFT_OUTLIER_FLOOR_PX, 3.0 * mad)

    # Identify outlier pairs (deterministic order: by ref index). Never flag a
    # Phase-A locked anchor — those are the order-consistent ground truth the
    # baseline is built from, so they cannot be outliers by construction.
    locked = locked_pairs or {}
    outliers: list[int] = []
    for r_idx in sorted(preferred_impl):
        if r_idx in off_canvas_refs or r_idx in locked:
            continue
        r = ref_by_index.get(r_idx)
        im = impl_by_index.get(preferred_impl[r_idx])
        if r is None or im is None:
            continue
        d = _pair_drift(r, im)
        if d is None:
            continue
        if abs(d - median_drift) > threshold:
            outliers.append(r_idx)

    if not outliers:
        return set()

    # Release every outlier's impl first so freed impls can be reclaimed by the
    # order-consistent candidate (e.g. a ref that should own an impl currently
    # held by an outlier pairing). Drop the stale text/semantic labels too — a
    # re-paired ref must materialize via the position-anchored fallback, not the
    # text/semantic score from the rejected pairing.
    for r_idx in outliers:
        im_idx = preferred_impl.pop(r_idx)
        used_impl.discard(im_idx)
        if text_paired is not None:
            text_paired.pop(r_idx, None)
        if semantic_key_paired is not None:
            semantic_key_paired.discard(r_idx)

    # Re-pair each freed ref (deterministic order) to the still-free impl whose
    # drift is MOST consistent with the page median. Only accept a candidate
    # whose drift deviation is within the threshold AND strictly better than the
    # rejected pairing by a meaningful margin — otherwise leave the ref unpaired
    # (UNMATCHED) rather than re-introduce a different gross mispair.
    repaired: set[int] = set()
    for r_idx in outliers:
        r = ref_by_index.get(r_idx)
        if r is None:
            continue
        best_im_idx: int | None = None
        best_dev = float("inf")
        for im in impl:
            if im["index"] in used_impl:
                continue
            d = _pair_drift(r, im)
            if d is None:
                continue
            dev = abs(d - median_drift)
            if dev > threshold:
                continue
            # Prefer the most position-consistent candidate; break ties on
            # text similarity then DOM-index proximity (deterministic).
            key_dev = dev
            if best_im_idx is None or key_dev < best_dev or (
                key_dev == best_dev
                and best_im_idx is not None
                and (
                    text_similarity(r, im),
                    -abs(r["index"] - im["index"]),
                )
                > (
                    text_similarity(r, impl_by_index[best_im_idx]),
                    -abs(r["index"] - impl_by_index[best_im_idx]["index"]),
                )
            ):
                best_dev = key_dev
                best_im_idx = im["index"]
        if best_im_idx is not None:
            preferred_impl[r_idx] = best_im_idx
            used_impl.add(best_im_idx)
            repaired.add(r_idx)
        # else: leave UNMATCHED — the downstream loop emits status UNMATCHED,
        # which is an honest "no consistent impl" verdict, never a false pass.

    return repaired


def pair_sections(ref: list[Section], impl: list[Section]) -> list[Section]:
    """Pair ref sections to impl sections, returning a matches list.

    Pairing order, strongest signal first:
    (A) ANCHOR-LOCK — Y-order-stable identity anchors. Pairs that share an exact
        id/class token AND preserve the page's monotonic Y-order are LOCKED:
        they cannot be stolen by a later stage. This makes the assignment
        deterministic under section-count perturbation — a ref with an
        incidental text/token collision can no longer reshuffle the pairing of
        its Y-neighbours.
    (B) Y-ORDER FILL — remaining ref/impl are aligned by monotonic document-top
        within the bands the locked anchors carve out, so a ref at a given Y
        pairs to the impl at the corresponding Y position. Surplus refs in a
        band stay unpaired (a genuine enumeration gap), never reshuffling.
    (C) the legacy fallback for genuine orphans, strongest signal first:
        (1) TEXT-CONTENT similarity — what a section SAYS;
        (2) semantic-key identity overlap (id/class tokens);
        (3) className-exact tokens;
        (4) text similarity with a same-tag + DOM-order tiebreaker.
    A drift-outlier repair pass (baselined on the LOCKED anchors) rejects any
    surviving gross mispair.

    This is a PAIRING signal only — the AE/dssim/structure comparison
    downstream is unchanged, so better pairing yields more accurate
    measurement, never an easier pass. Self-pass (impl==ref): identity is
    present on every section, Y-order is perfect, drift ~0, so Phase A locks
    all pairs 1:1 and B/C/repair are no-ops.
    """
    matches: list[Section] = []
    used_impl: set[int] = set()
    used_names: set[str] = set()
    preferred_impl: dict[int, int] = {}

    # ── Off-canvas pre-pass ──
    # A ref row whose stored rect lies entirely above the canvas is a settled
    # splash/overlay the ref itself unmounted (end-to-end run intro at -900..0).
    # The impl has no enumerable candidate, so normal pairing garbage-matches
    # it to an unrelated on-canvas section and the compare crops painted
    # content against the ref's transparent off-canvas stub. Pair it to a
    # SYNTHETIC impl entry carrying the same rect: both sides then crop the
    # identical off-canvas region (deterministic transparent stubs, AE 0) and
    # no real impl section is consumed.
    off_canvas_refs: set[int] = set()
    for r in ref:
        rect = r.get("rect") or {}
        try:
            off = float(rect.get("top", 0)) + float(rect.get("height", 0)) <= 0
        except (TypeError, ValueError):
            off = False
        if off:
            off_canvas_refs.add(r["index"])

    eligible_ref = [r["index"] for r in ref if r["index"] not in off_canvas_refs]

    # ── Phase A: lock Y-order-stable identity anchors ──
    # High-confidence id/class anchors that preserve the page's monotonic Y-order
    # are pinned 1:1 and may never be stolen by a later stage. On the self-pass
    # this locks every section to its own copy.
    locked_pairs = _lock_identity_anchors(ref, impl, eligible_ref, used_impl)
    preferred_impl.update(locked_pairs)

    # ── Phase B: fill anchor gaps by monotonic Y-order ──
    # Within each Y-band carved out by the locked anchors, pair the still-free
    # ref/impl sections by document-top so a ref at a given Y maps to the impl at
    # the corresponding Y. Surplus refs in a band stay unpaired (enumeration gap,
    # e.g. one observed site's card_bg collapsed into its pyramid sibling).
    band_pairs = _fill_between_anchors(
        ref, impl, locked_pairs, used_impl, eligible_ref
    )
    preferred_impl.update(band_pairs)
    band_paired: set[int] = set(band_pairs)

    # ── TEXT-CONTENT pre-pass (strongest signal, runs first) ──
    # Collect every (ref, impl) candidate at or above the strong-text floor and
    # assign 1:1, globally-best-first. Ties break deterministically on index
    # proximity then index (no RNG/clock — repo scripts forbid them).
    text_paired: dict[int, float] = {}
    text_candidates: list[tuple[float, float, int, int, int]] = []
    for r in ref:
        if r["index"] in off_canvas_refs or r["index"] in preferred_impl:
            continue
        for im in impl:
            if im["index"] in used_impl:
                continue
            sim = text_similarity(r, im)
            if sim >= _STRONG_TEXT_SIM:
                # Tiebreak among equal-text candidates by POSITION first (the
                # section-map duplicates the same innerText onto adjacent
                # same-class rows, so two ref rows tie at sim=1.0 to one impl
                # row — position picks the right one), then DOM-index, then
                # indices for determinism.
                text_candidates.append(
                    (sim, _top_distance(r, im), abs(r["index"] - im["index"]),
                     r["index"], im["index"])
                )
    text_candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3], t[4]))
    for sim, _td, _dist, r_idx, im_idx in text_candidates:
        if r_idx in preferred_impl or im_idx in used_impl:
            continue
        preferred_impl[r_idx] = im_idx
        used_impl.add(im_idx)
        text_paired[r_idx] = sim

    # ── Identity stage (semantic-key + class-signature), GLOBALLY disambiguated ──
    # batch-11 ITEM 4(a): a single shared id/class token (e.g. a generic "footer"
    # id on two distinct sections — a CTA section and a content section) overlaps
    # EVERY footer, so the old greedy per-ref index-proximity tiebreak cross-paired
    # the ref CTA section -> the nearest impl blank footer and stranded the real
    # CTA. Score every
    # identity-overlapping (ref, impl) candidate with _identity_pair_score
    # (id-exact + class-token Jaccard + rect-size + DOM-order) and assign 1:1
    # GLOBALLY best-first, so the class signature — not raw index distance —
    # decides which same-id footer pairs where. Deterministic ordering only
    # (no RNG/clock — repo scripts forbid them).
    semantic_key_paired: set[int] = set()
    ident_candidates: list[tuple[float, float, int, int, int, bool]] = []
    for r in ref:
        if r["index"] in preferred_impl or r["index"] in off_canvas_refs:
            continue
        r_class = _class_tokens(_class_name(r))
        for im in impl:
            if im["index"] in used_impl:
                continue
            sem = _has_identity_overlap(r, im)
            cls_overlap = bool(r_class & _class_tokens(_class_name(im)))
            if not sem and not cls_overlap:
                continue
            ident_candidates.append(
                (
                    _identity_pair_score(r, im),
                    _top_distance(r, im),
                    abs(r["index"] - im["index"]),
                    r["index"],
                    im["index"],
                    sem,
                )
            )
    ident_candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3], t[4]))
    for _score, _td, _dist, r_idx, im_idx, sem in ident_candidates:
        if r_idx in preferred_impl or im_idx in used_impl:
            continue
        preferred_impl[r_idx] = im_idx
        used_impl.add(im_idx)
        if sem:
            # id/tag/class-token (semantic-key) overlap; otherwise it is a pure
            # class-signature match, labeled className-exact in the materializer.
            semantic_key_paired.add(r_idx)

    # ── Drift-outlier repair (gross mispair rejection) ──
    # Both stages above can assign a high-confidence pairing whose vertical
    # position is wildly inconsistent with the rest of the page — a CTA that
    # inherited a sibling FAQ's textWords (shared base class), or a section that
    # shares a generic id/class token with a far-away impl block. Reject such
    # gross drift outliers in favor of a position-consistent candidate. NO-OP in
    # ref-vs-ref self-pass (all drifts ~0). Repaired pairs lose their text/
    # semantic label below (no longer in text_paired/semantic_key_paired), so
    # they materialize via the position-anchored fallback score.
    drift_repaired: set[int] = _repair_drift_outliers(
        ref, impl, preferred_impl, used_impl, off_canvas_refs,
        text_paired, semantic_key_paired, locked_pairs,
    )

    for r in ref:
        if r["index"] in off_canvas_refs:
            rect = dict(r.get("rect") or {})
            name = _dedup_name(_make_name(r, "section"), used_names)
            matches.append({
                "name": name,
                "score": 1.0,
                "ref": r,
                "impl": {
                    "rect": rect,
                    "className": r.get("className"),
                    "tag": r.get("tag"),
                    "offCanvas": True,
                },
                "pairing": "off-canvas",
            })
            continue
        if r["index"] in preferred_impl:
            anchored = next(
                (x for x in impl if x["index"] == preferred_impl[r["index"]]), None
            )
            if anchored:
                name = _dedup_name(_make_name(r, "section"), used_names)
                if r["index"] in drift_repaired:
                    # Re-paired by the drift-outlier repair pass after its
                    # text/semantic pairing was rejected as a gross mispair.
                    pairing_kind = "position-repaired"
                    score_val: float = round(
                        text_similarity(r, anchored), 3
                    )
                elif r["index"] in locked_pairs:
                    # Phase A: Y-order-stable identity anchor (id/class + Y-order).
                    pairing_kind = "anchor-locked"
                    score_val = 1.0
                elif r["index"] in band_paired:
                    # Phase B: paired by monotonic Y-order within an anchor gap.
                    pairing_kind = "y-order"
                    score_val = round(text_similarity(r, anchored), 3)
                elif r["index"] in semantic_key_paired:
                    pairing_kind = "semantic-key"
                    score_val = 1.0
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
