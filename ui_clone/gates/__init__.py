"""Gate class assembly.

The Gate class lives in `gates.base`. Per-gate methods live in their
own modules (reference.py, extraction.py, …) as bare functions that
take `self: "Gate"`. This module rebinds each onto the Gate class so
callers can keep using `Gate(ref_dir).gate_*()`.

Adding a new gate:
  1. Add the gate name to `state.GATE_ORDER`.
  2. Add a `gate_<name>(self)` function to one of the per-area modules
     (or a new module under `ui_clone/gates/`).
  3. Import and rebind it below.
  4. The import-time validator at the bottom of this file fails fast
     if any gate name in state.GATE_ORDER lacks a matching method.
"""

from __future__ import annotations

from ui_clone import state as _state

from . import (
    boundary,
    bundle,
    extraction,
    font_parity,
    paid_features,
    post_implement,
    pre_generate,
    reference,
    section_compare,
    spec,
    verification_plan,
)
from . import (
    dispatch as _dispatch_mod,
)
from .base import (
    _AE_GROWTH_MULTIPLIER,
    _DISALLOWED_PROVENANCE_SOURCES,
    _PROVENANCE_REQUIRED_ARTIFACTS,
    _REQUIRED_ARTIFACT_FIELDS,
    _VALID_PROVENANCE_SOURCES,
    _VALID_VERIFIED_BY,
    CheckResult,
    Gate,
    _parse_all_section_ae,
    _parse_failed_sections,
    _validate_artifact_entry,
)
from .dispatch import VALID_GATES, _gate_method_name, main

# Rebind dispatch / orchestration methods.
Gate._make_dispatch = _dispatch_mod._make_dispatch  # type: ignore[method-assign]
Gate._dispatch = _dispatch_mod._dispatch  # type: ignore[method-assign]
Gate._check_pipeline_state_prerequisites = (  # type: ignore[method-assign]
    _dispatch_mod._check_pipeline_state_prerequisites
)
Gate._render_text = _dispatch_mod._render_text  # type: ignore[method-assign]
Gate._render_json = _dispatch_mod._render_json  # type: ignore[method-assign]
Gate.run = _dispatch_mod.run  # type: ignore[method-assign]

# Rebind per-gate methods.
#
# Mock-patch gotcha (Codex Item-5 follow-up): `Gate.gate_spec` etc. are
# bound to the per-area function objects AT IMPORT TIME of this module.
# Tests that want to patch a gate must target the class attribute, not
# the per-area module:
#
#   ✅ patch("ui_clone.gates.base.Gate.gate_spec", ...)
#   ❌ patch("ui_clone.gates.spec.gate_spec", ...)   # has no effect on
#                                                    # existing Gate instances
#
# The wrong target patches the per-module attribute but Gate already
# captured a direct reference; Gate(...).gate_spec() will still call
# the original. See tests/gates/test_dispatch.py for a verified example.
Gate.gate_reference = reference.gate_reference  # type: ignore[method-assign]
Gate.gate_extraction = extraction.gate_extraction  # type: ignore[method-assign]
Gate.gate_bundle = bundle.gate_bundle  # type: ignore[method-assign]
Gate._check_paid_font_substitution = (  # type: ignore[method-assign]
    paid_features._check_paid_font_substitution
)
Gate.gate_paid_features = paid_features.gate_paid_features  # type: ignore[method-assign]
Gate.gate_spec = spec.gate_spec  # type: ignore[method-assign]
Gate._check_webflow = pre_generate._check_webflow  # type: ignore[method-assign]
Gate._check_hover_timing = pre_generate._check_hover_timing  # type: ignore[method-assign]
Gate._check_transition_coverage = (  # type: ignore[method-assign]
    pre_generate._check_transition_coverage
)
Gate._check_section_counts = pre_generate._check_section_counts  # type: ignore[method-assign]
Gate._check_audit_artifacts = pre_generate._check_audit_artifacts  # type: ignore[method-assign]
Gate._check_detection_artifact_integrity = (  # type: ignore[method-assign]
    pre_generate._check_detection_artifact_integrity
)
Gate._check_scroll_spec_coverage = (  # type: ignore[method-assign]
    pre_generate._check_scroll_spec_coverage
)
Gate.gate_pre_generate = pre_generate.gate_pre_generate  # type: ignore[method-assign]
Gate._check_generation_completeness = (  # type: ignore[method-assign]
    post_implement._check_generation_completeness
)
Gate._check_componentization = (  # type: ignore[method-assign]
    post_implement._check_componentization
)
Gate._find_impl_root = post_implement._find_impl_root  # type: ignore[method-assign]
Gate.gate_post_implement = post_implement.gate_post_implement  # type: ignore[method-assign]
Gate._check_verification_plan = (  # type: ignore[method-assign]
    verification_plan._check_verification_plan
)
Gate._transition_spec_count = (  # type: ignore[method-assign]
    verification_plan._transition_spec_count
)
Gate._tree_diff_floor = verification_plan._tree_diff_floor  # type: ignore[method-assign]
Gate.gate_boundary = boundary.gate_boundary  # type: ignore[method-assign]
Gate.gate_font_parity = font_parity.gate_font_parity  # type: ignore[method-assign]
Gate.gate_section_compare = section_compare.gate_section_compare  # type: ignore[method-assign]


