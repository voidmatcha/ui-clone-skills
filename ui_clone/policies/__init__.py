"""Closeout-policy modifier helpers.

These modules encode the gate-side behavior of opt-in closeout policies
(canvas-replay, future structural variants). The Stop-hook side of each
policy lives in `ui_clone.hooks.section_gate`; this package mirrors the
modifier logic that runs inside the post-implement gate set, keeping
policy-aware checks out of the canonical gate code paths.
"""
