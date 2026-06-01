from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ui_clone.evidence_pack import materialize_skill_briefs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.evidence_pack",
        description="Materialize compact UI evidence briefs from an evidence pack JSON file or ref dir.",
    )
    parser.add_argument("pack", help="Path to an evidence pack JSON file or tmp/ref/<component> dir")
    parser.add_argument("--out-dir", default=None, help="Directory for generated brief files")
    parser.add_argument("--max-chars", type=int, default=3000, help="Per-brief character budget")
    args = parser.parse_args(argv)

    pack_path = Path(args.pack)
    if not pack_path.exists():
        print(f"evidence pack not found: {pack_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else (
        pack_path / "brief" if pack_path.is_dir() else pack_path.with_suffix("")
    )
    try:
        written = materialize_skill_briefs(pack_path, out_dir, max_chars=args.max_chars)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
