from __future__ import annotations

import json
from pathlib import Path

from ui_clone.local_source_reuse import detect_local_source_reuse


def test_detect_local_source_reuse_flags_any_protected_root(tmp_path: Path) -> None:
    protected = tmp_path / "seed" / "projects" / "sample"
    protected.mkdir(parents=True)
    impl = tmp_path / "impl"
    impl.mkdir()
    log = tmp_path / "clone.jsonl"
    log.write_text(
        json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": f"rsync -a {protected} {impl / 'src'}",
            },
        })
        + "\n",
        encoding="utf-8",
    )

    findings = detect_local_source_reuse(
        impl_dir=impl,
        protected_roots=[protected],
        log_paths=[log],
        source_label="seed",
    )

    assert findings
    assert "copies seed path" in findings[0]


def test_detect_local_source_reuse_flags_embedded_absolute_paths(tmp_path: Path) -> None:
    protected = tmp_path / "fixture"
    protected.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "page.tsx").write_text(
        f"export const source = '{protected}/asset.png';\n",
        encoding="utf-8",
    )

    findings = detect_local_source_reuse(
        impl_dir=impl,
        protected_roots=[protected],
        source_label="fixture",
    )

    assert findings == [f"impl/src/page.tsx embeds absolute fixture path {protected.resolve()}"]
