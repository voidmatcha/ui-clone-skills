#!/usr/bin/env python3
"""Write inline <script> bodies from an agent-browser eval into bundles/.

Split out of inline-scripts.sh so the envelope handling and file writing are
unit-testable without a browser. See tests/test_inline_scripts.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _unwrap(payload: Any) -> Any:
    """Peel the agent-browser eval envelope: {success, data: {origin, result}}.

    Unit-test shims emit the result bare, so the peel is a no-op there.
    """
    current = payload
    for _ in range(5):
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except json.JSONDecodeError:
                return current
            continue
        if isinstance(current, dict):
            data = current.get("data")
            if isinstance(data, dict) and "result" in data:
                current = data["result"]
                continue
            if "result" in current and isinstance(current["result"], dict | str):
                current = current["result"]
                continue
        break
    return current


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: _inline_scripts.py <ref-dir> <response-json>", file=sys.stderr)
        return 2
    ref_dir = Path(argv[1])
    raw = Path(argv[2]).read_text(encoding="utf-8", errors="replace")

    try:
        parsed = _unwrap(json.loads(raw))
    except json.JSONDecodeError as exc:
        print(f"inline-scripts: invalid JSON from agent-browser eval ({exc})", file=sys.stderr)
        return 3

    if not isinstance(parsed, dict) or not isinstance(parsed.get("scripts"), list):
        print(
            f"inline-scripts: unexpected payload shape: {json.dumps(parsed)[:200]}",
            file=sys.stderr,
        )
        return 3

    bundles = ref_dir / "bundles"
    bundles.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, Any]] = []
    total = 0
    for entry in parsed["scripts"]:
        if not isinstance(entry, dict):
            continue
        body = entry.get("body")
        if not isinstance(body, str) or not body.strip():
            continue
        index = entry.get("index")
        index = index if isinstance(index, int) else len(written)
        # .js so parse_bundles picks it up alongside real chunks; the name says
        # where it came from when a match is attributed back to a file.
        name = f"inline-{index:03d}.js"
        (bundles / name).write_text(body, encoding="utf-8")
        total += len(body)
        written.append({
            "file": f"bundles/{name}",
            "type": entry.get("type") or "text/javascript",
            "module": bool(entry.get("module")),
            "bytes": len(body),
        })

    skipped = parsed.get("skipped")
    summary = {
        "schemaVersion": 1,
        "url": parsed.get("url") or "",
        "count": len(written),
        "totalBytes": total,
        "files": written,
        "skipped": skipped if isinstance(skipped, list) else [],
    }
    (ref_dir / "inline-scripts.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"✓ inline-scripts: {len(written)} script(s), {total} bytes → {bundles}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
