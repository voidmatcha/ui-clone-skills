"""Targeted dispatch and bounded no-progress receipts; never gate evidence."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from ui_clone.check_inputs import (
    InputFingerprintUnavailable,
    _expand_braces,
    compute_check_input_hash,
    get_check_inputs,
)


def select_rows(rows: list[str], requested: set[str], changed: list[str] | None) -> list[str]:
    fields = [row.split("\t") for row in rows]
    ids = {f[1] for f in fields if len(f) > 1}
    if requested - ids:
        raise ValueError(f"Unknown iteration checks: {sorted(requested - ids)}")
    if changed is not None and any(Path(p).is_absolute() or ".." in Path(p).parts for p in changed):
        raise ValueError("Changed files must be implementation-relative paths")
    selected = set(requested)
    if changed is not None:
        for f in fields:
            spec = get_check_inputs(f[1])
            if spec is None or any(
                fnmatch.fnmatch(path, pattern)
                or Path(path).match(pattern)
                or fnmatch.fnmatch(path, pattern.replace("**/", ""))
                for path in changed
                for raw in spec.impl
                for pattern in _expand_braces(raw)
            ):
                selected.add(f[1])
    while True:
        before = set(selected)
        for f in fields:
            if f[1] in selected and len(f) > 6:
                selected.update(f[6].split())
        if before == selected:
            break
    return ["\t".join(f if f[1] in selected else ["DEFERRED", *f[1:]]) for f in fields]


def classify(artifact: dict | None, rc: int, fresh: bool) -> str:
    if rc in (124, 126, 127, 137, 143):
        return "infrastructure"
    if artifact is None:
        return "missing"
    if not fresh:
        return "stale"
    status = artifact.get("status")
    if status == "error":
        detail = json.dumps(artifact).lower()
        return (
            "measurement"
            if any(x in detail for x in ("measurement", "capture", "autoplay", "unmeasurable"))
            else "infrastructure"
        )
    if status in ("pass", "warn", "skip") and rc == 0:
        return "pass"
    return "implementation"


def _receipt(ref: Path, cid: str) -> Path:
    return ref / "iteration-retries" / (hashlib.sha256(cid.encode()).hexdigest() + ".json")


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_artifact(path: Path) -> dict | None:
    """Read retry evidence, including the canonical visual comparison text formats.

    F4 (fable-20260910): the dispatcher writes several other plain-text results
    into transitions/ (trajectory-result.txt, hover-state-result.txt,
    click-state-result.txt, video-motion-result.txt, temporal-result.txt,
    keyframes-diff-result.txt, ...), all sharing the same ✅/❌ line convention
    as transitions/result.txt. Matching only the exact literal filename made
    every one of those fall through to json.loads (raising ValueError on plain
    text) -> _read returns {} -> `or None` -> classify() saw "missing" instead
    of the real pass/fail evidence and progress signature. Match by directory +
    "result.txt" suffix instead of the single literal name; sections/result.txt
    keeps its own distinct summary-line format (unique to section-compare).
    """
    parts = path.parts[-2:]
    directory, name = parts if len(parts) == 2 else (None, path.name)
    if not name.endswith("result.txt") or directory not in ("sections", "transitions"):
        return _read(path) or None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Match post_implement's visual-health semantics for a required result.
    # Retry receipts remain advisory and never replace the canonical gate.
    if directory == "sections" and name == "result.txt":
        summary = re.search(
            r"Result:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL"
            r"(?:,\s*\d+\s+SKIP)?(?:,\s*(\d+)\s+STRUCTURAL_ONLY)?",
            text,
        )
        passed = bool(summary and int(summary[1]) > 0 and int(summary[2]) == 0)
    else:
        summary = re.search(r"Transition compare:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL", text)
        measurement_rows = any(
            ("✅" in line or "❌" in line) and "result:" not in line.lower()
            for line in text.splitlines()
        )
        passed = (
            "❌" not in text
            and measurement_rows
            and (summary is None or (int(summary[1]) > 0 and int(summary[2]) == 0))
        )
    # Preserve the measured rows as well as the verdict: changing failures
    # represent progress even when the aggregate FAIL count stays unchanged.
    return {"status": "pass" if passed else "fail", "text": text}


def _attempt_count(receipt: dict) -> int:
    value = receipt.get("attempts", 0)
    return value if type(value) is int and value >= 0 else 0


def should_pause(ref: Path, cid: str, fingerprint: str) -> bool:
    old = _read(_receipt(ref, cid))
    return bool(fingerprint and old.get("inputHash") == fingerprint and _attempt_count(old) >= 2)


def _stable(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _stable(v)
            for k, v in value.items()
            if not any(
                token in k.lower()
                for token in (
                    "timestamp",
                    "duration",
                    "elapsed",
                    "generatedat",
                    "capturedat",
                    "session",
                )
            )
        }
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def record_attempt(
    ref: Path, cid: str, fingerprint: str, artifact: dict | None, rc: int, fresh: bool
) -> None:
    category = classify(artifact, rc, fresh)
    signature = hashlib.sha256(
        json.dumps([category, rc, _stable(artifact)], sort_keys=True).encode()
    ).hexdigest()
    path = _receipt(ref, cid)
    old = _read(path)
    attempts = (
        _attempt_count(old) + 1
        if old.get("inputHash") == fingerprint and old.get("failureSignature") == signature
        else 1
    )
    if category == "pass":
        attempts = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "checkId": cid,
                "inputHash": fingerprint,
                "failureSignature": signature,
                "category": category,
                "attempts": attempts,
                "status": "needs-diagnosis" if attempts >= 2 else category,
                "canonical": False,
                "action": "Inspect the artifact and fix the reported cause before retrying. After repairing an external browser/server condition, remove this check receipt to acknowledge a diagnostic reset.",
            },
            indent=2,
        )
        + "\n"
    )


def main(args: list[str]) -> int:
    command, ref_arg, *rest = args
    ref = Path(ref_arg)
    if command == "finish":
        path = ref / "iteration-receipt.json"
        receipt = _read(path)
        if receipt.get("mode") != "final" or receipt.get("status") != "running":
            raise ValueError("Cannot finish without an active full dispatch receipt")
        receipt["status"] = "completed"
        path.write_text(json.dumps(receipt) + "\n")
        return 0
    if command == "select":
        dispatch = Path(rest[0])
        requested = {s for s in os.environ.get("UI_CLONE_ITERATION_CHECKS", "").split(",") if s}
        changed_file = os.environ.get("UI_CLONE_CHANGED_FILES")
        changed = Path(changed_file).read_text().splitlines() if changed_file else None
        active = bool(requested or changed_file)
        if active:
            rows = select_rows(dispatch.read_text().splitlines(), requested, changed)
            dispatch.write_text("\n".join(rows) + "\n")
        (ref / "iteration-receipt.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "mode": "iteration" if active else "final",
                    "canonical": False,
                    "status": "partial" if active else "running",
                }
            )
            + "\n"
        )
        return 0
    cid, impl, script, ref_url, impl_url, *extra = rest
    try:
        inputs = compute_check_input_hash(impl, ref, cid)
    except InputFingerprintUnavailable:
        # fable-20260910 follow-up review (LOW): a declared input side being
        # literally unreadable (traversal error, unavailable root) used to
        # collapse to the SAME `inputs = None` -> fingerprint "" as an
        # UNREGISTERED check_id. should_pause's `bool(fingerprint and ...)`
        # then never fires for a check whose evidence is permanently
        # unavailable — it would be redispatched forever with no path to a
        # diagnostic pause. Give it a distinct, stable, truthy sentinel so a
        # real (non-empty) fingerprint is still computed and repeated
        # unavailable-input failures can still pause for diagnosis.
        inputs = "UNAVAILABLE"
    except (OSError, ValueError):
        inputs = None
    fingerprint = (
        hashlib.sha256(
            json.dumps(
                [
                    inputs,
                    hashlib.sha256(Path(script).read_bytes()).hexdigest(),
                    ref_url,
                    impl_url,
                    {
                        # fable-20260910 follow-up review (MEDIUM): a fixed
                        # two-key tuple missed the per-check timeout overrides
                        # (RUN_REQUIRED_HOVER_TIMEOUT_SEC,
                        # RUN_REQUIRED_MASKED_STATIC_TIMEOUT_SEC,
                        # RUN_REQUIRED_TRANSITION_FIRES_TIMEOUT_SEC,
                        # RUN_REQUIRED_BREAKPOINT_COLLISION_TIMEOUT_SEC,
                        # SECTION_FROZEN_TIMEOUT_SEC, ...) run-required-checks.sh
                        # and build_required_dispatch.py resolve — raising the
                        # right one after two timeouts left the fingerprint
                        # unchanged and the pause guard stuck. Hash every
                        # *_TIMEOUT_SEC env var generically instead of a fixed
                        # allowlist, so a new timeout knob is covered automatically.
                        **{
                            key: value
                            for key, value in sorted(os.environ.items())
                            if key.endswith("_TIMEOUT_SEC")
                        },
                        "VIEWPORTS": os.environ.get("VIEWPORTS"),
                        "AGENT_BROWSER_ARGS": os.environ.get("AGENT_BROWSER_ARGS"),
                        "AGENT_BROWSER_COLOR_SCHEME": os.environ.get("AGENT_BROWSER_COLOR_SCHEME"),
                    },
                    _stable(_read(ref / "verification-plan.json")),
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if inputs
        else ""
    )
    if command == "guard":
        return 3 if should_pause(ref, cid, fingerprint) else 0
    artifact_path, rc, fresh = extra
    record_attempt(
        ref, cid, fingerprint, _read_artifact(Path(artifact_path)), int(rc), fresh == "1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