# Validate at import: every gate in state.GATE_ORDER has a matching
# `gate_<name>` method on Gate AND the method is rebound from a per-gate
# module (not the empty stub declared in base.py). Catches drift the
# moment a gate is added to GATE_ORDER without a corresponding
# implementation (or vice versa), with no runtime overhead.
#
# Codex Item-5 follow-up: extend the check to ALL stubs declared on Gate
# (gate_* methods, helper _check_* methods, dispatch/render/run, etc.) —
# the original validator only covered gate_* names from GATE_ORDER, so a
# helper stub like _check_verification_plan that never got rebound would
# stay silently broken until the first call.
_BASE_MODULE = "ui_clone.gates.base"
_missing_methods: list[str] = []
for _gate in _state.GATE_ORDER:
    _attr = getattr(Gate, _gate_method_name(_gate), None)
    if not callable(_attr):
        _missing_methods.append(_gate)
        continue
    # Rebound methods live in `ui_clone.gates.<module>`; stubs live in
    # `ui_clone.gates.base`. A stub here means __init__ forgot to rebind.
    if getattr(_attr, "__module__", "") == _BASE_MODULE:
        _missing_methods.append(_gate)
if _missing_methods:
    raise RuntimeError(
        f"Gate methods missing or not rebound for state.GATE_ORDER entries: "
        f"{_missing_methods}. Each gate must be defined in a per-area module "
        f"under ui_clone/gates/ and rebound onto Gate via this __init__."
    )

# Names of attributes deliberately KEPT on the base Gate class (i.e. real
# implementations, not stubs that need rebinding). These are the primitive
# helpers + the constants. Anything else with `__module__ == base` and
# whose source body is `...` is an un-rebound stub bug.
_BASE_OWNED: frozenset[str] = frozenset(
    {
        "__init__",
        "check_dir",
        "check_file",
        "check_json_key",
        "_load_json",
        "_check_artifact_provenance",
        "_PAID_FONT_CDN_HOSTS",
    }
)
_unbound_stubs: list[str] = []
for _name in dir(Gate):
    if _name in _BASE_OWNED or _name.startswith("__"):
        continue
    _attr = getattr(Gate, _name, None)
    if not callable(_attr):
        continue
    if getattr(_attr, "__module__", "") != _BASE_MODULE:
        continue
    # Attribute is callable, declared in base, NOT in the allow-list of
    # base-owned methods → it's a stub that __init__ should have rebound.
    _unbound_stubs.append(_name)
if _unbound_stubs:
    raise RuntimeError(
        f"Gate has un-rebound stubs from ui_clone.gates.base: "
        f"{sorted(_unbound_stubs)}. Each stub must be rebound from a "
        f"per-area module in this __init__, or removed from base.py if "
        f"intentionally no longer needed."
    )


__all__ = [
    "CheckResult",
    "Gate",
    "VALID_GATES",
    "_AE_GROWTH_MULTIPLIER",
    "_DISALLOWED_PROVENANCE_SOURCES",
    "_PROVENANCE_REQUIRED_ARTIFACTS",
    "_REQUIRED_ARTIFACT_FIELDS",
    "_VALID_PROVENANCE_SOURCES",
    "_VALID_VERIFIED_BY",
    "_gate_method_name",
    "_parse_all_section_ae",
    "_parse_failed_sections",
    "_validate_artifact_entry",
    "main",
]
