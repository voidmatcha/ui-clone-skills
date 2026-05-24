"""Per-phase pipeline helpers, split out of `ui_clone.pipeline` to keep
the main module focused on the `Pipeline` class shim and CLI.

This package is a *physical move* of the per-phase function bodies — the
public API surface on `ui_clone.pipeline` (Pipeline class methods,
helper functions, dataclasses) is unchanged. Tests in `tests/test_pipeline.py`
exercise the Pipeline class API directly and pass without modification.
"""

from __future__ import annotations
