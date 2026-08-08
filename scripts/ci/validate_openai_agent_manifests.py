#!/usr/bin/env python3
"""Validate the three public OpenAI agent manifests without YAML dependencies."""

from __future__ import annotations

import re
import sys
from pathlib import Path

EXPECTED = {
    Path("skills/ui-reverse-engineering/agents/openai.yaml"),
    Path("skills/ui-capture/agents/openai.yaml"),
    Path("skills/visual-debug/agents/openai.yaml"),
}
REQUIRED = {
    "interface": ("display_name", "short_description", "default_prompt"),
    "policy": ("allow_implicit_invocation",),
}


def validate(root: Path) -> list[str]:
    paths = {
        path.relative_to(root)
        for path in (root / "skills").glob("*/agents/openai.yaml")
    }
    errors: list[str] = []

    for missing in sorted(EXPECTED - paths):
        errors.append(f"{missing}: missing OpenAI agent manifest")

    for relative_path in sorted(paths):
        path = root / relative_path
        text = path.read_text(encoding="utf-8")
        if "\t" in text:
            errors.append(f"{relative_path}: tabs are not allowed")

        sections: dict[str, dict[str, str]] = {}
        current: str | None = None
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line.startswith(" "):
                current = line.split(":", 1)[0].strip()
                sections.setdefault(current, {})
                continue
            if current is None:
                continue
            match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
            if match:
                sections.setdefault(current, {})[match.group(1)] = (
                    match.group(2).strip()
                )

        for section, keys in REQUIRED.items():
            if section not in sections:
                errors.append(f"{relative_path}: missing {section}: section")
                continue
            for key in keys:
                if key not in sections[section]:
                    errors.append(f"{relative_path}: missing {section}.{key}")
                elif sections[section][key] == "":
                    errors.append(f"{relative_path}: empty {section}.{key}")

    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    errors = validate(root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
