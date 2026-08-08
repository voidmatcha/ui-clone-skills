"""Producer-receipt checks for mandatory pre-generation media inventories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import CheckResult


def _load_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def check_media_inventory_receipts(ref_dir: Path) -> list[CheckResult]:
    """Require receipts emitted by runtime-media.sh and required-media.sh."""
    results: list[CheckResult] = []

    runtime_path = ref_dir / "runtime-media.json"
    if runtime_path.is_file():
        runtime = _load_object(runtime_path)
        sources = runtime.get("sources") if isinstance(runtime, dict) else None
        valid = (
            isinstance(runtime, dict)
            and runtime.get("schemaVersion") == 1
            and isinstance(sources, dict)
            and sources.get("extractor") == "runtime-media.sh"
            and _non_negative_int(sources.get("scrollSamples"))
            and sources["scrollSamples"] > 0
        )
        results.append(
            CheckResult(
                "runtime-media.json producer receipt",
                "pass" if valid else "fail",
                (
                    "runtime-media.json carries the runtime-media.sh producer receipt"
                    if valid
                    else "runtime-media.json is missing its runtime-media.sh receipt "
                    "(schemaVersion=1, sources.extractor, positive scrollSamples)."
                ),
                fix="Run Step 6b-bis runtime-media.sh against the live reference URL.",
            )
        )

    required_path = ref_dir / "required-media.json"
    if required_path.is_file():
        required = _load_object(required_path)
        sources = required.get("sources") if isinstance(required, dict) else None
        valid = (
            isinstance(required, dict)
            and required.get("schemaVersion") == 1
            and isinstance(sources, dict)
            and sources.get("extractor") == "required-media.sh"
            and _non_negative_int(sources.get("htmlSectionsScanned"))
            and sources.get("runtimeMediaScanned") is True
            and _non_negative_int(sources.get("bundlesScanned"))
        )
        results.append(
            CheckResult(
                "required-media.json producer receipt",
                "pass" if valid else "fail",
                (
                    "required-media.json carries the required-media.sh producer receipt"
                    if valid
                    else "required-media.json is missing its required-media.sh receipt "
                    "(schemaVersion=1 and complete sources scan counts)."
                ),
                fix=f'bash $PLUGIN_ROOT/scripts/extract/required-media.sh "{ref_dir}"',
            )
        )

    return results
