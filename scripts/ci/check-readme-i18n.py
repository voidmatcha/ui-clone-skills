#!/usr/bin/env python3
"""Fail closed when localized README files drift from README.md."""

from __future__ import annotations

import argparse
import collections
import hashlib
import re
from pathlib import Path

TRANSLATIONS = {
    "README.ko.md": "🇰🇷 \ud55c\uad6d\uc5b4",
    "README.ja.md": "🇯🇵 日本語",
    "README.zh-cn.md": "🇨🇳 简体中文",
}
LANGUAGE_LABELS = {
    "README.md": "🇺🇸 English",
    **TRANSLATIONS,
}
PROTECTED_TOKENS = (
    "ui-clone-skills",
    "ui-reverse-engineering",
    "ui-capture",
    "visual-debug",
    "transition-spec.json",
    'id="what-it-recovers"',
    'id="skills"',
    "React",
    "Tailwind",
    "GSAP",
    "Framer Motion",
    "Webflow IX2",
    "Lenis",
    "Lottie",
    "AE",
    "SSIM",
    "60 fps",
    "`quick`",
    "`standard`",
    "`comprehensive`",
    "macOS 14+",
    "Ubuntu 22.04+",
    "WSL2",
    "Apache-2.0",
)
ACK_RE = re.compile(
    r"<!-- README-CANONICAL-REVISION: "
    r"sha256=([0-9a-f]{64}); "
    r"bytes=exact-README\.md-UTF-8; "
    r"translation-quality=not-attested -->"
)
FENCE_RE = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
URL_RE = re.compile(r"https://[^\s)\"'<>]+")


def _without_language_links(text: str) -> str:
    for name in LANGUAGE_LABELS:
        text = text.replace(f'href="{name}"', 'href="README.language.md"')
    return text


def check(root: Path) -> list[str]:
    errors: list[str] = []
    canonical_path = root / "README.md"
    try:
        canonical_bytes = canonical_path.read_bytes()
        canonical = canonical_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"README.md: cannot read canonical README: {exc}"]

    digest = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_headings = canonical.count("\n## ")
    canonical_table_rows = sum(line.startswith("|") for line in canonical.splitlines())
    canonical_fences = FENCE_RE.findall(canonical)
    canonical_languages = [lang.strip() for lang, _ in canonical_fences]
    canonical_shell = [body for lang, body in canonical_fences if lang.strip() == "bash"]
    canonical_urls = collections.Counter(URL_RE.findall(_without_language_links(canonical)))

    for name, active_label in TRANSLATIONS.items():
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{name}: cannot read translation: {exc}")
            continue

        ack = ACK_RE.search(text)
        if not ack or ack.group(1) != digest:
            errors.append(f"{name}: canonical revision acknowledgement is missing or stale")
        if text.count('<h1 align="center">UI Clone Skills</h1>') != 1:
            errors.append(f"{name}: centered title must match README.md exactly")
        if text.count(f"<strong>{active_label}</strong>") != 1:
            errors.append(f"{name}: active language label is missing or duplicated")
        for linked_name, label in LANGUAGE_LABELS.items():
            if linked_name == name:
                continue
            if text.count(f'<a href="{linked_name}">{label}</a>') != 1:
                errors.append(f"{name}: language link for {linked_name} is missing or duplicated")

        if text.count("\n## ") != canonical_headings:
            errors.append(f"{name}: section count differs from README.md")
        if sum(line.startswith("|") for line in text.splitlines()) != canonical_table_rows:
            errors.append(f"{name}: table row count differs from README.md")

        fences = FENCE_RE.findall(text)
        if [lang.strip() for lang, _ in fences] != canonical_languages:
            errors.append(f"{name}: fenced code block languages or order differ from README.md")
        shell = [body for lang, body in fences if lang.strip() == "bash"]
        if shell != canonical_shell:
            errors.append(f"{name}: bash installation command differs from README.md")

        urls = collections.Counter(URL_RE.findall(_without_language_links(text)))
        if urls != canonical_urls:
            errors.append(f"{name}: external URL set differs from README.md")

        for token in PROTECTED_TOKENS:
            if text.count(token) != canonical.count(token):
                errors.append(f"{name}: protected token count differs for {token!r}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    errors = check(args.root.resolve())
    if errors:
        for error in errors:
            print(f"README i18n parity: {error}")
        return 1
    print("README i18n parity: all localized READMEs match the canonical contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
