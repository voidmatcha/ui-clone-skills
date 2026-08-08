from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from array import array
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ref_path = Path(sys.argv[1])
impl_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])
ref_url = sys.argv[4]
impl_url = sys.argv[5]

MIN_ORDERED_SIMILARITY = 0.85
MAX_MISSING_RATIO = 0.15
ZERO_WIDTH_NOISE = re.compile("[\u200b\u2060]")
WHITESPACE = re.compile(r"\s+")
CJK_BOUNDARY_WHITESPACE = re.compile(
    r"(?<=[\u2e80-\u9fff\uac00-\ud7af])\s+"
    r"|\s+(?=[\u2e80-\u9fff\uac00-\ud7af])"
)


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = ZERO_WIDTH_NOISE.sub("", text).replace("\u00a0", " ")
    return WHITESPACE.sub(" ", text).strip()


def comparison_text(value: Any) -> str:
    return CJK_BOUNDARY_WHITESPACE.sub("", normalize(value))


def canonical_http_url(value: Any) -> str | None:
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


def parse_browser_result(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return {"error": "empty browser result"}
    value: Any = raw
    for _ in range(3):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            break
    if not isinstance(value, dict):
        return {"error": "unparseable browser result", "raw": raw[:300]}
    if "error" in value:
        return value
    blocks = value.get("blocks")
    if not isinstance(blocks, list):
        return {"error": "browser result is missing blocks"}
    normalized_blocks = [
        text for item in blocks if (text := normalize(item))
    ]

    def normalize_record(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        text = normalize(item.get("text"))
        slot = item.get("slot")
        tag = item.get("tag")
        initial = item.get("initialViewport")
        if (
            not text
            or not isinstance(slot, str)
            or not slot
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

    records_raw = value.get("records")
    records = (
        [record for item in records_raw if (record := normalize_record(item))]
        if isinstance(records_raw, list)
        else []
    )
    if len(records) != len(normalized_blocks) or [
        item["text"] for item in records
    ] != normalized_blocks:
        return {"error": "browser result records do not match blocks"}
    if len({item["slot"] for item in records}) != len(records):
        return {"error": "browser result contains duplicate final slots"}

    samples = []
    samples_raw = value.get("samples")
    if isinstance(samples_raw, list):
        for sample_raw in samples_raw:
            if not isinstance(sample_raw, list):
                continue
            sample = [
                record
                for item in sample_raw
                if (record := normalize_record(item))
            ]
            if len(sample) == len(sample_raw):
                samples.append(sample)
    if not samples:
        return {"error": "browser result is missing valid samples"}
    if any(
        len({item["slot"] for item in sample}) != len(sample)
        for sample in samples
    ):
        return {"error": "browser result contains duplicate sample slots"}
    if samples[-1] != records:
        return {"error": "browser result final sample does not match records"}
    phase_start = value.get("phaseSampleStartIndex")
    if (
        type(phase_start) is not int
        or phase_start < 0
        or phase_start > len(samples) - 2
    ):
        return {"error": "browser result has invalid phase sample window"}
    actual_url = canonical_http_url(value.get("actualUrl"))
    capture_receipt = value.get("captureReceipt")
    if actual_url is None or not isinstance(capture_receipt, dict):
        return {"error": "browser result is missing URL capture evidence"}
    return {
        "blocks": normalized_blocks,
        "records": records,
        "samples": samples,
        "phaseSampleStartIndex": phase_start,
        "actualUrl": actual_url,
        "captureReceipt": capture_receipt,
    }


def lcs_alignment(
    ref: list[str], impl: list[str]
) -> tuple[int, list[int], list[int]]:
    rows = [array("I", [0]) * (len(impl) + 1)]
    for ref_item in ref:
        previous = rows[-1]
        current = array("I", [0]) * (len(impl) + 1)
        for j, impl_item in enumerate(impl, start=1):
            if ref_item == impl_item:
                current[j] = previous[j - 1] + 1
            else:
                current[j] = max(previous[j], current[j - 1])
        rows.append(current)

    ref_matches: list[int] = []
    impl_matches: list[int] = []
    i, j = len(ref), len(impl)
    while i and j:
        if ref[i - 1] == impl[j - 1]:
            ref_matches.append(i - 1)
            impl_matches.append(j - 1)
            i -= 1
            j -= 1
        elif rows[i - 1][j] >= rows[i][j - 1]:
            i -= 1
        else:
            j -= 1
    ref_matches.reverse()
    impl_matches.reverse()
    return rows[-1][-1], ref_matches, impl_matches


def confirm_phase_variance(
    ref_data: dict[str, Any],
    impl_data: dict[str, Any],
    ref_matches: list[int],
    impl_matches: list[int],
) -> dict[str, Any]:
    ref_records = ref_data["records"]
    impl_records = impl_data["records"]
    if len(ref_matches) != len(impl_matches):
        return {
            "accepted": False,
            "reason": "internal-lcs-match-length-mismatch",
            "refMatchCount": len(ref_matches),
            "implMatchCount": len(impl_matches),
        }
    boundaries = [
        (-1, -1),
        *zip(ref_matches, impl_matches),
        (len(ref_records), len(impl_records)),
    ]
    gaps = []
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
        return {"accepted": False, "reason": "no-phase-gaps"}

    protected_tags = {"H1", "H2", "H3"}
    proof = []

    def bounded_sample_gap(
        sample: list[dict[str, Any]],
        before_anchor: dict[str, Any] | None,
        after_anchor: dict[str, Any] | None,
    ) -> list[dict[str, Any]] | None:
        if before_anchor is None and after_anchor is None:
            return None
        def anchor_index(anchor: dict[str, Any] | None) -> int | None:
            if anchor is None:
                return None
            expected = (anchor["slot"], anchor["text"])
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

    def projected_anchor(anchor: dict[str, Any] | None) -> dict[str, str] | None:
        if anchor is None:
            return None
        return {"slot": anchor["slot"], "text": anchor["text"]}

    def projected_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "slot": record["slot"],
            "text": record["text"],
            "tag": record["tag"],
            "initialViewport": record["initialViewport"],
        }

    def slot_tail(record: dict[str, Any], depth: int) -> tuple[str, ...]:
        return tuple(record["slot"].split(">")[-depth:])

    def same_shape(
        ref_gap: list[dict[str, Any]],
        impl_gap: list[dict[str, Any]],
        *,
        tail_depth: int = 1,
    ) -> bool:
        return (
            len(ref_gap) == len(impl_gap)
            and all(
                ref_item["tag"] == impl_item["tag"]
                and slot_tail(ref_item, tail_depth)
                == slot_tail(impl_item, tail_depth)
                for ref_item, impl_item in zip(ref_gap, impl_gap)
            )
        )

    def phase_gap_states(
        capture: dict[str, Any],
        before_anchor: dict[str, Any] | None,
        after_anchor: dict[str, Any] | None,
        expected: list[dict[str, Any]],
    ) -> set[tuple[str, ...]] | None:
        states: set[tuple[str, ...]] = set()
        for sample in capture["samples"][
            capture["phaseSampleStartIndex"] :
        ]:
            observed = bounded_sample_gap(
                sample, before_anchor, after_anchor
            )
            if (
                observed is None
                or len(observed) != len(expected)
                or [item["tag"] for item in observed]
                != [item["tag"] for item in expected]
            ):
                return None
            states.add(tuple(comparison_text(item["text"]) for item in observed))
        return states

    def dynamic_region_proof(
        gap: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            len(gap["ref"]) < 2
            or not same_shape(gap["ref"], gap["impl"])
            or any(
                item["tag"] in protected_tags
                for item in [*gap["ref"], *gap["impl"]]
            )
        ):
            return None
        ref_states = phase_gap_states(
            ref_data,
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            gap["ref"],
        )
        impl_states = phase_gap_states(
            impl_data,
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
            gap["impl"],
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
            "recordCount": len(gap["ref"]),
            "referenceStateCount": len(ref_states),
            "implementationStateCount": len(impl_states),
        }

    def volatile_counter_proof(
        gap: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            len(gap["ref"]) != 1
            or len(gap["impl"]) != 1
            or not same_shape(gap["ref"], gap["impl"], tail_depth=3)
        ):
            return None
        ref_item = gap["ref"][0]
        impl_item = gap["impl"][0]
        if (
            ref_item["tag"] in protected_tags
            or not re.fullmatch(r"\d{1,4}", comparison_text(ref_item["text"]))
            or not re.fullmatch(r"\d{1,4}", comparison_text(impl_item["text"]))
        ):
            return None
        anchors = (
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
        )
        if any(anchor is None for anchor in anchors):
            return None
        anchor_texts = [
            comparison_text(anchor["text"])
            for anchor in anchors
            if anchor is not None
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
            "reference": projected_record(ref_item),
            "implementation": projected_record(impl_item),
        }

    def progressive_reveal_proof(
        gap: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            len(gap["ref"]) != 1
            or len(gap["impl"]) != 1
            or not same_shape(gap["ref"], gap["impl"], tail_depth=3)
        ):
            return None
        ref_item = gap["ref"][0]
        impl_item = gap["impl"][0]
        if (
            ref_item["initialViewport"]
            or impl_item["initialViewport"]
            or ref_item["tag"] in protected_tags
        ):
            return None
        ref_text = comparison_text(ref_item["text"])
        impl_text = comparison_text(impl_item["text"])
        if ref_text in impl_text:
            short_side, short_item, long_text = ref_data, ref_item, impl_text
        elif impl_text in ref_text:
            short_side, short_item, long_text = impl_data, impl_item, ref_text
        else:
            return None
        variants = {
            comparison_text(item["text"])
            for sample in short_side["samples"]
            for item in sample
            if item["slot"] == short_item["slot"]
        }
        if (
            len(variants) < 2
            or any(not variant or variant not in long_text for variant in variants)
        ):
            return None
        return {
            "kind": "progressive-reveal",
            "observedVariantCount": len(variants),
            "reference": projected_record(ref_item),
            "implementation": projected_record(impl_item),
        }

    def live_card_proof(
        gap: dict[str, Any],
    ) -> dict[str, Any] | None:
        disputed = [*gap["ref"], *gap["impl"]]
        anchors = (
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
        )
        if (
            len(gap["ref"]) < 2
            or not same_shape(gap["ref"], gap["impl"], tail_depth=3)
            or any(anchor is None for anchor in anchors)
            or any(
                anchor["tag"] not in {"A", "BUTTON"}
                for anchor in anchors
                if anchor is not None
            )
            or any(
                item["initialViewport"] or item["tag"] in protected_tags
                for item in disputed
            )
            or any(
                not any(character.isalpha() for character in item["text"])
                for item in disputed
            )
        ):
            return None
        ref_texts = {comparison_text(item["text"]) for item in gap["ref"]}
        impl_texts = {comparison_text(item["text"]) for item in gap["impl"]}
        if ref_texts & impl_texts:
            return None
        return {
            "kind": "live-card-region",
            "recordCount": len(gap["ref"]),
            "slotTailDepth": 3,
        }

    def side_recurrence(
        capture: dict[str, Any],
        before_anchor: dict[str, Any] | None,
        after_anchor: dict[str, Any] | None,
        candidate: dict[str, Any],
    ) -> dict[str, Any] | None:
        phase_start = capture["phaseSampleStartIndex"]
        states: list[tuple[int, str, dict[str, Any] | None]] = []
        for sample_index in range(phase_start, len(capture["samples"])):
            observed = bounded_sample_gap(
                capture["samples"][sample_index],
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
                and not observed[0]["initialViewport"]
                and observed[0]["tag"] not in protected_tags
            ):
                states.append((sample_index, "present", observed[0]))
                continue
            return None

        runs: list[dict[str, Any]] = []
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
            present_record = present_run["record"]
            if not isinstance(present_record, dict):
                return None
            return {
                "phaseSampleStartIndex": phase_start,
                "cyclePolarity": polarity,
                "candidatePresentSample": present_run["start"],
                "candidateAbsentStartSample": first_absent_run["start"],
                "candidateRecurredSample": last["start"],
                "absenceRunLength": (
                    first_absent_run["end"] - first_absent_run["start"] + 1
                ),
                "candidate": projected_record(present_record),
            }
        return None

    legacy_gap_count = sum(
        bool(gap["ref"]) != bool(gap["impl"]) for gap in gaps
    )
    if legacy_gap_count > 2:
        return {
            "accepted": False,
            "reason": "too-many-phase-gaps",
            "gapCount": len(gaps),
        }

    for gap_index, gap in enumerate(gaps):
        if gap["ref"] and gap["impl"]:
            substitution_proof = (
                dynamic_region_proof(gap)
                or volatile_counter_proof(gap)
                or progressive_reveal_proof(gap)
                or live_card_proof(gap)
            )
            if substitution_proof is None:
                return {
                    "accepted": False,
                    "reason": "unproven-slot-variance",
                    "gapIndex": gap_index,
                }
            proof.append({"gapIndex": gap_index, **substitution_proof})
            continue
        disputed = [*gap["ref"], *gap["impl"]]
        if (len(gap["ref"]), len(gap["impl"])) not in {(1, 0), (0, 1)}:
            return {
                "accepted": False,
                "reason": "phase-gap-is-not-singleton",
                "gapIndex": gap_index,
            }
        if any(
            item["initialViewport"] or item["tag"] in protected_tags
            for item in disputed
        ):
            return {
                "accepted": False,
                "reason": "protected-copy-disputed",
                "gapIndex": gap_index,
            }

        candidate_side = "ref" if gap["ref"] else "impl"
        candidate_record = disputed[0]
        ref_recurrence = side_recurrence(
            ref_data,
            gap["refBeforeAnchor"],
            gap["refAfterAnchor"],
            candidate_record,
        )
        if ref_recurrence is None:
            return {
                "accepted": False,
                "reason": "reference-phase-nonrecurrent",
                "gapIndex": gap_index,
            }
        impl_recurrence = side_recurrence(
            impl_data,
            gap["implBeforeAnchor"],
            gap["implAfterAnchor"],
            candidate_record,
        )
        if impl_recurrence is None:
            return {
                "accepted": False,
                "reason": "implementation-phase-nonrecurrent",
                "gapIndex": gap_index,
            }

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
        proof.append({
            "gapIndex": gap_index,
            "beforeSlot": (
                candidate_before["slot"] if candidate_before else None
            ),
            "afterSlot": (
                candidate_after["slot"] if candidate_after else None
            ),
            "beforeAnchor": projected_anchor(candidate_before),
            "afterAnchor": projected_anchor(candidate_after),
            "refBeforeAnchor": projected_anchor(gap["refBeforeAnchor"]),
            "refAfterAnchor": projected_anchor(gap["refAfterAnchor"]),
            "implBeforeAnchor": projected_anchor(gap["implBeforeAnchor"]),
            "implAfterAnchor": projected_anchor(gap["implAfterAnchor"]),
            "candidateSide": candidate_side,
            "candidate": projected_record(candidate_record),
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
            "referenceAbsenceRunLength": (
                ref_recurrence["absenceRunLength"]
            ),
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
        })
    return {
        "accepted": True,
        "advisory": "bounded rendered phase variance confirmed",
        "gapCount": len(gaps),
        "proof": proof,
        "referenceSampleCount": len(ref_data["samples"]),
        "implementationSampleCount": len(impl_data["samples"]),
    }


ref_data = parse_browser_result(ref_path)
impl_data = parse_browser_result(impl_path)
violations: list[dict[str, Any]] = []

if "error" in ref_data:
    ref_error_kind = ref_data.get("kind")
    violations.append({
        "kind": (
            "empty-ref-capture"
            if ref_error_kind == "empty-capture"
            else (
                "ref-url-mismatch"
                if ref_error_kind == "url-mismatch"
                else (
                    "ref-http-error"
                    if ref_error_kind == "http-error"
                    else "ref-browser-failed"
                )
            )
        ),
        "detail": ref_data.get("detail") or ref_data["error"],
        "attempts": ref_data.get("attempts"),
    })
if "error" in impl_data:
    impl_error_kind = impl_data.get("kind")
    violations.append({
        "kind": (
            "empty-impl-capture"
            if impl_error_kind == "empty-capture"
            else (
                "impl-url-mismatch"
                if impl_error_kind == "url-mismatch"
                else (
                    "impl-http-error"
                    if impl_error_kind == "http-error"
                    else "impl-browser-failed"
                )
            )
        ),
        "detail": impl_data.get("detail") or impl_data["error"],
        "attempts": impl_data.get("attempts"),
    })
canonical_ref_url = canonical_http_url(ref_url)
canonical_impl_url = canonical_http_url(impl_url)
if canonical_ref_url is None or canonical_impl_url is None:
    violations.append({
        "kind": "invalid-requested-url",
        "detail": "reference and implementation URLs must be valid HTTP(S) URLs",
    })
elif canonical_ref_url == canonical_impl_url:
    violations.append({
        "kind": "ref-impl-url-collision",
        "detail": (
            "reference and implementation captures resolve to the same "
            "requested URL/route"
        ),
    })
browser_ok = not violations

ref_blocks = ref_data.get("blocks", [])
impl_blocks = impl_data.get("blocks", [])
if browser_ok and not ref_blocks:
    violations.append({
        "kind": "empty-ref-capture",
        "detail": "reference capture produced zero visible text blocks",
    })
if browser_ok and not impl_blocks:
    violations.append({
        "kind": "empty-impl-capture",
        "detail": "implementation capture produced zero visible text blocks",
    })
capture_ok = not violations
lcs_length = 0
ref_matches: list[int] = []
impl_matches: list[int] = []
ref_comparison_blocks = [comparison_text(text) for text in ref_blocks]
impl_comparison_blocks = [comparison_text(text) for text in impl_blocks]
canonical_blocks_equal = ref_comparison_blocks == impl_comparison_blocks

if capture_ok:
    lcs_length, ref_matches, impl_matches = lcs_alignment(
        ref_comparison_blocks, impl_comparison_blocks
    )

ref_match_set = set(ref_matches)
impl_match_set = set(impl_matches)
missing = [
    {"index": index, "text": text}
    for index, text in enumerate(ref_blocks)
    if index not in ref_match_set
]
extra = [
    {"index": index, "text": text}
    for index, text in enumerate(impl_blocks)
    if index not in impl_match_set
]

combined_count = len(ref_blocks) + len(impl_blocks)
if combined_count:
    ordered_similarity = 2 * lcs_length / combined_count
else:
    ordered_similarity = 1.0
missing_ratio = len(missing) / len(ref_blocks) if ref_blocks else 0.0
max_missing_blocks = max(1, math.floor(len(ref_blocks) * MAX_MISSING_RATIO))
phase_variance: dict[str, Any] = {
    "accepted": False,
    "reason": "exact-match" if canonical_blocks_equal else "not-confirmed",
}
if capture_ok and canonical_blocks_equal:
    ref_phase_catalog = {
        tuple(comparison_text(item["text"]) for item in sample)
        for sample in ref_data["samples"][ref_data["phaseSampleStartIndex"] :]
    }
    impl_phase_catalog = {
        tuple(comparison_text(item["text"]) for item in sample)
        for sample in impl_data["samples"][impl_data["phaseSampleStartIndex"] :]
    }
    if ref_phase_catalog != impl_phase_catalog:
        violations.append({
            "kind": "phase-window-text-catalog-mismatch",
            "detail": (
                "exact final copy hides different rendered text states inside "
                "the marked phase windows"
            ),
        })
if (
    capture_ok
    and not canonical_blocks_equal
    and ordered_similarity >= MIN_ORDERED_SIMILARITY
    and (
        not ref_blocks
        or (
            missing_ratio <= MAX_MISSING_RATIO
            and len(missing) <= max_missing_blocks
        )
    )
):
    phase_variance = confirm_phase_variance(
        ref_data, impl_data, ref_matches, impl_matches
    )

if (
    capture_ok
    and not canonical_blocks_equal
    and not phase_variance["accepted"]
):
    violations.append({
        "kind": "canonical-block-sequence-mismatch",
        "detail": "visible text blocks differ by value, count, or document order",
    })
if capture_ok and ordered_similarity < MIN_ORDERED_SIMILARITY:
    violations.append({
        "kind": "ordered-text-similarity-below-threshold",
        "actual": round(ordered_similarity, 4),
        "required": MIN_ORDERED_SIMILARITY,
    })
if capture_ok and ref_blocks and (
    missing_ratio > MAX_MISSING_RATIO or len(missing) > max_missing_blocks
):
    violations.append({
        "kind": "rendered-text-missing-above-threshold",
        "missingCount": len(missing),
        "missingRatio": round(missing_ratio, 4),
        "maxMissingRatio": MAX_MISSING_RATIO,
        "maxMissingBlocks": max_missing_blocks,
    })
capture_error_kinds = {
    "ref-browser-failed",
    "impl-browser-failed",
    "empty-ref-capture",
    "empty-impl-capture",
    "ref-url-mismatch",
    "impl-url-mismatch",
    "ref-http-error",
    "impl-http-error",
    "invalid-requested-url",
    "ref-impl-url-collision",
}
status = (
    "error"
    if any(item.get("kind") in capture_error_kinds for item in violations)
    else ("fail" if violations else "pass")
)
artifact = {
    "schemaVersion": 1,
    "status": status,
    "refUrl": canonical_ref_url or ref_url,
    "implUrl": canonical_impl_url or impl_url,
    "actualRefUrl": ref_data.get("actualUrl"),
    "actualImplUrl": impl_data.get("actualUrl"),
    "captureReceipt": {
        "ref": ref_data.get("captureReceipt"),
        "impl": impl_data.get("captureReceipt"),
    },
    "thresholds": {
        "minOrderedSimilarity": MIN_ORDERED_SIMILARITY,
        "maxMissingRatio": MAX_MISSING_RATIO,
        "maxMissingBlocks": max_missing_blocks,
    },
    "ref": {
        "blockCount": len(ref_blocks),
        "blocks": ref_blocks,
        "records": ref_data.get("records", []),
        "samples": ref_data.get("samples", []),
        "phaseSampleStartIndex": ref_data.get("phaseSampleStartIndex"),
    },
    "impl": {
        "blockCount": len(impl_blocks),
        "blocks": impl_blocks,
        "records": impl_data.get("records", []),
        "samples": impl_data.get("samples", []),
        "phaseSampleStartIndex": impl_data.get("phaseSampleStartIndex"),
    },
    "phaseVariance": phase_variance,
    "comparison": {
        "lcsLength": lcs_length,
        "orderedSimilarity": round(ordered_similarity, 4),
        "missingCount": len(missing),
        "missingRatio": round(missing_ratio, 4),
        "extraCount": len(extra),
        "missing": missing[:50],
        "extra": extra[:50],
    },
    "violations": violations,
}
out_path.write_text(
    json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(
    "runtime-text-sequence: "
    f"{status.upper()} lcs={lcs_length} "
    f"ordered={ordered_similarity:.3f} missing={len(missing)}"
)
raise SystemExit(2 if status == "error" else (1 if status == "fail" else 0))
