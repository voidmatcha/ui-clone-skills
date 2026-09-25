"""Release hash manifest of the scoped-evidence producers.

`ui_clone/scoped_producers.sha256.json` ships the sha256 of every producer
module and capture script whose content decides what scoped evidence says:

    python -m ui_clone.scoped_producers --check    # installed files match the manifest
    python -m ui_clone.scoped_producers --write    # maintainers: regenerate after editing a producer

`python -m ui_clone.scoped_check` requires three hashes to agree for each
producer: the one recorded in the evidence (`producer.moduleSha256`,
`producer.driver.sha256`, `producerRecord.moduleSha256`), the one in this
manifest, and the one of the file installed next to the checker. An edited
producer (or an evidence record stamped by one) therefore fails even when
the edit was made before the evidence was produced. `scripts/ci/review.sh`
runs `--check`, so a producer change cannot ship without a regenerated
manifest. Hashes are of file content, never of paths, so a version bump or a
plugin cache path (`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>`)
does not change them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ui_clone.scoped_provenance import REPO_ROOT, file_sha256

MANIFEST_NAME = "scoped_producers.sha256.json"
MANIFEST_PATH = REPO_ROOT / "ui_clone" / MANIFEST_NAME
MANIFEST_SCHEMA_VERSION = 1
# Repo-relative paths, POSIX separators. Keep in sync with the modules the
# Bash guard denies importing (`bash_write._SCOPED_PRODUCER_MODULES`) plus the
# contract they share and the scripts that drive them.
PRODUCER_FILES: tuple[str, ...] = (
    "ui_clone/computed_style_diff.py",
    "ui_clone/element_capture.py",
    "ui_clone/scoped_diff.py",
    "ui_clone/scoped_frames.py",
    "ui_clone/scoped_provenance.py",
    "scripts/extract/element-evidence.sh",
    "scripts/extract/element-state-capture.sh",
)
# Evidence field -> manifest entry it must equal.
CAPTURE_MODULE = "ui_clone/element_capture.py"
DIFF_MODULE = "ui_clone/scoped_diff.py"
DRIVER_SCRIPT = "scripts/extract/element-state-capture.sh"


def current_hashes(root: Path | None = None) -> dict[str, str | None]:
    """sha256 of every producer file as installed under `root`."""
    base = REPO_ROOT if root is None else root
    return {rel: file_sha256(base / rel) for rel in PRODUCER_FILES}


def load_manifest(path: Path | None = None) -> dict[str, str] | None:
    """The shipped `{repo-relative path: sha256}` map, None when missing or malformed."""
    try:
        data = json.loads((MANIFEST_PATH if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        return None
    files = data.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and len(v) == 64 for k, v in files.items()
    ):
        return None
    return dict(files)


def shipped_sha256(rel: str, manifest: dict[str, str] | None = None) -> str | None:
    """The manifest hash of one producer file (None when the manifest is unusable)."""
    files = load_manifest() if manifest is None else manifest
    return files.get(rel) if files else None


def problems(root: Path | None = None, path: Path | None = None) -> list[str]:
    """Why the installed producers do not match the shipped manifest, else []."""
    path = MANIFEST_PATH if path is None else path
    manifest = load_manifest(path)
    if manifest is None:
        return [f"{path.name} missing or malformed; regenerate with `python -m ui_clone.scoped_producers --write`"]
    out: list[str] = []
    installed = current_hashes(root)
    for rel in PRODUCER_FILES:
        digest = installed[rel]
        if digest is None:
            out.append(f"{rel}: not installed")
        elif rel not in manifest:
            out.append(f"{rel}: not in {path.name}")
        elif manifest[rel] != digest:
            out.append(f"{rel}: installed file differs from {path.name}")
    for rel in sorted(set(manifest) - set(PRODUCER_FILES)):
        out.append(f"{rel}: listed in {path.name} but not a producer file")
    return out


def render(root: Path | None = None) -> dict[str, Any]:
    files = current_hashes(root)
    missing = [rel for rel, digest in files.items() if digest is None]
    if missing:
        raise FileNotFoundError(f"producer file(s) missing under {REPO_ROOT if root is None else root}: {', '.join(missing)}")
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "note": (
            "sha256 of the scoped-evidence producers; regenerate with "
            "`python -m ui_clone.scoped_producers --write` after editing any listed file"
        ),
        "files": files,
    }


def write(path: Path | None = None, root: Path | None = None) -> Path:
    path = MANIFEST_PATH if path is None else path
    path.write_text(json.dumps(render(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.scoped_producers",
        description="Check or regenerate the release hash manifest of the scoped-evidence producers.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="exit 1 when an installed producer differs")
    group.add_argument("--write", action="store_true", help="regenerate the manifest from the installed files")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if args.write:
        try:
            path = write()
        except (OSError, FileNotFoundError) as exc:
            print(f"scoped_producers: {exc}", file=sys.stderr)
            return 1
        print(f"scoped_producers: wrote {path}")
        return 0
    found = problems()
    if found:
        for item in found:
            print(f"scoped_producers: {item}", file=sys.stderr)
        return 1
    print(f"scoped_producers: {len(PRODUCER_FILES)} producer file(s) match {MANIFEST_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
