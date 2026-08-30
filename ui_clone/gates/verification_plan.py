"""Verification-Plan gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit, urlunsplit

from ui_clone.check_inputs import (
    compute_check_input_hash,
    get_check_inputs,
    newest_input_mtime,
    sidecar_path,
)
from ui_clone.evidence_validation import (
    hover_state_partial_result,
    load_strict_json_text,
    transition_compare_text_result,
    transition_proof_semantic_error,
    visual_fidelity_semantic_error,
)

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def _runtime_env_passed(ref_dir: Path) -> bool:
    """True only when runtime-env.json positively reports a healthy impl.

    Guards the scroll-coverage infra-skip escalation: if the impl dev server was
    never confirmed up, an infra skip is more likely a mid-build blip than an
    unverified-motion debt, so we stay lenient and do NOT fail closed on it.
    """
    path = ref_dir / "runtime-env.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and str(data.get("status") or "").lower() == "pass"


def _observed_scroll_linked_motion(ref_dir: Path) -> bool:
    """True when the runtime dump PROVES scroll-linked style variation exists.

    animation-runtime-dump.json.scrollLinkedStyles is non-null only when element
    styles actually VARY across scroll (real scroll-scrub transforms) — as
    opposed to a mere smooth-scroll library (Lenis/Locomotive) that moves pixels
    but drives no scroll-linked motion. Gating the scroll-coverage infra-skip
    escalation on this OBSERVED signal (not the over-broad plan hasScrollScrub,
    which flips true on any smooth-scroll lib) prevents false-failing static /
    smooth-scroll-only pages. Conservative: absent/None dump -> False, so an
    unproven page never escalates.
    """
    path = ref_dir / "animation-runtime-dump.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("scrollLinkedStyles"):
        return True
    # A non-null scrollTrigger is scroll-linked motion BY DEFINITION (GSAP
    # ScrollTrigger scrub/pin) even when the scrubbed property (clip-path,
    # filter, backgroundColor, pin-only) leaves no varying inline
    # transform/opacity/width for the inline-style sampler to record. Lenis /
    # smooth-scroll-only sites register no scrollTrigger, so OR-ing it in does
    # not reintroduce the smooth-scroll brick.
    return bool(data.get("scrollTrigger") or data.get("scrollTriggers"))


def _legacy_newest_mtime(impl_root: Path) -> float:
    """Newest mtime over impl_root/{src,public} — the pre-B1 staleness sweep,
    kept only as the conservative fallback for an UNREGISTERED check (one not in
    ui_clone.check_inputs). Registered checks use their declared-input mtime."""
    newest = 0.0
    for sub in ("src", "public"):
        sub_dir = impl_root / sub
        if sub_dir.is_dir():
            for p in sub_dir.rglob("*"):
                try:
                    if p.is_file():
                        newest = max(newest, p.stat().st_mtime)
                except OSError:
                    continue
    return newest


def _registered_check_is_stale(
    ref_dir: Path,
    impl_root: Path | None,
    check_id: str,
    artifact_path: Path,
) -> bool:
    """Compare one registered check artifact with its declared inputs."""
    spec = get_check_inputs(check_id)
    if spec is None or (not spec.impl and not spec.ref):
        return False

    sidecar = sidecar_path(ref_dir, check_id)
    try:
        stored_hash = sidecar.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if sidecar.is_symlink():
            raise OSError(f"broken fingerprint sidecar symlink: {sidecar}")
        stored_hash = None
    if stored_hash is not None:
        current_hash = compute_check_input_hash(impl_root, ref_dir, check_id)
        return stored_hash != current_hash
    if check_id == "transition-proof":
        return True

    newest = newest_input_mtime(impl_root, ref_dir, check_id)
    if newest is None:
        raise OSError(
            f"declared input mtimes unavailable for sidecar-less {check_id}"
        )
    return newest > (artifact_path.stat().st_mtime + 1.0)


_RUNTIME_TEXT_ZERO_WIDTH_NOISE = re.compile("[\u200b\u2060]")
_RUNTIME_TEXT_WHITESPACE = re.compile(r"\s+")
_RUNTIME_TEXT_CJK_BOUNDARY_WHITESPACE = re.compile(
    r"(?<=[\u2e80-\u9fff\uac00-\ud7af])\s+"
    r"|\s+(?=[\u2e80-\u9fff\uac00-\ud7af])"
)


def _runtime_text_comparison_text(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _RUNTIME_TEXT_ZERO_WIDTH_NOISE.sub("", text).replace("\u00a0", " ")
    normalized = _RUNTIME_TEXT_WHITESPACE.sub(" ", text).strip()
    return _RUNTIME_TEXT_CJK_BOUNDARY_WHITESPACE.sub("", normalized)


def _lcs_length(left: list[str], right: list[str]) -> int:
    """Return the exact LCS length without trusting producer diagnostics."""
    previous = [0] * (len(right) + 1)
    for left_item in left:
        current = [0] * (len(right) + 1)
        for index, right_item in enumerate(right, start=1):
            if left_item == right_item:
                current[index] = previous[index - 1] + 1
            else:
                current[index] = max(previous[index], current[index - 1])
        previous = current
    return previous[-1]


def _lcs_alignment(left: list[str], right: list[str]) -> tuple[list[int], list[int]]:
    rows = [[0] * (len(right) + 1)]
    for left_item in left:
        previous = rows[-1]
        current = [0] * (len(right) + 1)
        for index, right_item in enumerate(right, start=1):
            if left_item == right_item:
                current[index] = previous[index - 1] + 1
            else:
                current[index] = max(previous[index], current[index - 1])
        rows.append(current)
    left_matches: list[int] = []
    right_matches: list[int] = []
    i, j = len(left), len(right)
    while i and j:
        if left[i - 1] == right[j - 1]:
            left_matches.append(i - 1)
            right_matches.append(j - 1)
            i -= 1
            j -= 1
        elif rows[i - 1][j] >= rows[i][j - 1]:
            i -= 1
        else:
            j -= 1
    left_matches.reverse()
    right_matches.reverse()
    return left_matches, right_matches


def _canonical_runtime_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))


def _runtime_text_phase_confirmation(
    ref_capture: dict[str, object],
    impl_capture: dict[str, object],
    ref_matches: list[int],
    impl_matches: list[int],
) -> dict[str, object] | None:
    def _record(value: object) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        slot = value.get("slot")
        text = value.get("text")
        tag = value.get("tag")
        initial = value.get("initialViewport")
        if (
            not isinstance(slot, str)
            or not slot
            or not isinstance(text, str)
            or not text
            or not isinstance(tag, str)
            or not isinstance(initial, bool)
        ):
            return None
        return {
            "slot": slot,
            "text": text,
            "tag": tag.upper(),
            "initialViewport": initial,
        }

    def _records(value: object) -> list[dict[str, object]] | None:
        if not isinstance(value, list):
            return None
        records = [_record(item) for item in value]
        if any(record is None for record in records):
            return None
        return [record for record in records if record is not None]

    ref_records = _records(ref_capture.get("records"))
    impl_records = _records(impl_capture.get("records"))
    ref_samples_raw = ref_capture.get("samples")
    impl_samples_raw = impl_capture.get("samples")
    if (
        ref_records is None
        or impl_records is None
        or not isinstance(ref_samples_raw, list)
        or not isinstance(impl_samples_raw, list)
        or [item["text"] for item in ref_records] != ref_capture.get("blocks")
        or [item["text"] for item in impl_records] != impl_capture.get("blocks")
    ):
        return None
    ref_samples = [_records(sample) for sample in ref_samples_raw]
    impl_samples = [_records(sample) for sample in impl_samples_raw]
    if (
        not ref_samples
        or not impl_samples
        or any(sample is None for sample in ref_samples)
        or any(sample is None for sample in impl_samples)
    ):
        return None
    typed_ref_samples = [
        sample for sample in ref_samples if sample is not None
    ]
    typed_impl_samples = [
        sample for sample in impl_samples if sample is not None
    ]
    all_record_sets = [
        ref_records,
        impl_records,
        *typed_ref_samples,
        *typed_impl_samples,
    ]
    if any(
        len({item["slot"] for item in records}) != len(records)
        for records in all_record_sets
    ):
        return None
    if (
        typed_ref_samples[-1] != ref_records
        or typed_impl_samples[-1] != impl_records
    ):
        return None
    ref_phase_start = ref_capture.get("phaseSampleStartIndex")
    impl_phase_start = impl_capture.get("phaseSampleStartIndex")
    if (
        type(ref_phase_start) is not int
        or type(impl_phase_start) is not int
        or ref_phase_start < 0
        or impl_phase_start < 0
        or ref_phase_start > len(typed_ref_samples) - 2
        or impl_phase_start > len(typed_impl_samples) - 2
    ):
        return None

    boundaries = [
        (-1, -1),
        *zip(ref_matches, impl_matches, strict=True),
        (len(ref_records), len(impl_records)),
    ]
    gaps: list[dict[str, object]] = []
    for (ref_before, impl_before), (ref_after, impl_after) in zip(
        boundaries, boundaries[1:]
    ):
        ref_gap = ref_records[ref_before + 1 : ref_after]
        impl_gap = impl_records[impl_before + 1 : impl_after]
        if ref_gap or impl_gap:
            gaps.append({
                "ref": ref_gap,
                "impl": impl_gap,
                "refBeforeSlot": (
                    ref_records[ref_before]["slot"] if ref_before >= 0 else None
                ),
                "refBeforeAnchor": (
                    ref_records[ref_before] if ref_before >= 0 else None
                ),
                "refAfterSlot": (
                    ref_records[ref_after]["slot"]
                    if ref_after < len(ref_records)
                    else None
                ),
                "refAfterAnchor": (
                    ref_records[ref_after]
                    if ref_after < len(ref_records)
                    else None
                ),
                "implBeforeSlot": (
                    impl_records[impl_before]["slot"] if impl_before >= 0 else None
                ),
                "implBeforeAnchor": (
                    impl_records[impl_before] if impl_before >= 0 else None
                ),
                "implAfterSlot": (
                    impl_records[impl_after]["slot"]
                    if impl_after < len(impl_records)
                    else None
                ),
                "implAfterAnchor": (
                    impl_records[impl_after]
                    if impl_after < len(impl_records)
                    else None
                ),
            })
    if not gaps:
        return None

    proof: list[dict[str, object]] = []
    protected_tags = {"H1", "H2", "H3"}

    def _bounded_sample_gap(
        sample: list[dict[str, object]],
        before_anchor: object,
        after_anchor: object,
    ) -> list[dict[str, object]] | None:
        if before_anchor is None and after_anchor is None:
            return None
        if before_anchor is not None and not isinstance(before_anchor, dict):
            return None
        if after_anchor is not None and not isinstance(after_anchor, dict):
            return None
        def anchor_index(anchor: object) -> int | None:
            if anchor is None:
                return None
            if not isinstance(anchor, dict):
                return -1
            expected = (anchor.get("slot"), anchor.get("text"))
            return next(
                (
                    index for index, item in enumerate(sample)
                    if (item["slot"], item["text"]) == expected
                ),
                -1,
            )
        before_index = anchor_index(before_anchor)
        after_index = anchor_index(after_anchor)
        if before_index == -1 or after_index == -1:
            return None
        start = 0 if before_index is None else before_index + 1
        end = len(sample) if after_index is None else after_index
        if start > end:
            return None
        return sample[start:end]

    def _projected_anchor(anchor: object) -> dict[str, object] | None:
        if anchor is None:
            return None
        if not isinstance(anchor, dict):
            return None
        return {"slot": anchor.get("slot"), "text": anchor.get("text")}

    def _projected_record(record: dict[str, object]) -> dict[str, object]:
        return {
            "slot": record["slot"],
            "text": record["text"],
            "tag": record["tag"],
            "initialViewport": record["initialViewport"],
        }

    def _slot_tail(
        record: dict[str, object],
        depth: int,
    ) -> tuple[str, ...]:
        return tuple(str(record["slot"]).split(">")[-depth:])

    def _same_shape(
        ref_gap: list[dict[str, object]],
        impl_gap: list[dict[str, object]],
        *,
        tail_depth: int = 1,
    ) -> bool:
        return (
            len(ref_gap) == len(impl_gap)
            and all(
                ref_item["tag"] == impl_item["tag"]
                and _slot_tail(ref_item, tail_depth)
                == _slot_tail(impl_item, tail_depth)
                for ref_item, impl_item in zip(
                    ref_gap, impl_gap, strict=True
                )
            )
        )

    def _phase_gap_states(
        samples: list[list[dict[str, object]]],
        phase_start: int,
        before_anchor: object,
        after_anchor: object,
        expected: list[dict[str, object]],
    ) -> set[tuple[str, ...]] | None:
        states: set[tuple[str, ...]] = set()
        for sample in samples[phase_start:]:
            observed = _bounded_sample_gap(
                sample,
                before_anchor,
                after_anchor,
            )
            if (
                observed is None
                or len(observed) != len(expected)
                or [item["tag"] for item in observed]
                != [item["tag"] for item in expected]
            ):
                return None
            states.add(tuple(
                _runtime_text_comparison_text(item["text"])
                for item in observed
            ))
        return states

    def _dynamic_region_proof(
        gap: dict[str, object],
    ) -> dict[str, object] | None:
        gap_ref_items = cast(list[dict[str, object]], gap["ref"])
        gap_impl_items = cast(list[dict[str, object]], gap["impl"])
        if (
            len(gap_ref_items) < 2
            or not _same_shape(gap_ref_items, gap_impl_items)
            or any(
                item["tag"] in protected_tags
                for item in [*gap_ref_items, *gap_impl_items]
            )
        ):
            return None
        ref_states = _phase_gap_states(
            typed_ref_samples,
            ref_phase_start,
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            gap_ref_items,
        )
        impl_states = _phase_gap_states(
            typed_impl_samples,
            impl_phase_start,
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
            gap_impl_items,
        )
        if (
            ref_states is None
            or impl_states is None
            or len(ref_states) < 2
            or len(impl_states) < 2
        ):
            return None
        return {
            "kind": "dynamic-region",
            "recordCount": len(gap_ref_items),
            "referenceStateCount": len(ref_states),
            "implementationStateCount": len(impl_states),
        }

    def _volatile_counter_proof(
        gap: dict[str, object],
    ) -> dict[str, object] | None:
        gap_ref_items = cast(list[dict[str, object]], gap["ref"])
        gap_impl_items = cast(list[dict[str, object]], gap["impl"])
        if (
            len(gap_ref_items) != 1
            or len(gap_impl_items) != 1
            or not _same_shape(
                gap_ref_items,
                gap_impl_items,
                tail_depth=3,
            )
        ):
            return None
        ref_item = gap_ref_items[0]
        impl_item = gap_impl_items[0]
        if (
            ref_item["tag"] in protected_tags
            or not re.fullmatch(
                r"\d{1,4}",
                _runtime_text_comparison_text(ref_item["text"]),
            )
            or not re.fullmatch(
                r"\d{1,4}",
                _runtime_text_comparison_text(impl_item["text"]),
            )
        ):
            return None
        anchors = (
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
        )
        if any(not isinstance(anchor, dict) for anchor in anchors):
            return None
        anchor_texts = [
            _runtime_text_comparison_text(anchor["text"])
            for anchor in anchors
            if isinstance(anchor, dict)
        ]
        if (
            anchor_texts[0] != anchor_texts[2]
            or anchor_texts[1] != anchor_texts[3]
            or any(
                not text or re.search(r"\w", text, flags=re.UNICODE)
                for text in anchor_texts
            )
        ):
            return None
        return {
            "kind": "volatile-counter",
            "reference": _projected_record(ref_item),
            "implementation": _projected_record(impl_item),
        }

    def _progressive_reveal_proof(
        gap: dict[str, object],
    ) -> dict[str, object] | None:
        gap_ref_items = cast(list[dict[str, object]], gap["ref"])
        gap_impl_items = cast(list[dict[str, object]], gap["impl"])
        if (
            len(gap_ref_items) != 1
            or len(gap_impl_items) != 1
            or not _same_shape(
                gap_ref_items,
                gap_impl_items,
                tail_depth=3,
            )
        ):
            return None
        ref_item = gap_ref_items[0]
        impl_item = gap_impl_items[0]
        if (
            ref_item["initialViewport"]
            or impl_item["initialViewport"]
            or ref_item["tag"] in protected_tags
        ):
            return None
        ref_text = _runtime_text_comparison_text(ref_item["text"])
        impl_text = _runtime_text_comparison_text(impl_item["text"])
        if ref_text in impl_text:
            short_samples, short_item, long_text = (
                typed_ref_samples,
                ref_item,
                impl_text,
            )
        elif impl_text in ref_text:
            short_samples, short_item, long_text = (
                typed_impl_samples,
                impl_item,
                ref_text,
            )
        else:
            return None
        variants = {
            _runtime_text_comparison_text(item["text"])
            for sample in short_samples
            for item in sample
            if item["slot"] == short_item["slot"]
        }
        if (
            len(variants) < 2
            or any(
                not variant or variant not in long_text
                for variant in variants
            )
        ):
            return None
        return {
            "kind": "progressive-reveal",
            "observedVariantCount": len(variants),
            "reference": _projected_record(ref_item),
            "implementation": _projected_record(impl_item),
        }

    def _live_card_proof(
        gap: dict[str, object],
    ) -> dict[str, object] | None:
        gap_ref_items = cast(list[dict[str, object]], gap["ref"])
        gap_impl_items = cast(list[dict[str, object]], gap["impl"])
        disputed = [*gap_ref_items, *gap_impl_items]
        anchors = (
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
        )
        if (
            len(gap_ref_items) < 2
            or not _same_shape(
                gap_ref_items,
                gap_impl_items,
                tail_depth=3,
            )
            or any(not isinstance(anchor, dict) for anchor in anchors)
            or any(
                anchor["tag"] not in {"A", "BUTTON"}
                for anchor in anchors
                if isinstance(anchor, dict)
            )
            or any(
                item["initialViewport"]
                or item["tag"] in protected_tags
                for item in disputed
            )
            or any(
                not any(
                    character.isalpha()
                    for character in str(item["text"])
                )
                for item in disputed
            )
        ):
            return None
        ref_texts = {
            _runtime_text_comparison_text(item["text"])
            for item in gap_ref_items
        }
        impl_texts = {
            _runtime_text_comparison_text(item["text"])
            for item in gap_impl_items
        }
        if ref_texts & impl_texts:
            return None
        return {
            "kind": "live-card-region",
            "recordCount": len(gap_ref_items),
            "slotTailDepth": 3,
        }

    def _side_recurrence(
        samples: list[list[dict[str, object]]],
        phase_start: int,
        before_anchor: object,
        after_anchor: object,
        candidate: dict[str, object],
    ) -> dict[str, object] | None:
        states: list[tuple[int, str, dict[str, object] | None]] = []
        for sample_index in range(phase_start, len(samples)):
            observed = _bounded_sample_gap(
                samples[sample_index],
                before_anchor,
                after_anchor,
            )
            if observed is None:
                return None
            if not observed:
                states.append((sample_index, "absent", None))
                continue
            if (
                len(observed) == 1
                and observed[0]["text"] == candidate["text"]
                and observed[0]["tag"] == candidate["tag"]
                and observed[0]["initialViewport"] is False
                and observed[0]["tag"] not in protected_tags
            ):
                states.append((sample_index, "present", observed[0]))
                continue
            return None

        runs: list[dict[str, object]] = []
        for sample_index, state, record in states:
            if runs and runs[-1]["state"] == state:
                runs[-1]["end"] = sample_index
                continue
            runs.append({
                "state": state,
                "start": sample_index,
                "end": sample_index,
                "record": record,
            })
        for first, middle, last in zip(runs, runs[1:], runs[2:]):
            first_start = first.get("start")
            first_end = first.get("end")
            middle_start = middle.get("start")
            middle_end = middle.get("end")
            last_start = last.get("start")
            if not all(
                type(value) is int
                for value in (
                    first_start,
                    first_end,
                    middle_start,
                    middle_end,
                    last_start,
                )
            ):
                return None
            assert isinstance(first_start, int)
            assert isinstance(first_end, int)
            assert isinstance(middle_start, int)
            assert isinstance(middle_end, int)
            assert isinstance(last_start, int)
            if (
                first["state"] != last["state"]
                or first["state"] == middle["state"]
            ):
                continue
            polarity = (
                "present-absent-present"
                if first["state"] == "present"
                else "absent-present-absent"
            )
            present_run = first if first["state"] == "present" else middle
            first_absent_run = first if first["state"] == "absent" else middle
            present_record = present_run.get("record")
            present_start = present_run.get("start")
            absent_start = first_absent_run.get("start")
            absent_end = first_absent_run.get("end")
            if (
                not isinstance(present_record, dict)
                or type(present_start) is not int
                or type(absent_start) is not int
                or type(absent_end) is not int
            ):
                return None
            return {
                "phaseSampleStartIndex": phase_start,
                "cyclePolarity": polarity,
                "candidatePresentSample": present_start,
                "candidateAbsentStartSample": absent_start,
                "candidateRecurredSample": last_start,
                "absenceRunLength": absent_end - absent_start + 1,
                "candidate": _projected_record(present_record),
            }
        return None

    legacy_gap_count = sum(
        bool(gap["ref"]) != bool(gap["impl"])
        for gap in gaps
    )
    if legacy_gap_count > 2:
        return {
            "accepted": False,
            "reason": "too-many-phase-gaps",
            "gapCount": len(gaps),
        }

    for gap_index, gap in enumerate(gaps):
        gap_ref_items = gap["ref"]
        gap_impl_items = gap["impl"]
        if not isinstance(gap_ref_items, list) or not isinstance(gap_impl_items, list):
            return None
        if gap_ref_items and gap_impl_items:
            substitution_proof = (
                _dynamic_region_proof(gap)
                or _volatile_counter_proof(gap)
                or _progressive_reveal_proof(gap)
                or _live_card_proof(gap)
            )
            if substitution_proof is None:
                return {
                    "accepted": False,
                    "reason": "unproven-slot-variance",
                    "gapIndex": gap_index,
                }
            proof.append({
                "gapIndex": gap_index,
                **substitution_proof,
            })
            continue
        disputed = [*gap_ref_items, *gap_impl_items]
        if (len(gap_ref_items), len(gap_impl_items)) not in {(1, 0), (0, 1)}:
            return None
        if any(
            item["initialViewport"] or item["tag"] in protected_tags
            for item in disputed
        ):
            return None
        candidate_side = "ref" if gap_ref_items else "impl"
        candidate_record = disputed[0]
        if not isinstance(candidate_record, dict):
            return None
        ref_recurrence = _side_recurrence(
            typed_ref_samples,
            ref_phase_start,
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            candidate_record,
        )
        impl_recurrence = _side_recurrence(
            typed_impl_samples,
            impl_phase_start,
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
            candidate_record,
        )
        if ref_recurrence is None or impl_recurrence is None:
            return None

        candidate_before = (
            gap["refBeforeAnchor"]
            if candidate_side == "ref"
            else gap["implBeforeAnchor"]
        )
        candidate_after = (
            gap["refAfterAnchor"]
            if candidate_side == "ref"
            else gap["implAfterAnchor"]
        )
        item_proof: dict[str, object] = {
            "gapIndex": gap_index,
            "beforeSlot": (
                candidate_before.get("slot")
                if isinstance(candidate_before, dict) else None
            ),
            "afterSlot": (
                candidate_after.get("slot")
                if isinstance(candidate_after, dict) else None
            ),
            "beforeAnchor": _projected_anchor(candidate_before),
            "afterAnchor": _projected_anchor(candidate_after),
            "refBeforeAnchor": _projected_anchor(gap["refBeforeAnchor"]),
            "refAfterAnchor": _projected_anchor(gap["refAfterAnchor"]),
            "implBeforeAnchor": _projected_anchor(gap["implBeforeAnchor"]),
            "implAfterAnchor": _projected_anchor(gap["implAfterAnchor"]),
            "candidateSide": candidate_side,
            "candidate": _projected_record(candidate_record),
            "matchedReferenceCandidatePresentSample": (
                ref_recurrence["candidatePresentSample"]
            ),
            "referenceCyclePolarity": ref_recurrence["cyclePolarity"],
            "matchedReferenceCandidateAbsentStartSample": (
                ref_recurrence["candidateAbsentStartSample"]
            ),
            "matchedReferenceCandidateRecurredSample": (
                ref_recurrence["candidateRecurredSample"]
            ),
            "referenceAbsenceRunLength": ref_recurrence["absenceRunLength"],
            "referencePhaseSampleStartIndex": (
                ref_recurrence["phaseSampleStartIndex"]
            ),
            "referenceCandidate": ref_recurrence["candidate"],
            "matchedImplementationCandidateSample": (
                impl_recurrence["candidatePresentSample"]
            ),
            "implementationCyclePolarity": impl_recurrence["cyclePolarity"],
            "matchedImplementationCandidateAbsentStartSample": (
                impl_recurrence["candidateAbsentStartSample"]
            ),
            "matchedImplementationCandidateRecurredSample": (
                impl_recurrence["candidateRecurredSample"]
            ),
            "implementationAbsenceRunLength": (
                impl_recurrence["absenceRunLength"]
            ),
            "implementationPhaseSampleStartIndex": (
                impl_recurrence["phaseSampleStartIndex"]
            ),
            "implementationCandidate": impl_recurrence["candidate"],
        }
        proof.append(item_proof)
    return {
        "accepted": True,
        "advisory": "bounded rendered phase variance confirmed",
        "gapCount": len(gaps),
        "proof": proof,
        "referenceSampleCount": len(typed_ref_samples),
        "implementationSampleCount": len(typed_impl_samples),
    }


def _runtime_text_semantic_error(data: dict[str, object]) -> str | None:
    """Validate runtime-text claims independently of producer-authored metrics."""
    ref_capture = data.get("ref")
    impl_capture = data.get("impl")
    comparison = data.get("comparison")
    violations = data.get("violations")
    status = str(data.get("status") or "").lower()
    if (
        not isinstance(ref_capture, dict)
        or not isinstance(impl_capture, dict)
        or not isinstance(comparison, dict)
        or not isinstance(violations, list)
    ):
        return "capture/comparison/violations shape is invalid"

    def _capture_error(label: str, capture: dict[str, object]) -> str | None:
        blocks = capture.get("blocks")
        records = capture.get("records")
        samples = capture.get("samples")
        block_count = capture.get("blockCount")
        phase_start = capture.get("phaseSampleStartIndex")
        if (
            type(block_count) is not int
            or not isinstance(blocks, list)
            or block_count != len(blocks)
            or not blocks
            or not all(
                isinstance(item, str)
                and item
                and _runtime_text_comparison_text(item)
                for item in blocks
            )
        ):
            return f"{label} blocks/blockCount are invalid"

        def _record(value: object) -> dict[str, object] | None:
            if not isinstance(value, dict):
                return None
            slot = value.get("slot")
            text = value.get("text")
            tag = value.get("tag")
            initial = value.get("initialViewport")
            if (
                not isinstance(slot, str)
                or not slot
                or not isinstance(text, str)
                or not text
                or not _runtime_text_comparison_text(text)
                or not isinstance(tag, str)
                or not tag
                or not isinstance(initial, bool)
            ):
                return None
            return {
                "slot": slot,
                "text": text,
                "tag": tag.upper(),
                "initialViewport": initial,
            }

        if not isinstance(records, list):
            return f"{label} records are missing"
        typed_records = [_record(item) for item in records]
        if (
            any(item is None for item in typed_records)
            or [item["text"] for item in typed_records if item is not None] != blocks
            or len({item["slot"] for item in typed_records if item is not None})
            != len(typed_records)
        ):
            return f"{label} records do not exactly bind blocks and unique slots"
        if not isinstance(samples, list) or len(samples) < 2:
            return f"{label} samples are missing"
        typed_samples: list[list[dict[str, object]]] = []
        for sample in samples:
            if not isinstance(sample, list):
                return f"{label} sample is not a record list"
            typed_sample = [_record(item) for item in sample]
            if (
                any(item is None for item in typed_sample)
                or len({
                    item["slot"] for item in typed_sample if item is not None
                }) != len(typed_sample)
            ):
                return f"{label} sample records are invalid or duplicated"
            typed_samples.append([
                item for item in typed_sample if item is not None
            ])
        normalized_records = [
            item for item in typed_records if item is not None
        ]
        if typed_samples[-1] != normalized_records:
            return f"{label} final sample does not match final records"
        if (
            type(phase_start) is not int
            or phase_start < 0
            or phase_start > len(typed_samples) - 2
        ):
            return f"{label} phase sample window is invalid"
        return None

    for label, capture in (
        ("reference", ref_capture),
        ("implementation", impl_capture),
    ):
        capture_error = _capture_error(label, capture)
        if capture_error is not None:
            return capture_error

    ref_blocks_value = ref_capture.get("blocks")
    impl_blocks_value = impl_capture.get("blocks")
    assert isinstance(ref_blocks_value, list)
    assert isinstance(impl_blocks_value, list)
    ref_blocks = [
        item for item in ref_blocks_value if isinstance(item, str)
    ]
    impl_blocks = [
        item for item in impl_blocks_value if isinstance(item, str)
    ]

    ref_url = _canonical_runtime_url(data.get("refUrl"))
    impl_url = _canonical_runtime_url(data.get("implUrl"))
    actual_ref_url = _canonical_runtime_url(data.get("actualRefUrl"))
    actual_impl_url = _canonical_runtime_url(data.get("actualImplUrl"))
    if None in {ref_url, impl_url, actual_ref_url, actual_impl_url}:
        return "requested/actual URL evidence is missing or invalid"
    if ref_url == impl_url or actual_ref_url == actual_impl_url:
        return "reference and implementation URL/route evidence collides"
    if ref_url != actual_ref_url or impl_url != actual_impl_url:
        return "captured actual URL/route does not match the requested URL"

    capture_receipt = data.get("captureReceipt")
    if not isinstance(capture_receipt, dict):
        return "captureReceipt is missing"
    for side, requested_url, actual_url in (
        ("ref", ref_url, actual_ref_url),
        ("impl", impl_url, actual_impl_url),
    ):
        receipt = capture_receipt.get(side)
        if not isinstance(receipt, dict):
            return f"captureReceipt.{side} is missing"
        assert requested_url is not None
        assert actual_url is not None
        actual_parts = urlsplit(actual_url)
        expected_origin = f"{actual_parts.scheme}://{actual_parts.netloc}"
        receipt_status = receipt.get("responseStatus")
        receipt_attempt = receipt.get("attempt")
        receipt_close_attempts = receipt.get("closeAttempts")
        if (
            _canonical_runtime_url(receipt.get("requestedUrl")) != requested_url
            or _canonical_runtime_url(receipt.get("openUrl")) != actual_url
            or _canonical_runtime_url(receipt.get("actualUrl")) != actual_url
            or _canonical_runtime_url(receipt.get("analysisUrl")) != actual_url
            or receipt.get("analysisOrigin") != expected_origin
            or type(receipt_status) is not int
            or not (200 <= receipt_status < 400)
            or receipt.get("readyState") != "complete"
            or not isinstance(receipt.get("navigationType"), str)
            or not receipt.get("navigationType")
            or receipt.get("errorDocument") is not False
            or receipt.get("batchCommandCount") != 6
            or type(receipt_attempt) is not int
            or not (1 <= receipt_attempt <= 3)
            or type(receipt_close_attempts) is not int
            or not (1 <= receipt_close_attempts <= 3)
            or receipt.get("closed") is not True
        ):
            return f"captureReceipt.{side} is incomplete or inconsistent"

    typed_ref = [
        _runtime_text_comparison_text(item)
        for item in ref_blocks
    ]
    typed_impl = [
        _runtime_text_comparison_text(item)
        for item in impl_blocks
    ]
    lcs_length = _lcs_length(typed_ref, typed_impl)
    ref_matches, impl_matches = _lcs_alignment(typed_ref, typed_impl)
    missing_count = len(typed_ref) - lcs_length
    extra_count = len(typed_impl) - lcs_length
    combined_count = len(typed_ref) + len(typed_impl)
    ordered_similarity = (
        2 * lcs_length / combined_count if combined_count else 1.0
    )
    missing_ratio = missing_count / len(typed_ref) if typed_ref else 0.0
    max_missing_blocks = max(1, int(len(typed_ref) * 0.15))

    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict):
        return "threshold metadata is missing"
    expected_thresholds: dict[str, int | float] = {
        "minOrderedSimilarity": 0.85,
        "maxMissingRatio": 0.15,
        "maxMissingBlocks": max_missing_blocks,
    }
    for field, expected in expected_thresholds.items():
        actual = thresholds.get(field)
        if isinstance(expected, int):
            if type(actual) is not int or actual != expected:
                return f"thresholds.{field}={actual!r}, expected {expected}"
        elif (
            isinstance(actual, bool)
            or not isinstance(actual, int | float)
            or abs(float(actual) - expected) > 0.0001
        ):
            return f"thresholds.{field}={actual!r}, expected {expected}"

    expected_ints = {
        "lcsLength": lcs_length,
        "missingCount": missing_count,
        "extraCount": extra_count,
    }
    for field, expected in expected_ints.items():
        actual = comparison.get(field)
        if isinstance(actual, bool) or not isinstance(actual, int) or actual != expected:
            return f"comparison.{field}={actual!r}, expected {expected}"

    expected_floats = {
        "orderedSimilarity": round(ordered_similarity, 4),
        "missingRatio": round(missing_ratio, 4),
    }
    for metric, expected_float in expected_floats.items():
        actual = comparison.get(metric)
        if isinstance(actual, bool) or not isinstance(actual, int | float):
            return f"comparison.{metric}={actual!r}, expected {expected_float}"
        if abs(float(actual) - expected_float) > 0.0001:
            return f"comparison.{metric}={actual!r}, expected {expected_float}"

    if status == "pass":
        if typed_ref != typed_impl:
            if (
                ordered_similarity < 0.85
                or missing_ratio > 0.15
                or missing_count > max_missing_blocks
            ):
                return "status=pass phase variance exceeds near-pass bounds"
            phase_variance = data.get("phaseVariance")
            if not isinstance(phase_variance, dict):
                return "status=pass mismatch requires phaseVariance object"
            if phase_variance.get("accepted") is not True:
                return "status=pass mismatch requires accepted phaseVariance proof"
            strict_integer_fields = [
                phase_variance.get("gapCount"),
                phase_variance.get("referenceSampleCount"),
                phase_variance.get("implementationSampleCount"),
            ]
            proof_value = phase_variance.get("proof")
            if not isinstance(proof_value, list):
                return "phaseVariance.proof must be a list"
            for item in proof_value:
                if not isinstance(item, dict):
                    return "phaseVariance.proof entries must be objects"
                strict_integer_fields.extend(
                    item.get(field)
                    for field in (
                        "gapIndex",
                        "matchedReferenceCandidatePresentSample",
                        "matchedReferenceCandidateAbsentStartSample",
                        "matchedReferenceCandidateRecurredSample",
                        "referenceAbsenceRunLength",
                        "matchedImplementationCandidateSample",
                        "matchedImplementationCandidateAbsentStartSample",
                        "matchedImplementationCandidateRecurredSample",
                        "implementationAbsenceRunLength",
                        "referencePhaseSampleStartIndex",
                        "implementationPhaseSampleStartIndex",
                        "recordCount",
                        "referenceStateCount",
                        "implementationStateCount",
                        "slotTailDepth",
                        "observedVariantCount",
                    )
                    if field in item
                )
            if any(type(value) is not int for value in strict_integer_fields):
                return "phaseVariance proof indices and counts must be integers"
            expected_phase = _runtime_text_phase_confirmation(
                ref_capture, impl_capture, ref_matches, impl_matches
            )
            if expected_phase is None or phase_variance != expected_phase:
                return (
                    "status=pass mismatch requires independently confirmed "
                    "phaseVariance proof"
                )
        else:
            phase_variance = data.get("phaseVariance")
            if phase_variance != {"accepted": False, "reason": "exact-match"}:
                return (
                    "exact canonical sequences require explicit exact-match "
                    "phase metadata"
                )
            ref_samples = ref_capture.get("samples")
            impl_samples = impl_capture.get("samples")
            ref_phase_start = ref_capture.get("phaseSampleStartIndex")
            impl_phase_start = impl_capture.get("phaseSampleStartIndex")
            assert isinstance(ref_samples, list)
            assert isinstance(impl_samples, list)
            assert isinstance(ref_phase_start, int)
            assert isinstance(impl_phase_start, int)

            def _phase_catalog(
                samples: list[object],
                phase_start: int,
            ) -> set[tuple[str, ...]]:
                return {
                    tuple(
                        _runtime_text_comparison_text(record.get("text"))
                        for record in sample
                        if isinstance(record, dict)
                    )
                    for sample in samples[phase_start:]
                    if isinstance(sample, list)
                }

            if _phase_catalog(ref_samples, ref_phase_start) != _phase_catalog(
                impl_samples, impl_phase_start
            ):
                return (
                    "exact final sequences hide different phase-window "
                    "rendered text catalogs"
                )
        if violations:
            return "status=pass requires an empty violations list"
    return None


def _runtime_text_provenance_error(
    ref_dir: Path,
    artifact_path: Path,
    data: dict[str, object],
) -> str | None:
    """Verify dispatcher-owned URL and exact-artifact provenance."""
    provenance_path = ref_dir / "runtime-text-sequence.provenance.json"
    try:
        artifact_bytes = artifact_path.read_bytes()
        artifact_mtime_ns = artifact_path.stat().st_mtime_ns
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "runtime-text-sequence.provenance.json is missing"
    except (OSError, json.JSONDecodeError) as exc:
        return f"provenance is unreadable ({exc})"
    if not isinstance(provenance, dict):
        return "provenance is not an object"
    if (
        provenance.get("schemaVersion") != 1
        or provenance.get("owner") != "run-required-checks"
        or provenance.get("artifact") != artifact_path.name
    ):
        return "provenance identity is invalid"
    digest = provenance.get("artifactSha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or digest != digest.lower()
        or any(char not in "0123456789abcdef" for char in digest)
        or digest != hashlib.sha256(artifact_bytes).hexdigest()
    ):
        return "artifact digest does not match"
    if provenance.get("artifactMtimeNs") != artifact_mtime_ns:
        return "artifact mtime does not match"

    expected = {
        "ref": _canonical_runtime_url(provenance.get("refUrl")),
        "impl": _canonical_runtime_url(provenance.get("implUrl")),
    }
    if (
        None in expected.values()
        or expected["ref"] == expected["impl"]
        or provenance.get("refUrl") != expected["ref"]
        or provenance.get("implUrl") != expected["impl"]
    ):
        return "provenance URLs are invalid or non-canonical"
    receipts = data.get("captureReceipt")
    if not isinstance(receipts, dict):
        return "captureReceipt is missing"
    for side, top_requested, top_actual in (
        ("ref", "refUrl", "actualRefUrl"),
        ("impl", "implUrl", "actualImplUrl"),
    ):
        receipt = receipts.get(side)
        if not isinstance(receipt, dict):
            return f"captureReceipt.{side} is missing"
        urls = (
            data.get(top_requested),
            data.get(top_actual),
            receipt.get("requestedUrl"),
            receipt.get("openUrl"),
            receipt.get("actualUrl"),
            receipt.get("analysisUrl"),
        )
        if any(_canonical_runtime_url(url) != expected[side] for url in urls):
            return f"{side} URL evidence does not match provenance"
        parts = urlsplit(str(expected[side]))
        if receipt.get("analysisOrigin") != f"{parts.scheme}://{parts.netloc}":
            return f"captureReceipt.{side}.analysisOrigin does not match provenance"
    return None


def _check_verification_plan(self: Gate) -> list[CheckResult]:
    """Honor tmp/ref/<c>/verification-plan.json — declared site-specific checks.

    Schema:
      { "schemaVersion": 1,
        "requiredChecks": [{
          "id": "<short-id>",
          "script": "<path/to/script.sh>",
          "produces": "<artifact relative to ref-dir>",
          "reason": "<why required>",
          "severity": "block" | "warn"
        }] }

    Missing file → returns []. Each required check artifact must exist
    and contain `"status": "pass"`. Severity "warn" emits a warning that
    does not block; "block" (default) fails the gate.
    """
    plan_path = self.ref_dir / "verification-plan.json"
    if not plan_path.is_file():
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                "verification-plan.json — MISSING. post-implement cannot infer required text/DOM/asset/motion checks without it.",
                fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
            )
        ]

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                f"verification-plan.json — unreadable ({e}). "
                "post-implement cannot enforce required checks against "
                "an unparseable plan. Regenerate before retrying.",
                fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
            )
        ]

    schema_version = plan.get("schemaVersion")
    vp_fix = "Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>"
    if "schemaVersion" not in plan:
        # Hand-written / hallucinated verification-plan.json (e.g. agent
        # inventing {component, checks} keys instead of running
        # verification-plan.sh) used to slip through as a silent warn —
        # making every declared required check unenforceable. Hard-fail
        # when no version is declared so the agent must actually run the
        # script. (Known future versions still degrade gracefully below.)
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                "verification-plan.json — missing `schemaVersion`. "
                "The file is hand-written; declared checks would be silently ignored.",
                fix=vp_fix,
            )
        ]
    if schema_version != 1:
        return [
            CheckResult(
                "verification-plan.json",
                "warn",
                f"verification-plan.json — schemaVersion {schema_version!r} not supported; ignoring",
            )
        ]

    if "requiredChecks" not in plan:
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                "verification-plan.json — missing `requiredChecks` key "
                "(wrong schema; required by verification-plan.sh output).",
                fix=vp_fix,
            )
        ]
    checks = plan.get("requiredChecks") or []
    if not isinstance(checks, list):
        return [
            CheckResult(
                "verification-plan.json",
                "fail",
                f"verification-plan.json — `requiredChecks` must be a list, "
                f"got {type(checks).__name__}.",
                fix=vp_fix,
            )
        ]
    if not checks:
        # Empty list is rare-but-legitimate (static site with no JS/scroll
        # signals). Surface it as a warn so the operator sees that NO
        # site-specific checks fired, rather than silently passing.
        return [
            CheckResult(
                "verification-plan.json",
                "warn",
                "verification-plan.json — `requiredChecks` is empty "
                "(verification-plan.sh detected no site-specific checks).",
            )
        ]

    # Two-phase mode (option A) — when UI_CLONE_PHASE=rapid the
    # agent is in initial visual-iteration mode. Non-anti-cheat
    # block checks are downgraded to warn so the agent can build
    # quickly and iterate visually first. Anti-cheat gates and
    # the must-stay-strict set remain block regardless.
    #
    # Promotion to strict: set UI_CLONE_PHASE=strict (default)
    # before declaring done. The strict run enforces every block.
    import os as _os

    phase = (_os.environ.get("UI_CLONE_PHASE") or "strict").lower()
    strict_warnings = (
        str(plan.get("strictWarnings", "")).lower() in {"1", "true", "yes", "on"}
        or (_os.environ.get("UI_CLONE_STRICT_WARNINGS") or "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    STRICT_WARNING_IDS = {
        # Advisory in fast iteration, blocking in release/strict closeout when
        # the operator opts in. These checks explain real visual drift that can
        # otherwise hide behind an overall PASS.
        "tree-diff",
        "scroll-coverage",
        "keyframes-diff",
        "scroll-anim-temporal",
        "visual-fidelity-judge",
    }
    # Anti-cheat gates that ALWAYS stay block, even in rapid
    # mode — these catch cheating and must never downgrade.
    #
    STRICT_ALWAYS = {
        # Anti-cheat (block cheating regardless of phase).
        "ref-screenshot-asset",
        "invalidation",
        "scaffold-warn",
        "remote-asset-ref",
        "html-paste",
        "proxy-mirror-check",
        "blank-viewport",
        "hidden-children",
        "monolithic-impl",
        "entry-coherence",
        "text-fidelity-check",
        "dom-mirror-check",
        "required-media-coverage",
        "css-mirror",
        "runtime-dom-parity",
        "svg-dom-parity",
        "motion-coverage",
        "scroll-engine-parity",
        # Rendered UI copy is a product-fidelity invariant. A stale plan that
        # still labels this row warn must not let missing, malformed, stale, or
        # mismatched runtime text remain green. Proven bounded phase variance
        # continues to pass through the artifact validator below.
        "runtime-text-sequence",
        # runtime-image-validity — HTML-fallback-as-image is a
        # fundamental cheat (Vite serving index.html for missing
        # assets). Must stay strict.
        "runtime-image-validity",
        # geometry-sanity — a ballooned/collapsed page is content-coverage
        # failure, not a polish item; rapid-phase downgrade let a 1593px
        # shortfall ship (omx postmortem). Stays block in every phase.
        "geometry-sanity",
        # reveal-trigger — IO+overflow:hidden reveals that never
        # fire are a core completion-blocker (catches "stuck
        # reveal" patterns that visually show empty space where
        # ref has content). Must stay strict.
        "reveal-trigger",
        # transition-fires — the RUNTIME source-of-truth for motion
        # fidelity. Each transition-spec entry must produce a measured
        # runtime delta when its trigger is driven; class-name presence
        # is not motion. Anti-gaming, so it must never downgrade.
        "transition-fires",
        "transition-proof",
        # signature-effects-coverage — a declared signatureEffect or a
        # scrollScrub scale band (the #3 zoom) must be wired in impl;
        # declaring it then shipping it static is the gap this closes, so
        # it must keep blocking even in rapid phase.
        "signature-effects-coverage",
        # batch-8 ITEM 8 minor — the pixel-truth/provenance anti-cheat checks
        # must not downgrade to a warning in rapid phase: a visibility-spoof,
        # forged state-reveal, symmetric-dispersion, or junk-token bypass is a
        # cheat regardless of phase.
        "masked-region-static",
        "state-reveal",
        "alignment-parity",
        "junk-token",
    }

    out: list[CheckResult] = []
    impl_root: Path | None = None
    impl_root_resolved = False

    def _current_impl_root() -> Path | None:
        nonlocal impl_root, impl_root_resolved
        if not impl_root_resolved:
            impl_root = self._find_impl_root()
            impl_root_resolved = True
        return impl_root

    for entry in checks:
        if not isinstance(entry, dict):
            continue
        check_id = str(entry.get("id") or "?")
        produces = entry.get("produces")
        script = entry.get("script") or ""
        reason = entry.get("reason") or ""
        severity = entry.get("severity") or "block"
        # Fail closed for plans generated before runtime text became blocking.
        # This gate-level override also protects hand-authored/stale plans that
        # retain severity=warn.
        if check_id == "runtime-text-sequence":
            severity = "block"
        # Rapid-mode downgrade: block→warn for non-anti-cheat
        # checks so the agent can iterate visually without
        # consuming the iteration budget on fidelity gates.
        if phase == "rapid" and severity == "block" and check_id not in STRICT_ALWAYS:
            severity = "warn"
        if strict_warnings and severity == "warn" and check_id in STRICT_WARNING_IDS:
            severity = "block"

        if not produces:
            continue
        artifact = self.ref_dir / produces
        label = f"required: {check_id}"
        fix = f"Run: bash {script}" if script else ""

        def _resolved_issue(message: str, *, stale: bool = False) -> CheckResult:
            if severity == "warn":
                return CheckResult(label, "warn", message, stale=stale)
            return CheckResult(label, "fail", message, fix=fix, stale=stale)

        if not artifact.is_file():
            msg = (
                f"MISSING_ARTIFACT {check_id} — produces "
                f"{produces}. Reason: {reason}. "
                "Run scripts/verify/run-required-checks.sh "
                "<session> <ref-url> <impl-url> <ref-dir> to "
                "produce every missing required-check artifact "
                "in one shell call."
            )
            if severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))
            continue

        try:
            raw = artifact.read_text(encoding="utf-8")
        except OSError as e:
            msg = (
                f"{check_id} — artifact unreadable ({e}). "
                "Cannot verify; re-run the producing script."
            )
            if severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))
            continue

        # B1 staleness applies to every registered required check, not only the
        # subset whose JSON artifact also carries impl path provenance. This
        # runs before artifact-format handling so registered text checks receive
        # the same fingerprint enforcement as JSON checks.
        input_spec = get_check_inputs(check_id)
        try:
            check_impl_root = (
                _current_impl_root()
                if input_spec is not None and bool(input_spec.impl)
                else None
            )
            if (
                input_spec is not None
                and input_spec.impl
                and check_impl_root is None
            ):
                msg = (
                    f"{check_id} — input fingerprint unverifiable because no "
                    "implementation root resolves. Restore or identify the impl "
                    "tree, then run scripts/verify/run-required-checks.sh."
                )
                out.append(_resolved_issue(msg, stale=True))
                continue
            if input_spec is not None and _registered_check_is_stale(
                self.ref_dir,
                check_impl_root,
                check_id,
                artifact,
            ):
                msg = (
                    f"{check_id} — stale artifact. Declared inputs changed "
                    f"since {produces} was produced. Run "
                    "scripts/verify/run-required-checks.sh to refresh."
                )
                out.append(_resolved_issue(msg, stale=True))
                continue
        except OSError as exc:
            msg = (
                f"{check_id} — input fingerprint unverifiable ({exc}). "
                "Restore readable check evidence, then run "
                "scripts/verify/run-required-checks.sh."
            )
            out.append(_resolved_issue(msg, stale=True))
            continue

        # If artifact is JSON with a `status` field, enforce status: "pass".
        # Non-JSON artifacts (e.g. transitions/result.txt) are scanned for
        # ❌ FAIL markers — presence-only would let real failures slip past
        # this gate when section-compare's dedicated parser only watches
        # sections/result.txt, not transitions/result.txt.
        try:
            data = load_strict_json_text(raw)
        except (json.JSONDecodeError, ValueError):
            if check_id == "runtime-text-sequence":
                out.append(
                    _resolved_issue(
                        f"{check_id} — artifact is not strict JSON; malformed "
                        "JSON and non-finite values (NaN/Infinity) are invalid "
                        "runtime text evidence."
                    )
                )
                continue
            if check_id == "visual-fidelity-judge":
                out.append(
                    _resolved_issue(
                        f"{check_id} — artifact is not strict JSON; NaN and "
                        "Infinity are not valid fidelity scores."
                    )
                )
                continue
            if check_id == "hover-state-compare":
                partial_hover = hover_state_partial_result(self.ref_dir, raw)
                if partial_hover is not None:
                    partial_valid, partial_note = partial_hover
                    if partial_valid:
                        if "PARTIAL" in partial_note:
                            out.append(
                                CheckResult(
                                    label,
                                    "warn",
                                    f"{check_id} — {partial_note}",
                                )
                            )
                        else:
                            out.append(
                                CheckResult(
                                    label,
                                    "pass",
                                    f"{check_id} — {partial_note}",
                                )
                            )
                    else:
                        out.append(
                            _resolved_issue(
                                f"{check_id} — semantically invalid partial "
                                f"hover evidence ({partial_note})."
                            )
                        )
                    continue
            if check_id == "transition-compare":
                transition_valid, transition_note = (
                    transition_compare_text_result(
                        raw,
                        allow_empty=self._transition_spec_count() == 0,
                    )
                )
                if not transition_valid:
                    out.append(
                        _resolved_issue(
                            f"{check_id} — semantically invalid text evidence "
                            f"({transition_note})."
                        )
                    )
                elif "PARTIAL" in transition_note:
                    out.append(CheckResult(label, "warn", transition_note))
                else:
                    out.append(CheckResult(label, "pass", transition_note))
                continue
            fail_lines = sum(1 for line in raw.splitlines() if "❌" in line)
            if fail_lines > 0:
                if check_id in {"video-motion-compare", "hover-state-compare", "hover-tree-diff"} and _only_reset_skipped_transitions(self):
                    out.append(
                        CheckResult(
                            label,
                            "pass",
                            f"{check_id} (skipped: only reset-only hover specs; no motion expected)",
                        )
                    )
                    continue
                msg = f"{check_id} — {fail_lines} FAIL line(s) in {produces}. Reason: {reason}"
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))
            else:
                # transition-compare / video-motion-compare / hover-state /
                # keyframes-diff etc — when transition-spec declares any
                # transitions, the corresponding result.txt MUST contain
                # actual measurement rows (✅ or ❌). An empty / near-empty
                # text artifact is the "transition-compare never actually
                # ran" gaming pattern (vacuous PASS because no ❌ exists).
                if check_id in {
                    "transition-compare",
                    "video-motion-compare",
                    "hover-state-compare",
                    "hover-tree-diff",
                    "keyframes-diff",
                    "scroll-anim-temporal",
                }:
                    measurement_rows = sum(
                        1
                        for line in raw.splitlines()
                        if ("✅" in line or "❌" in line) and "result:" not in line.lower()
                    )
                    spec_has_transitions = self._transition_spec_count() > 0
                    if spec_has_transitions and measurement_rows == 0:
                        msg = (
                            f"{check_id} — {produces} contains 0 measurement rows "
                            f"(no ✅/❌ lines) despite transition-spec.json declaring "
                            f"{self._transition_spec_count()} transition(s). The check "
                            f"didn't actually run."
                        )
                        if severity == "warn":
                            out.append(CheckResult(label, "warn", msg))
                        else:
                            out.append(CheckResult(label, "fail", msg, fix=fix))
                        continue
                # transition-trajectory is planned ONLY when scroll-scrub or
                # IO-reveal motion is declared, so reaching it here means motion
                # was declared. Its summary line ("✅ all N sample points…") and
                # the legit no-scroll skip line both carry ✅, so the generic
                # counter above miscounts — count TABLE rows only. Zero sample
                # rows with no genuine no-scroll skip is the "trajectory never
                # measured" vacuous PASS; fail closed (block severity by default).
                if check_id == "transition-trajectory":
                    no_scroll_skip = ("no-scroll page" in raw) or (
                        "neither page scrolls" in raw
                    )
                    table_rows = sum(
                        1
                        for line in raw.splitlines()
                        if line.lstrip().startswith("|")
                        and "%" in line
                        and ("✅" in line or "❌" in line)
                    )
                    if not no_scroll_skip and table_rows == 0:
                        msg = (
                            f"{check_id} — {produces} has 0 sample-point rows "
                            "despite scroll/IO motion being declared (this check "
                            "is only planned when scroll-scrub or IO-reveal is "
                            "present). The scroll trajectory was never measured; "
                            "a vacuous 'all 0 sample points' summary is not a pass."
                        )
                        if severity == "warn":
                            out.append(CheckResult(label, "warn", msg))
                        else:
                            out.append(CheckResult(label, "fail", msg, fix=fix))
                        continue
                out.append(
                    CheckResult(label, "pass", f"{check_id} (text artifact, no FAIL markers)")
                )
            continue

        # transition-trajectory's genuine artifact is ALWAYS #-prefixed markdown
        # (AE table, structural table, or the no-scroll skip line) — never valid
        # JSON. Reaching here means json.loads() SUCCEEDED, so the artifact is a
        # forge: a `{"status":"pass"}` / `{"status":"skip"}` / `{}` written to
        # dodge the text-branch sample-row + no-scroll-skip guards above. Fail
        # closed on any JSON-parsing trajectory artifact.
        if check_id == "transition-trajectory":
            msg = (
                f"{check_id} — {produces} parsed as JSON, but the genuine "
                "trajectory report is always a #-prefixed markdown table. A JSON "
                "artifact here bypasses the sample-row measurement scan (forged / "
                "vacuous pass); fail closed."
            )
            if severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))
            continue

        status = data.get("status") if isinstance(data, dict) else None
        if check_id == "font-parity" and status is None:
            # font-parity-check.sh predates the generic required-check status
            # contract and intentionally emits measured evidence as
            # {ref, impl, parity, capturedAt}. Reuse the canonical font-parity
            # gate here instead of treating that genuine producer schema as a
            # vacuous/status-less pass or duplicating its substitution and
            # FontFace-loading semantics.
            font_results = self.gate_font_parity()
            font_failures = [
                result for result in font_results if result.status == "fail"
            ]
            font_warnings = [
                result for result in font_results if result.status == "warn"
            ]
            if font_failures:
                details = "; ".join(result.message for result in font_failures)
                out.append(
                    _resolved_issue(
                        f"{check_id} — canonical font evidence failed: {details}"
                    )
                )
            elif font_warnings:
                details = "; ".join(result.message for result in font_warnings)
                out.append(CheckResult(label, "warn", details))
            else:
                out.append(
                    CheckResult(
                        label,
                        "pass",
                        "font-parity (canonical producer evidence passed)",
                    )
                )
            continue
        if check_id == "visual-fidelity-judge":
            semantic_error = visual_fidelity_semantic_error(data)
            if semantic_error is not None:
                out.append(
                    _resolved_issue(
                        f"{check_id} — semantically invalid artifact "
                        f"({semantic_error}). Re-run the visual judge."
                    )
                )
                continue
        if check_id == "transition-proof":
            semantic_error = transition_proof_semantic_error(self.ref_dir, data)
            if semantic_error is not None:
                out.append(
                    _resolved_issue(
                        f"{check_id} — semantically invalid composite proof "
                        f"({semantic_error}). Re-run transition-proof-rollup.sh "
                        "through run-required-checks.sh."
                    )
                )
                continue
        # tree-diff floor — a `status=pass` with `elements_walked` below
        # the floor is the 5199dd9 gaming pattern: agent ships a near-
        # empty impl (11 elements walked vs ref's 200) and tree-diff
        # vacuously passes "0 critical mismatches" because there's
        # nothing to mismatch. Floor cross-references section-map.json
        # so a 4-section page doesn't trip the gate.
        if check_id == "tree-diff" and status == "pass" and isinstance(data, dict):
            walked = int(data.get("elements_walked") or 0)
            floor = self._tree_diff_floor()
            if walked < floor:
                msg = (
                    f"tree-diff — only {walked} elements walked (floor: {floor}). "
                    f"This is the 'near-empty impl' gaming pattern: with so few "
                    f"elements to pair, tree-diff vacuously reports 0 mismatches. "
                    f"Generate real impl content."
                )
                out.append(CheckResult(label, "fail", msg, fix=fix))
                continue
            counts = data.get("counts") or {}
            if isinstance(counts, dict):
                unpaired = int(counts.get("unpaired") or 0)
                ok = int(counts.get("ok") or 0)
                if unpaired >= 3 and unpaired > ok:
                    msg = (
                        f"tree-diff — unpaired majority "
                        f"(unpaired={unpaired}, ok={ok}). "
                        "elementFromPoint pairing failed, so status=pass "
                        "is not convergence evidence. Fix DOM/layout "
                        "structure until most walked elements pair."
                    )
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                    continue
        #
        PATH_CHECK_IDS = {
            "impl-url-guard",
            "asset-transfer",
            "asset-utilization",
            "asset-placement",
            "image-fidelity",
            "proxy-mirror-check",
            "lottie-runtime",
            "bundle-impl-coverage",
            "ref-screenshot-asset",
            # Common cheat pattern A1/A2/A3 — all emit implRoot.
            "entry-coherence",
            "scaffold-residue",
            "html-paste",
            # Diagnosis B — required-media coverage emits implRoot.
            "required-media-coverage",
            # Common cheat pattern A4/A5 + fix #2 — css-mirror emits
            # implRoot, runtime-dom-parity and hidden-children
            # are URL-based (no implRoot path to validate, but
            # listing here makes intent explicit; the PATH_CHECK
            # block is skipped when the recorded path field is
            # absent so this is safe).
            "css-mirror",
            # Signal 1 — scaffold-warn placeholders (impl source scan).
            "scaffold-warn",
            # validation run findings — monolithic-impl + motion-coverage
            # both emit implRoot for cross-loop protection.
            "monolithic-impl",
            "motion-coverage",
            "scroll-engine-parity",
            # review-1 MINOR 4 — junk-token emits implSrcDir so its scan
            # target joins path/staleness validation.
            "junk-token",
        }
        # hover-fallback and state-reveal join the path-check machinery via their
        # `produces` name (batch-7 ITEM 4b / batch-9 ITEM 3): their live-scan
        # receipt is bound to impl_root + mtime exactly like junk-token's
        # implSrcDir, so a self-attested env flag (or a hand-authored artifact
        # claiming runtimeScanned) can no longer mint a pass.
        is_path_checked = check_id in PATH_CHECK_IDS or produces in (
            "hover-fallback.json",
            "state-reveal.json",
        )
        if is_path_checked and isinstance(data, dict) and status == "pass":

            def _nz(v: object) -> str | None:
                if isinstance(v, str) and v.strip():
                    return v
                return None

            recorded = (
                _nz(data.get("implPublicDir"))
                or _nz(data.get("implSrcDir"))
                or _nz(data.get("implDir"))
                or _nz(data.get("implRoot"))
                or _nz(data.get("implPkgJson"))
                or _nz(data.get("scanReceipt"))
            )
            impl_root = _current_impl_root()
            # batch-6 ITEM 5(c): a junk-token pass that claims a real runtime
            # scan must bind to a resolvable impl tree. When NO impl_root
            # resolves (a standalone ref dir), the entire path/staleness
            # validation below is skipped — so a forged status=pass /
            # runtimeScanned=true artifact would sail through. Require the
            # impl_root for the pass to count.
            if (
                (
                    check_id == "junk-token"
                    or produces in ("hover-fallback.json", "state-reveal.json")
                )
                and impl_root is None
                and isinstance(data, dict)
                and data.get("runtimeScanned") is True
            ):
                out.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{check_id} — artifact reports a runtime scan "
                        "(runtimeScanned=true) but no impl tree resolves, so the "
                        "scan cannot be bound to the active impl: a forged pass "
                        "with no discoverable impl_root must not stand. Run "
                        "against the active loop's impl tree.",
                        fix=fix,
                    )
                )
                continue
            if impl_root is not None and recorded is None:
                msg = (
                    f"{check_id} — path-check artifact must emit "
                    "implRoot/implDir/implSrcDir/implPublicDir/"
                    "implPkgJson. None present, so cross-loop "
                    "contamination cannot be ruled out. Re-run the "
                    "check (newer scripts emit the field)."
                )
                out.append(CheckResult(label, "fail", msg, fix=fix))
                continue
            if recorded and impl_root is not None:
                rec_path = Path(str(recorded)).resolve()
                impl_resolved = impl_root.resolve()
                expected_roots = {
                    impl_resolved,
                    (impl_root / "public").resolve(),
                    (impl_root / "src").resolve(),
                }
                if rec_path not in expected_roots:
                    try:
                        rec_path.relative_to(impl_resolved)
                        recorded_inside = True
                    except ValueError:
                        recorded_inside = False
                    if not recorded_inside:
                        msg = (
                            f"{check_id} — loop path contamination. "
                            f"artifact recorded {recorded}, but current "
                            f"impl_root is {impl_root}. Run the check "
                            "against the active loop's impl tree."
                        )
                        out.append(CheckResult(label, "fail", msg, fix=fix, stale=True))
                        continue
                # Unregistered path checks retain the conservative legacy
                # fallback. Registered checks were already fingerprinted above,
                # independently of whether their artifact records path
                # provenance.
                try:
                    artifact_path = self.ref_dir / produces
                    if artifact_path.is_file() and input_spec is None:
                        stale = _legacy_newest_mtime(impl_root) > (
                            artifact_path.stat().st_mtime + 1.0
                        )
                        if stale:
                            msg = (
                                f"{check_id} — stale artifact. Implementation "
                                f"inputs changed since {produces} was produced. Run "
                                "scripts/verify/run-required-checks.sh to refresh."
                            )
                            out.append(
                                CheckResult(label, "fail", msg, fix=fix, stale=True)
                            )
                            continue
                except OSError:
                    pass
        STATUS_REQUIRED = {
            "capacity-probe",
            "impl-url-guard",
            "asset-transfer",
            "asset-utilization",
            "asset-placement",
            "image-fidelity",
            "font-parity",
            "dom-mirror-check",
            "text-fidelity",
            "hydration-check",
            "transition-spec-coverage",
            "spec-implementation-coverage",
            "runtime-spec-coverage",
            "tree-diff",
            "scroll-end-completion",
            "reveal-trigger",
            "transition-fires",
            "boundary",
            "tailwind-transform-conflict",
            "proxy-mirror-check",
            "lottie-runtime",
            "bundle-impl-coverage",
            "scroll-coverage",
            "live-parity-sweep",
            "runtime-image-validity",
            "remote-asset-ref",
            "capture-artifact-inventory",
            "ref-screenshot-asset",
            # geometry-sanity — a status-less artifact vacuously passed
            # (omx postmortem); the evaluate() result always carries status.
            "geometry-sanity",
            # alignment-parity — inner-content horizontal alignment
            # (specific regression footer carousel off-center while section rects and
            # AE crops passed); status-less artifact must not pass.
            "alignment-parity",
            # junk-token — serialization junk in class/id/src/alt/style
            # (specific regression nav dots carried a literal "undefined" class).
            "junk-token",
            # alignment-sweep — invariant transfer to intermediate widths.
            "alignment-sweep",
            # masked-region-motion — live motion proof for dynamic-masked
            # timer/carousel regions (their only verification).
            "masked-region-motion",
            # dynamic-behavior-parity — runtime transition/carousel behavior
            # proof. A status-less JSON artifact can otherwise pass without
            # recording that the producer actually judged the fingerprints.
            "dynamic-behavior-parity",
            # masked-region-static — static-style parity for dynamic-masked
            # regions (specific regression eatReal h2 lost text-align:center under the
            # mask; motion proof checks MOTION only).
            "masked-region-static",
            # state-reveal — active-state (scroll) reveal end-state proof
            # (specific regression nav active label stayed width:0 on scroll; hover
            # fallback only covers hover-triggered reveals).
            "state-reveal",
            # Common cheat pattern A1/A2/A3 anti-cheat — entry-coherence
            # (stack/entry consistency), scaffold-residue (orphan
            # components), html-paste (structural/script/CSS theft).
            "entry-coherence",
            "scaffold-residue",
            "html-paste",
            # Diagnosis B — required-media (video/Lottie) coverage.
            "required-media-coverage",
            # Common cheat pattern A4/A5 + fix #2 anti-cheat —
            # css-mirror (static), runtime-dom-parity (runtime
            # positive parity), hidden-children (runtime hidden
            # DOM with screenshot background overlay).
            "css-mirror",
            "blank-viewport",
            "runtime-dom-parity",
            "hidden-children",
            "invalidation",
            # Signal 1 — scaffold-warn placeholders.
            "scaffold-warn",
            "svg-dom-parity",
            # validation run findings — monolithic-impl + motion-coverage.
            "monolithic-impl",
            "motion-coverage",
            "scroll-engine-parity",
            "runtime-text-sequence",
            # batch-8 ITEM 8 minor — a status-less hover-fallback artifact must
            # not vacuously pass: the hover provenance branch only fires on
            # status==pass, so an absent status would otherwise fall through to
            # the no-status pass.
            "hover-fallback",
        }
        # Review-1 MAJOR 3 — junk-token coverage honesty: the runtime DOM
        # scan is half the gate (template-string junk only materializes
        # live). A pass artifact whose runtime scan never ran is incomplete
        # coverage, not a pass.
        if (
            check_id == "junk-token"
            and str(status).lower() == "pass"
            and isinstance(data, dict)
            and data.get("runtimeScanned") is not True
        ):
            out.append(
                CheckResult(
                    label,
                    "fail",
                    f"{check_id} — artifact reports pass but the runtime DOM "
                    "scan never ran (runtimeScanned=false): coverage is "
                    "static-only. Re-run junk-token-check.sh with a reachable "
                    "impl URL.",
                    fix=fix,
                )
            )
            continue
        # batch-6 ITEM 4 — hover-fallback provenance: a pass artifact must rest
        # on a real live hover scan. A forged/replayed hover-fallback.json
        # (runtimeScanned=false) is fabricated evidence, not a pass.
        if (
            produces == "hover-fallback.json"
            and str(status).lower() == "pass"
            and isinstance(data, dict)
            and data.get("runtimeScanned") is not True
        ):
            out.append(
                CheckResult(
                    label,
                    "fail",
                    f"{check_id} — hover-fallback reports pass but no live hover "
                    "scan ran (runtimeScanned=false): coverage cannot rest on "
                    "fabricated or replayed samples. Re-run hover-fallback-probe.sh "
                    "against a reachable impl URL.",
                    fix=fix,
                )
            )
            continue
        # batch-7 ITEM 4 — state-reveal provenance + threshold band. A pass must
        # rest on a live state-sweep (A4: a hand-authored artifact has no
        # runtimeScanned), and the EFFECTIVE thresholds it recorded must be in
        # the allowed band (A5: env-tuned RATIO=0.01 / MIN_CONTENT=100 cannot
        # mint a pass even if the artifact claims it).
        if produces == "state-reveal.json" and str(status).lower() == "pass" and isinstance(data, dict):
            if data.get("runtimeScanned") is not True:
                out.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{check_id} — state-reveal reports pass but no live "
                        "state-sweep ran (runtimeScanned=false): a hand-authored "
                        "artifact cannot mint coverage. Re-run "
                        "state-reveal-proof-check.sh against a reachable impl URL.",
                        fix=fix,
                    )
                )
                continue
            # batch-8 ITEM 8 — fail-CLOSED threshold enforcement. A pass MUST
            # record BOTH effective thresholds as real numbers in band. Absent or
            # non-numeric (e.g. a stringified "0.01" that dodges the isinstance
            # check) is rejected: the genuine producer always emits numeric
            # fields, so omitting/stringifying them is a forge tell the consumer
            # must not fail open on.
            def _band_num(v: object) -> float | None:
                if isinstance(v, bool):  # JSON bool is an int subclass — exclude
                    return None
                if isinstance(v, int | float):
                    return float(v)
                return None

            eff_ratio = _band_num(data.get("effectiveRevealRatio"))
            eff_min = _band_num(data.get("effectiveMinContentPx"))
            if eff_ratio is None or eff_min is None:
                out.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{check_id} — state-reveal pass must record numeric "
                        "effectiveRevealRatio and effectiveMinContentPx; one or "
                        "both are absent or non-numeric "
                        f"(effectiveRevealRatio={data.get('effectiveRevealRatio')!r}, "
                        f"effectiveMinContentPx={data.get('effectiveMinContentPx')!r}): "
                        "a forged artifact cannot omit or stringify the band-"
                        "validated thresholds. Re-run state-reveal-proof-check.sh "
                        "against a reachable impl URL.",
                        fix=fix,
                    )
                )
                continue
            out_of_band = not (0.4 <= eff_ratio <= 0.95) or not (4.0 <= eff_min <= 40.0)
            if out_of_band:
                out.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{check_id} — state-reveal effective threshold out of "
                        f"band (effectiveRevealRatio={eff_ratio} must be in "
                        f"[0.4,0.95], effectiveMinContentPx={eff_min} in [4,40]); "
                        "threshold tampered, artifact rejected.",
                        fix=fix,
                    )
                )
                continue
        if check_id == "runtime-text-sequence" and isinstance(data, dict):
            ref_capture = data.get("ref")
            impl_capture = data.get("impl")
            comparison = data.get("comparison")

            def _valid_capture(value: object) -> bool:
                if not isinstance(value, dict):
                    return False
                block_count = value.get("blockCount")
                blocks = value.get("blocks")
                return (
                    isinstance(block_count, int)
                    and isinstance(blocks, list)
                    and block_count == len(blocks)
                    and all(isinstance(item, str) for item in blocks)
                )

            valid_shape = (
                data.get("schemaVersion") == 1
                and str(status).lower() in {"pass", "fail", "error"}
                and _valid_capture(ref_capture)
                and _valid_capture(impl_capture)
                and isinstance(comparison, dict)
                and isinstance(comparison.get("lcsLength"), int)
                and isinstance(comparison.get("missingCount"), int)
                and isinstance(comparison.get("extraCount"), int)
                and isinstance(comparison.get("orderedSimilarity"), int | float)
                and isinstance(comparison.get("missingRatio"), int | float)
                and isinstance(data.get("violations"), list)
            )
            if not valid_shape:
                msg = (
                    f"{check_id} — malformed artifact. Expected schemaVersion=1, "
                    "a declared pass/fail/error status, ref/impl block arrays with "
                    "matching counts, comparison counts, and a violations list."
                )
                out.append(_resolved_issue(msg))
                continue
            semantic_error = _runtime_text_semantic_error(data)
            if semantic_error is not None:
                msg = (
                    f"{check_id} — semantically inconsistent artifact "
                    f"({semantic_error}). Re-run the browser-backed producer; "
                    "producer-authored pass metrics are not trusted."
                )
                out.append(_resolved_issue(msg))
                continue
            if str(status).lower() == "pass":
                provenance_error = _runtime_text_provenance_error(
                    self.ref_dir, artifact, data
                )
                if provenance_error is not None:
                    msg = (
                        f"{check_id} — dispatcher provenance is missing or stale "
                        f"({provenance_error}). Re-run run-required-checks.sh "
                        "against the canonical ref and impl URLs."
                    )
                    out.append(_resolved_issue(msg))
                    continue

        if status == "pass":
            out.append(CheckResult(label, "pass", f"{check_id} (status: pass)"))
        elif status is None:
            if check_id in STATUS_REQUIRED:
                msg = (
                    f"{check_id} — artifact present but `status` field "
                    "is absent. Known checks must declare status; missing "
                    "status is the audit incident 'check produced JSON but never "
                    "ran the assertion' gaming pattern."
                )
                out.append(_resolved_issue(msg))
                continue
            out.append(
                CheckResult(label, "pass", f"{check_id} (artifact present, no status field)")
            )
        elif str(status).lower() == "skip":
            # A check reports skip when its prerequisites aren't met
            # (no signal in ref, below floor, gate does not apply).
            # That's a no-op verdict, not a block — treat as pass with
            # the skip reason preserved so it's visible in the gate
            # output. Setup-error skips ("impl_root not found",
            # "agent-browser missing") would otherwise have already
            # failed earlier in run-required-checks.sh.
            # Fail-closed on OBSERVED-but-UNMEASURED scroll-linked motion. The
            # producer tags infra skips (impl unreachable / no compare rows /
            # capture failed) with skipClass="infra", distinct from page-shape
            # skips (short/static page, redundant coverage) and config skips
            # (no orig-url). Escalate ONLY when the runtime dump proves real
            # scroll-linked transforms exist (_observed_scroll_linked_motion —
            # not the over-broad plan hasScrollScrub, which a mere Lenis/smooth-
            # scroll lib flips true) AND the impl was confirmed up (runtime-env
            # passed): then an infra skip means observed motion went unmeasured.
            # Respects severity: warn in fast iteration, block in strict closeout
            # (scroll-coverage ∈ STRICT_WARNING_IDS).
            if (
                check_id == "scroll-coverage"
                and isinstance(data, dict)
                and str(data.get("skipClass") or "").lower() == "infra"
                and _runtime_env_passed(self.ref_dir)
                and _observed_scroll_linked_motion(self.ref_dir)
            ):
                msg = (
                    f"{check_id} — scroll-linked motion was OBSERVED "
                    "(animation-runtime-dump.json scrollLinkedStyles) but scroll "
                    f"coverage could not be measured (infra skip: {reason}). This "
                    "leaves motion fidelity UNVERIFIED. Bring the impl up and "
                    "re-run so the scroll sweep can measure it."
                )
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                continue
            skip_msg = f"{check_id} (skipped: {reason})" if reason else f"{check_id} (skipped)"
            out.append(CheckResult(label, "pass", skip_msg))
        else:
            count = (
                (
                    data.get("errorCount")
                    or data.get("failureCount")
                    or data.get("totalStuck")
                    or "?"
                )
                if isinstance(data, dict)
                else "?"
            )
            msg = f"{check_id} — status: {status} ({count} issue(s)). Reason: {reason}"
            if str(status).lower() == "warn":
                out.append(CheckResult(label, "warn", msg))
            elif severity == "warn":
                out.append(CheckResult(label, "warn", msg))
            else:
                out.append(CheckResult(label, "fail", msg, fix=fix))

    return out



def _only_reset_skipped_transitions(self: Gate) -> bool:
    """True when transition checks prove every spec entry is a no-op reset."""

    fires_path = self.ref_dir / "transition-fires.json"
    spec_impl_path = self.ref_dir / "spec-implementation-coverage.json"
    if not fires_path.is_file():
        return False
    try:
        fires = json.loads(fires_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(fires, dict) or fires.get("status") != "pass":
        return False

    def _int(d: dict, key: str) -> int:
        try:
            return int(d.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    total = _int(fires, "total")
    if total <= 0:
        return False
    if _int(fires, "failed") or _int(fires, "unmeasurable") or _int(fires, "fired"):
        return False
    if _int(fires, "known_skip") != total:
        return False

    if spec_impl_path.is_file():
        try:
            spec_impl = json.loads(spec_impl_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if isinstance(spec_impl, dict) and _int(spec_impl, "total") != 0:
            return False
    return True

def _transition_spec_count(self: Gate) -> int:
    """Number of declared transitions in transition-spec.json.

    Used to gate "transition-compare must have measurement rows" — only
    applies when the spec actually declared transitions to compare.
    """
    spec = self.ref_dir / "transition-spec.json"
    if not spec.is_file():
        return 0
    try:
        data = json.loads(spec.read_text(encoding="utf-8"))
        transitions = data.get("transitions") if isinstance(data, dict) else None
        if isinstance(transitions, list):
            return len(transitions)
    except (json.JSONDecodeError, OSError):
        pass
    return 0


def _tree_diff_floor(self: Gate) -> int:
    """Minimum elements_walked tree-diff must achieve to be meaningful.

    Cross-reference section-map.json. The floor is max(30, sections * 5):
    a real page averages ≥5 visible elements per section (heading, sub-
    head, paragraph, button, image at minimum). Below 30 absolute, any
    page is too sparse to call tree-diff a real measurement.
    """
    section_map = self.ref_dir / "section-map.json"
    section_count = 0
    if section_map.is_file():
        try:
            data = json.loads(section_map.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                sections = data.get("sections") or []
                if isinstance(sections, list):
                    section_count = len(sections)
            elif isinstance(data, list):
                section_count = len(data)
        except (json.JSONDecodeError, OSError):
            section_count = 0
    return max(30, section_count * 5)
