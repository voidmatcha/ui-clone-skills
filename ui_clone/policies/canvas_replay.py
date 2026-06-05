"""Canvas-replay closeout policy — gate-side relief helpers.

When the operator opts into `closeoutPolicy: "canvas-replay"` and signs
the `canvas-replay-attestation.json` license proof (v0.7.0), three gates
relax their behavior for sections explicitly tagged `kind: "canvas"` in
section-map.json. The Stop-hook half of the policy (stamp enforcement,
tamper detection) lives in `ui_clone.hooks.section_gate`; this module
holds the modifier predicates that gates consult when running.

Boundary:
  - Relief applies to canvas pixels ONLY. Text fidelity, font parity,
    runtime-DOM parity, transition-compare are unaffected.
  - All 3 conditions must hold per section: closeoutPolicy="canvas-replay"
    AND canvas-replay-attestation.json present AND section.kind=="canvas".
  - Attestation is operator-signed proof; never auto-detected.
"""

from __future__ import annotations

import json
from pathlib import Path

# Canonical critical band from section-compare.sh — AE/Mpx > 20000 = critical.
# Mirror here so the gate can re-classify canvas-section criticals without
# re-running section-compare.
CRITICAL_AE_PER_MPX = 20000

# Relief multiplier. Canvas pixels diverge from CSS approximation by design;
# 2x widens the critical band to (20000, 40000] for canvas-tagged sections.
# Calibrated against high-AE canvas-heavy references where CSS replication was
# exhausted but the remaining canvas-pixel delta stayed in the 25k-40k AE/Mpx
# band. A 2x multiplier captures that envelope without admitting cases above
# 40k AE/Mpx, where the approximation has diverged beyond perceptual
# acceptance.
AE_RELIEF_MULTIPLIER = 2.0

_ATTESTATION_FILENAME = "canvas-replay-attestation.json"
_SECTION_MAP_FILENAME = "section-map.json"


def _load_state_policy(ref_dir: Path) -> str:
    """Read closeoutPolicy from pipeline-state.json without importing
    ui_clone.state — the gate path is hot and circular-import-prone.
    """
    path = ref_dir / "pipeline-state.json"
    if not path.is_file():
        return "canonical"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "canonical"
    if not isinstance(data, dict):
        return "canonical"
    return (
        data.get("closeoutPolicy")
        or data.get("closeout_policy")
        or "canonical"
    )


def attestation_path(ref_dir: Path) -> Path:
    return ref_dir / _ATTESTATION_FILENAME


def is_policy_active(ref_dir: Path) -> bool:
    """True iff closeoutPolicy=="canvas-replay" AND attestation file exists.

    Both halves must hold — policy alone (no attestation) MUST NOT relax
    any gate. Absent attestation means the mode is disabled even if the
    policy field is set. This makes the
    "wrote policy, forgot attestation" mistake fail-closed.
    """
    if _load_state_policy(ref_dir) != "canvas-replay":
        return False
    return attestation_path(ref_dir).is_file()


def _load_attestation(ref_dir: Path) -> dict | None:
    p = attestation_path(ref_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def attestation_canvas_sources(ref_dir: Path) -> list[str]:
    """The `ref_canvas_sources[]` array from the attestation — empty if
    missing/malformed. These are the JS bundle URLs the operator declared
    are loaded at runtime for canvas fidelity; ref-js-loader.sh uses this
    as an allowlist (URLs declared here PASS; all others still FAIL).
    """
    data = _load_attestation(ref_dir)
    if data is None:
        return []
    raw = data.get("ref_canvas_sources") or []
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, str) and s.strip()]


def _section_aliases(entry: dict) -> set[str]:
    """All plausible names a section-map.json entry might be known by
    in sections/result.txt. section-compare.sh derives row names from the
    capture PNG file stems (className-based, id-based, or index-based), so
    we try each shape an operator might use to annotate.
    """
    out: set[str] = set()
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        out.add(name.strip())
    sid = entry.get("id")
    if isinstance(sid, str) and sid.strip():
        out.add(sid.strip())
    cls = entry.get("className") or entry.get("cls")
    if isinstance(cls, str) and cls.strip():
        out.add(cls.strip())
    idx = entry.get("index")
    if isinstance(idx, int):
        out.add(f"section-{idx}")
    return out


def canvas_section_names(ref_dir: Path) -> frozenset[str]:
    """Set of section names tagged `kind: "canvas"` in section-map.json.

    Returns every plausible alias per entry — `name`, `id`, `className`,
    `section-{index}` — so the gate can match against whichever shape
    section-compare.sh emits in result.txt. Operators who add `kind:
    "canvas"` without a `name` field can still get relief via className/id.
    """
    p = ref_dir / _SECTION_MAP_FILENAME
    if not p.is_file():
        return frozenset()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    sections = data.get("sections") or []
    if not isinstance(sections, list):
        return frozenset()
    out: set[str] = set()
    for entry in sections:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") != "canvas":
            continue
        out.update(_section_aliases(entry))
    return frozenset(out)


def relief_active_sections(ref_dir: Path) -> frozenset[str]:
    """Composition: canvas section names if policy is active, else empty.

    Single entry point for gates — three conditions resolved into one
    lookup. Empty set = no relief (either policy disabled, attestation
    missing, or no kind=canvas sections declared).
    """
    if not is_policy_active(ref_dir):
        return frozenset()
    return canvas_section_names(ref_dir)


def critical_ae_ceiling() -> float:
    """The AE/Mpx ceiling under which a canvas-tagged section's critical
    fail downgrades to PASS. AE/Mpx values strictly above this stay critical
    even under relief — relief widens the band, it does not bypass it.
    """
    return CRITICAL_AE_PER_MPX * AE_RELIEF_MULTIPLIER
