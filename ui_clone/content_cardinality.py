"""Repeated-group cardinality derivation + verdict (omx postmortem).

A scratch clone shipped 9 hardcoded storyCards where the reference rendered
the full list; AE/section masks can hide a short repeated list and no gate
counted rendered members. Signatures derive from REF ground truth
(dom-scaffold.json): sibling groups of >=3 nodes sharing tag + class under
one parent. The shell check counts matching VISIBLE elements in the impl's
rendered runtime DOM and the verdict fails any group rendered short.

Anti-cheat properties:
- signatures come from the ref artifact, never from impl source;
- counts come from rendered runtime DOM with a visible-box filter (source
  arrays, metadata strings, hidden duplicate DOM do not count);
- duplication (impl > ref: looping carousels, virtualized clones) is an
  ADVISORY note, never a pass-substitute for a short group elsewhere.
"""

from __future__ import annotations

MIN_GROUP = 3


def _first_class(node: dict) -> str:
    cls = str(node.get("class") or "").strip()
    return cls.split()[0] if cls else ""


def repeated_group_signatures(scaffold: dict) -> list[dict]:
    """Walk dom-scaffold's tree; emit one signature per parent whose children
    contain a (tag, class) group of >= MIN_GROUP members."""
    out: list[dict] = []

    def walk(node: dict) -> None:
        children = [c for c in node.get("children") or [] if isinstance(c, dict)]
        groups: dict[tuple[str, str], int] = {}
        for c in children:
            tag = str(c.get("tag") or "").lower()
            cls = _first_class(c)
            if not tag or not cls:
                continue
            groups[(tag, cls)] = groups.get((tag, cls), 0) + 1
        for (tag, cls), count in groups.items():
            if count >= MIN_GROUP:
                out.append(
                    {
                        "parentClass": _first_class(node),
                        "childTag": tag,
                        "childClass": cls,
                        "refCount": count,
                    }
                )
        for c in children:
            walk(c)

    tree = scaffold.get("tree")
    if isinstance(tree, dict):
        walk(tree)
    return out


def signature_key(sig: dict) -> str:
    return f"{sig['parentClass']}|{sig['childTag']}|{sig['childClass']}"


def with_live_reference_counts(
    signatures: list[dict],
    live_counts: dict[str, int],
) -> list[dict]:
    """Replace scaffold sibling totals with visible live-reference totals.

    The scaffold is still the authoritative source of group signatures, but
    responsive alternates can exist in the DOM while being display:none at the
    verification viewport. Comparing a visible impl count against the raw DOM
    total would make a faithful responsive header fail its own reference.
    """
    adjusted: list[dict] = []
    for sig in signatures:
        row = dict(sig)
        key = signature_key(sig)
        if key in live_counts:
            scaffold_count = int(sig["refCount"])
            live_count = int(live_counts[key])
            row["refCount"] = live_count
            if live_count != scaffold_count:
                row["scaffoldRefCount"] = scaffold_count
        adjusted.append(row)
    return adjusted


def cardinality_verdict(
    signatures: list[dict],
    impl_counts: dict[str, int],
    tolerance: int = 0,
) -> dict:
    """Compare per-signature rendered impl counts against ref counts.

    fail  — any group with implCount < refCount - tolerance
    pass  — everything at/above ref count (duplication noted as advisory)
    """
    groups: list[dict] = []
    failed = 0
    for sig in signatures:
        key = signature_key(sig)
        ref_count = int(sig["refCount"])
        impl_count = int(impl_counts.get(key, 0))
        row = {**sig, "implCount": impl_count}
        if impl_count < ref_count - tolerance:
            row["status"] = "fail"
            failed += 1
        else:
            row["status"] = "pass"
            if impl_count > ref_count:
                # Carousel loops / virtualized lists clone members; more
                # rendered than ref is not a shortfall — note it for
                # diagnosis without failing.
                row["advisory"] = "duplication"
        groups.append(row)
    return {
        "schemaVersion": 1,
        "status": "fail" if failed else "pass",
        "failedGroups": failed,
        "tolerance": tolerance,
        "groups": groups,
    }
