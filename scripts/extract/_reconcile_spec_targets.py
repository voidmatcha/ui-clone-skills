#!/usr/bin/env python3
"""Deterministic (browser-free) half of spec-target DOM reconciliation.

Two responsibilities, both pure JSON so they are unit-testable without a browser:

  classify  — given structure.json + transition-spec.json + hover-css-rules.json,
              report which spec/hover TARGET selectors are absent from the captured
              homepage DOM (the ones the transpiler cannot emit and that
              transition-fires later reports as 'element not found').

  merge     — given structure.json + a revealed-targets.json sidecar (subtrees the
              browser reveal pass captured), splice each revealed subtree into a
              COPY of the tree (structure.merged.json) under its observed parent,
              so a downstream transpile emits real nodes. structure.json itself is
              never mutated (it is provenance-stamped); the merge writes a new file.
              Targets that cannot be revealed or placed are returned as
              missingSpecTargets[] for the Step-7 synthesis obligation.

CLI:
  _reconcile_spec_targets.py classify <ref_dir> [--out FILE]
  _reconcile_spec_targets.py merge <ref_dir> <revealed-targets.json> [--out FILE]
"""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

# ── Selector token extraction (kept in lockstep with
#    ui_clone/gates/spec.py::_check_spec_selectors_present_in_dom) ──────────────
_CLASS_RE = re.compile(r"\.((?:\\.|[A-Za-z_-])(?:\\.|[A-Za-z0-9_-])*)")
_ID_RE = re.compile(r"#((?:\\.|[A-Za-z_-])(?:\\.|[A-Za-z0-9_-])*)")
_NOISE_RE = re.compile(r"\[[^\]]*\]|(?<!\\)::?[A-Za-z][A-Za-z0-9-]*(?:\([^)]*\))?")
_RUNTIME_RE = re.compile(
    r"\b(?:swiper|splide|slick|flickity|embla|keen-slider|glide"
    r"|lottie|bodymovin|canvas"
    r"|lenis|locomotive|data-scroll|data-lottie|data-pseudo|data-lenis|data-smooth)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _toks(rx: re.Pattern[str], group: str) -> list[str]:
    return [m.replace("\\", "") for m in rx.findall(group)]


def _collect_dom_tokens(node: Any, classes: set[str], ids: set[str]) -> None:
    if not isinstance(node, dict):
        return
    cls = node.get("class")
    if isinstance(cls, str):
        for tok in cls.split():
            if tok:
                classes.add(tok)
    nid = node.get("id")
    if isinstance(nid, str) and nid:
        ids.add(nid)
    for child in node.get("children") or []:
        _collect_dom_tokens(child, classes, ids)


def _token_present(token: str, captured: set[str]) -> bool:
    """Exact match, plus the CSS-modules `[name]__[hash]` convention."""
    return any(c == token or c.startswith(token + "__") for c in captured)


def _selector_leaf_tokens(selector: str) -> set[str]:
    """Class/id tokens carried by a selector (used to test whether a merged
    target's subject token matches a classify-missing entry)."""
    cleaned = _NOISE_RE.sub(" ", selector)
    return set(_toks(_CLASS_RE, cleaned) + _toks(_ID_RE, cleaned))


def _selector_present(target: str, classes: set[str], ids: set[str]) -> bool:
    """A selector list matches when ANY comma-group matches; a group matches when
    ALL its class/id tokens are present. Attribute/pseudo noise stripped first."""
    cleaned = _NOISE_RE.sub(" ", target)
    if not _CLASS_RE.search(cleaned) and not _ID_RE.search(cleaned):
        return True  # tag-only / attr-only — not reliably checkable, treat as present
    for group in cleaned.split(","):
        group = group.strip()
        if not group:
            continue
        g_classes = _toks(_CLASS_RE, group)
        g_ids = _toks(_ID_RE, group)
        if not g_classes and not g_ids:
            return True
        if all(_token_present(c, classes) for c in g_classes) and all(
            _token_present(d, ids) for d in g_ids
        ):
            return True
    return False


def _spec_targets(ref_dir: Path) -> list[dict[str, Any]]:
    """Collect {id, selector, source} rows from transition-spec + hover-css-rules."""
    out: list[dict[str, Any]] = []
    spec = _load(ref_dir / "transition-spec.json")
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    for t in transitions if isinstance(transitions, list) else []:
        if not isinstance(t, dict):
            continue
        tgt = t.get("target") or t.get("selector")
        if isinstance(tgt, str) and tgt.strip():
            out.append({"id": str(t.get("id", "")), "selector": tgt.strip(),
                        "source": "transition-spec"})
    hcr = _load(ref_dir / "hover-css-rules.json")
    rules = hcr.get("rules") if isinstance(hcr, dict) else (hcr if isinstance(hcr, list) else None)
    for r in rules if isinstance(rules, list) else []:
        sel = r.get("selector") if isinstance(r, dict) else None
        if isinstance(sel, str) and sel.strip():
            # Reduce a `:hover` rule to its subject (strip pseudo tail) so we test
            # the element that must EXIST, not the hover state string.
            out.append({"id": "", "selector": sel.strip(), "source": "hover-css-rules"})
    return out


def classify(ref_dir: Path) -> dict[str, Any]:
    structure = _load(ref_dir / "structure.json")
    classes: set[str] = set()
    ids: set[str] = set()
    _collect_dom_tokens(structure, classes, ids)

    seen_selectors: set[str] = set()
    missing: list[dict[str, Any]] = []
    present = 0
    for row in _spec_targets(ref_dir):
        sel = row["selector"]
        if sel in seen_selectors:
            continue
        seen_selectors.add(sel)
        if _RUNTIME_RE.search(sel):
            continue  # runtime-injected — not revealable via DOM stimulation
        if not classes and not ids:
            continue  # degenerate capture — never flag
        if _selector_present(sel, classes, ids):
            present += 1
            continue
        # class/id tokens of the missing subject (for the reveal pass + merge)
        cleaned = _NOISE_RE.sub(" ", sel)
        toks = _toks(_CLASS_RE, cleaned) + _toks(_ID_RE, cleaned)
        if not toks:
            continue  # tag-only — nothing to reveal on
        missing.append({"id": row["id"], "selector": sel, "source": row["source"],
                        "tokens": toks})
    return {"schemaVersion": 1, "present": present, "missing": missing}


# ── Merge half ────────────────────────────────────────────────────────────────
def _find_parent_node(root: Any, ancestors: list[dict[str, Any]]) -> Any:
    """Return the structure.json node matching the NEAREST revealed ancestor that
    exists in the captured tree. `ancestors` is ordered nearest-first; each is
    {id?, classes:[...]}."""
    def _node_matches(node: dict[str, Any], anc: dict[str, Any]) -> bool:
        anc_id = anc.get("id")
        if anc_id:
            return isinstance(node.get("id"), str) and node["id"] == anc_id
        anc_classes = [c for c in (anc.get("classes") or []) if c]
        if not anc_classes:
            return False
        node_classes = set((node.get("class") or "").split())
        # every distinctive ancestor class token must be present on the node
        return all(_token_present(c, node_classes) for c in anc_classes)

    def _search(node: Any, anc: dict[str, Any]) -> Any:
        if not isinstance(node, dict):
            return None
        if _node_matches(node, anc):
            return node
        for child in node.get("children") or []:
            hit = _search(child, anc)
            if hit is not None:
                return hit
        return None

    for anc in ancestors:
        hit = _search(root, anc)
        if hit is not None:
            return hit
    return None


def _subtree_tokens(node: Any, acc: set[str]) -> None:
    if not isinstance(node, dict):
        return
    for tok in (node.get("class") or "").split():
        acc.add(tok)
    for child in node.get("children") or []:
        _subtree_tokens(child, acc)


def merge(structure: dict[str, Any], revealed: list[dict[str, Any]]) -> dict[str, Any]:
    """Return {merged_tree, mergedTargets[], missingSpecTargets[]}.

    Never mutates `structure` (deep-copied first)."""
    tree = deepcopy(structure)
    root_classes: set[str] = set()
    root_ids: set[str] = set()
    _collect_dom_tokens(tree, root_classes, root_ids)

    merged_targets: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in revealed:
        selector = item.get("selector", "")
        subtree = item.get("subtree")
        ancestors = item.get("ancestors") or []
        if not isinstance(subtree, dict):
            missing.append({"selector": selector, "reason": "no-subtree-captured",
                            "foundVia": item.get("foundVia")})
            continue
        # already present (some other reveal / re-run) — skip idempotently
        sub_tokens: set[str] = set()
        _subtree_tokens(subtree, sub_tokens)
        top_tokens = set((subtree.get("class") or "").split())
        if top_tokens and all(_token_present(t, root_classes) for t in top_tokens):
            merged_targets.append({"selector": selector, "placedUnder": "already-present",
                                   "foundVia": item.get("foundVia")})
            continue
        parent = _find_parent_node(tree, ancestors)
        if parent is None:
            missing.append({"selector": selector, "reason": "parent-not-in-structure",
                            "foundVia": item.get("foundVia"),
                            "subtreeHtml": item.get("subtreeHtml")})
            continue
        parent.setdefault("children", [])
        if not isinstance(parent["children"], list):
            parent["children"] = []
        parent["children"].append(subtree)
        # keep the token index current so a later reveal of the same class is idempotent
        root_classes |= sub_tokens
        merged_targets.append({
            "selector": selector,
            "placedUnder": (parent.get("id") or parent.get("class") or parent.get("tag") or "?"),
            "foundVia": item.get("foundVia"),
        })
    return {"tree": tree, "mergedTargets": merged_targets, "missingSpecTargets": missing}


# ── IO + CLI ──────────────────────────────────────────────────────────────────
def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
    ref_dir = Path(argv[1])
    out_flag = None
    if "--out" in argv:
        out_flag = Path(argv[argv.index("--out") + 1])

    if cmd == "classify":
        result = classify(ref_dir)
        target = out_flag or (ref_dir / "spec-targets-missing.json")
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"present": result["present"], "missing": len(result["missing"])}))
        return 0

    if cmd == "merge":
        if len(argv) < 3:
            print("merge needs <ref_dir> <revealed-targets.json>", file=sys.stderr)
            return 2
        structure = _load(ref_dir / "structure.json")
        if not isinstance(structure, dict):
            print("ERROR: structure.json missing/invalid", file=sys.stderr)
            return 2
        revealed_doc = _load(Path(argv[2]))
        revealed = revealed_doc.get("targets") if isinstance(revealed_doc, dict) else revealed_doc
        revealed = revealed if isinstance(revealed, list) else []
        result = merge(structure, revealed)
        merged_path = out_flag or (ref_dir / "structure.merged.json")
        merged_path.write_text(json.dumps(result["tree"]) + "\n", encoding="utf-8")

        # Complete the Step-7 obligation set: EVERY classify-missing target that
        # was not successfully spliced is a missingSpecTarget — whether it was
        # never revealed (no live element) or revealed into an interaction-only
        # overlay whose parent is absent from the homepage capture. Merge-side
        # placement failures already carry the captured hover-state snippet.
        # Match classify-missing rows to merge outcomes by LEAF TOKEN — the
        # classify selector keeps pseudo/attr tails (`...__mcr8C:not(:disabled)`)
        # while the revealed selector is the bare `.token`, so string equality
        # would misclassify a revealed-but-unplaceable target as never-revealed.
        placed_tokens = {
            tok
            for m in result["mergedTargets"]
            for tok in _selector_leaf_tokens(m.get("selector", ""))
        }
        unplaceable_by_token: dict[str, dict[str, Any]] = {}
        for m in result["missingSpecTargets"]:
            for tok in _selector_leaf_tokens(m.get("selector", "")):
                unplaceable_by_token[tok] = m
        classify_missing = _load(ref_dir / "spec-targets-missing.json")
        missing_rows = classify_missing.get("missing") if isinstance(classify_missing, dict) else None
        full_missing: list[dict[str, Any]] = []
        if isinstance(missing_rows, list):
            for row in missing_rows:
                sel = row.get("selector", "")
                toks = row.get("tokens") or []
                leaf = toks[-1] if toks else ""
                if leaf and leaf in placed_tokens:
                    continue  # reconciled into structure.merged.json
                if leaf and leaf in unplaceable_by_token:
                    src = unplaceable_by_token[leaf]
                    full_missing.append({"selector": sel, "id": row.get("id"),
                                         "source": row.get("source"),
                                         "reason": src.get("reason"),
                                         "foundVia": src.get("foundVia"),
                                         "subtreeHtml": src.get("subtreeHtml")})
                else:
                    full_missing.append({"selector": sel, "id": row.get("id"),
                                         "source": row.get("source"),
                                         "reason": "not-revealed-by-stimulation"})
        else:
            full_missing = result["missingSpecTargets"]

        report = {
            "schemaVersion": 1,
            "merged": len(result["mergedTargets"]),
            "mergedTargets": result["mergedTargets"],
            "missingSpecTargets": full_missing,
            "mergedStructure": str(merged_path),
        }
        (ref_dir / "reconcile-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"merged": report["merged"],
                          "missingSpecTargets": len(report["missingSpecTargets"])}))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
