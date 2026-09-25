#!/usr/bin/env python3
"""Ground skill eval expectations against the repository.

Live agent sessions cannot run in CI, so ``skills/*/evals/evals.json`` is only
useful when every concrete name it asserts on (artifact files, scripts, gate
names, flags, env vars, CLI modules) still exists somewhere in the repo. This
lint extracts those tokens from ``expected_output`` and ``expectations`` and
looks them up in a word index built from the skill docs, ``docs/``, ``scripts/``,
``ui_clone/``, hook manifests, and agent definitions.

Artifact files (``.json``/``.png``/``.webm``/``.html`` ...) are stricter: a prose
mention does not ground them. They must appear in executable text — code under
``scripts/``, ``ui_clone/``, ``hooks/``, ``bin/``, ``skills/*/scripts/``, or a
fenced command block of a skill doc — either literally or through a constructed
name (``"${SIDE}-styles.json"``, ``f"{name}.png"``), match a ``*`` glob written in
a non-history doc, or be listed in ``DOC_CONTRACT_ARTIFACTS`` with the doc that
tells the agent to write it.

Blocking tokens (exit 1 when ungrounded): file names with a known extension,
``--flags``, ``UI_CLONE_*``-style env vars, ``ui_clone.<module>`` CLI paths,
gate names, and backticked single identifiers. Everything else (Step/Phase
labels, thresholds, hyphenated prose) is reported as advisory only.

Usage: ``python scripts/ci/eval_grounding.py [--json] [--advisory]``
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVAL_GLOB = "skills/*/evals/evals.json"

# Directories and files that count as the grounding corpus. ``tests/`` and the
# eval fixtures themselves are deliberately excluded so an eval cannot ground
# itself.
CORPUS_DIRS = (
    "skills",
    "docs",
    "scripts",
    "ui_clone",
    "hooks",
    "bin",
    ".claude-plugin",
    ".codex",
    ".codex-plugin",
)
CORPUS_FILES = ("README.md", "AGENTS.md", "install.sh", "package.json", "pyproject.toml")
CORPUS_SUFFIXES = {
    ".md",
    ".sh",
    ".py",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".txt",
}
SKIP_DIR_NAMES = {"evals", "node_modules", "__pycache__", "tmp", ".git"}

# Artifact files (run outputs) must be produced by executable code, not merely
# mentioned in prose. Source/doc names (.md, .sh, .py, .css, ...) keep the
# word-index rule because they are files that exist in the repo.
ARTIFACT_EXTENSIONS = ("json", "png", "jpg", "webm", "mp4", "txt", "log", "csv", "html", "svg")
CODE_DIRS = ("scripts", "ui_clone", "hooks", "bin")
CODE_SUFFIXES = {".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ""}
# History/design-log docs describe past behavior; they never ground an artifact glob.
HISTORY_DOC = re.compile(r"(?:^|/)(?:[^/]*history[^/]*|CHANGELOG)\.md$", re.I)
# Inputs the pipeline consumes rather than produces (project manifests, the
# generated app's own files). They exist in every clone target, not in this repo.
INPUT_ARTIFACTS = {"package.json", "tsconfig.json", "index.html"}
# Artifacts written by the agent itself following a doc contract, with no
# script producer. Each entry names the producing doc; the doc must still
# contain the literal name or the entry is stale and the token blocks again.
DOC_CONTRACT_ARTIFACTS = {
    # comparison-fix.md Phase D step 3: "Produce tmp/ref/<component>/pixel-perfect-diff.json".
    "pixel-perfect-diff.json": "skills/visual-debug/comparison-fix.md",
    # verification.md A-C3: "Save the measurements to .../ref-styles.json and .../impl-styles.json".
    "ref-styles.json": "skills/visual-debug/verification.md",
    "impl-styles.json": "skills/visual-debug/verification.md",
    # comparison-page.md: "Generate `$OUT_DIR/compare.html`" (agent-written review page).
    "compare.html": "skills/ui-capture/comparison-page.md",
    # Not an artifact: the forbidden `> image.png` redirect quoted from SKILL.md.
    "image.png": "skills/ui-reverse-engineering/SKILL.md",
}
TEMPLATE_TOKEN = re.compile(
    r"[\w${}<>*()./-]*[${*<][\w${}<>*()./-]*\.(?:" + "|".join(ARTIFACT_EXTENSIONS) + r")\b"
)
FENCED_BLOCK = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.S | re.M)

FILE_EXTENSIONS = (
    "json",
    "md",
    "sh",
    "py",
    "tsx",
    "ts",
    "jsx",
    "js",
    "mjs",
    "css",
    "png",
    "jpg",
    "webm",
    "mp4",
    "txt",
    "yaml",
    "yml",
    "toml",
    "log",
    "html",
    "woff2",
    "woff",
    "svg",
    "csv",
)
FILE_TOKEN = re.compile(
    r"(?<![\w/.@-])((?:[\w<>$()./-]*/)?[\w<>$-]+\.(?:" + "|".join(FILE_EXTENSIONS) + r"))(?![\w.])"
)
BACKTICK = re.compile(r"`([^`]+)`")
# A leading "(" marks a CSS custom property such as var(--brand-color), not a CLI flag.
FLAG = re.compile(r"(?<![\w(-])(--[a-z][\w-]*)")
ENV_VAR = re.compile(
    r"\b((?:UI_CLONE|UI_RE|RECATCH|PLUGIN|CODEX_PLUGIN|CLAUDE_PLUGIN)_[A-Z0-9_]+)\b"
)
MODULE = re.compile(r"\b(ui_clone(?:\.[a-z_]+)+)\b")
HYPHEN_ID = re.compile(r"(?<![\w-])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![\w-])")
STEP_LABEL = re.compile(r"\b((?:Step|Phase) [0-9A-Z][0-9A-Za-z.-]*)")
THRESHOLD = re.compile(r"\b(\d{2,5}x\d{2,5}|\d+(?:\.\d+)?%|(?:AE|SSIM|DSSIM)\s*[<>≤≥]=?\s*[\d.]+)")
WORD = re.compile(r"[\w<>$./-]+")
PLACEHOLDER = re.compile(r"<[^>]+>")
GATE_ORDER_BLOCK = re.compile(r"GATE_ORDER:\s*list\[str\]\s*=\s*\[(.*?)\]", re.S)
GATE_NAME = re.compile(r"\"([a-z0-9-]+)\"")

# Backticked spans that are prose fragments rather than identifiers.
PROSE_CHARS = re.compile(r"[\s:=(){}\[\],'\"]")


@dataclass
class Corpus:
    words: set[str]
    basenames: set[str]
    gates: set[str]
    paths: set[str] = field(default_factory=set)
    path_words: tuple[str, ...] = ()
    text: str = field(default="", repr=False)
    # Artifact names written as literals in executable code.
    code_words: set[str] = field(default_factory=set)
    # Basename regexes from constructed names in code ("${X}-styles.json",
    # f"{name}.png") and from glob patterns in non-history docs ("frames/*.png").
    templates: tuple[re.Pattern[str], ...] = ()
    root: pathlib.Path | None = None

    def has_artifact(self, token: str) -> bool:
        base = token.rsplit("/", 1)[-1]
        if base in INPUT_ARTIFACTS or base in self.code_words:
            return True
        doc = DOC_CONTRACT_ARTIFACTS.get(base)
        if doc and self.root is not None:
            try:
                if base in (self.root / doc).read_text(encoding="utf-8"):
                    return True
            except OSError:
                pass
        if "<" in base:
            pattern = re.compile(_placeholder_regex(base))
            if any(pattern.fullmatch(word) for word in self.code_words):
                return True
        sample = PLACEHOLDER.sub("X", base)
        return any(template.fullmatch(sample) for template in self.templates)

    def has_word(self, token: str) -> bool:
        return token in self.words

    def has_file(self, token: str) -> bool:
        base = token.rsplit("/", 1)[-1]
        return base in self.basenames or base in self.words

    def has_path_suffix(self, token: str) -> bool:
        """True when a doc writes a longer path ending in token, e.g. tmp/ref/<c>/sections/result.txt."""
        suffix = "/" + token
        return any(word.endswith(suffix) for word in self.path_words)


@dataclass
class Finding:
    skill: str
    eval_id: int
    field_name: str
    kind: str
    token: str
    blocking: bool


def _iter_corpus_paths(root: pathlib.Path) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for name in CORPUS_FILES:
        path = root / name
        if path.is_file():
            paths.append(path)
    for name in CORPUS_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if any(part in SKIP_DIR_NAMES for part in path.relative_to(root).parts[:-1]):
                continue
            if path.is_file() and (path.suffix in CORPUS_SUFFIXES or path.suffix == ""):
                paths.append(path)
    return paths


def _placeholder_regex(base: str) -> str:
    """Regex for an eval token whose ``<placeholder>`` parts match any text."""
    return ".+".join(re.escape(part) for part in PLACEHOLDER.split(base))


WILDCARD = re.compile(r"\$\{[^}]*\}|\{[^}]*\}|\$\(?[A-Za-z_]\w*\)?|<[^>]+>|\*")


def _template_regex(raw: str) -> re.Pattern[str] | None:
    """Compile a constructed artifact name (``${X}-styles.json``) to a basename regex.

    Templates with little literal text (``*.json``, ``ref-*.json``) would ground
    unrelated artifacts, so a wildcard template needs 5+ literal letters/digits.
    """
    base = raw.rstrip("\"'`),;").rsplit("/", 1)[-1]
    literal = WILDCARD.sub("", base).rsplit(".", 1)[0]
    if WILDCARD.search(base) and len(re.findall(r"[A-Za-z0-9]", literal)) < 5:
        return None
    if not re.search(r"[A-Za-z]", literal):
        return None
    pieces = WILDCARD.split(base)
    return re.compile("[^/]*".join(re.escape(piece) for piece in pieces))


def _add_template(raw: str, templates: dict[str, re.Pattern[str]]) -> None:
    template = _template_regex(raw)
    if template is not None:
        templates.setdefault(template.pattern, template)


def _is_code_path(relative: str, suffix: str) -> bool:
    # This lint names allowlisted artifacts; it must never ground them itself.
    if suffix not in CODE_SUFFIXES or relative == "scripts/ci/eval_grounding.py":
        return False
    parts = relative.split("/")
    if parts[0] in CODE_DIRS:
        return True
    return len(parts) > 3 and parts[0] == "skills" and parts[2] == "scripts"


def _expand_words(raw: str, words: set[str]) -> None:
    token = raw.strip("./-")
    if not token:
        return
    words.add(raw)
    words.add(token)
    base = token.rsplit("/", 1)[-1]
    words.add(base)
    if "." in base:
        words.add(base.rsplit(".", 1)[0])


def build_corpus(root: pathlib.Path = ROOT) -> Corpus:
    words: set[str] = set()
    basenames: set[str] = set()
    gates: set[str] = set()
    paths: set[str] = set()
    chunks: list[str] = []
    code_words: set[str] = set()
    templates: dict[str, re.Pattern[str]] = {}
    for path in _iter_corpus_paths(root):
        basenames.add(path.name)
        relative = path.relative_to(root).as_posix()
        paths.add(relative)
        if relative.startswith("skills/"):
            inside = relative[len("skills/") :]
            paths.add(inside)  # <skill>/<doc>.md as SKILL.md links write it
            if "/" in inside:
                paths.add(inside.split("/", 1)[1])  # skill-relative, e.g. references/x.md
        if path.suffix == "" and path.stat().st_size > 200_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.append(text)
        for raw in WORD.findall(text):
            _expand_words(raw, words)
        is_doc = path.suffix == ".md" and not HISTORY_DOC.search(relative)
        # Executable text: code files, plus fenced blocks of skill docs (the
        # commands an agent runs, e.g. `echo "$R" > .../sweep-coarse.json`).
        executable = ""
        if _is_code_path(relative, path.suffix):
            executable = text
        elif is_doc and relative.startswith("skills/"):
            executable = "\n".join(FENCED_BLOCK.findall(text))
        for raw in re.findall(r"[\w.-]+", executable):
            code_words.add(raw.strip("."))
        for raw in TEMPLATE_TOKEN.findall(executable):
            _add_template(raw, templates)
        if is_doc:
            for raw in TEMPLATE_TOKEN.findall(text):
                if "*" in raw:  # prose grounds only explicit globs, not placeholders
                    _add_template(raw, templates)
        if path.name == "state.py" and path.parent.name == "ui_clone":
            match = GATE_ORDER_BLOCK.search(text)
            if match:
                gates.update(GATE_NAME.findall(match.group(1)))
    return Corpus(
        words=words,
        basenames=basenames,
        gates=gates,
        paths=paths,
        path_words=tuple(word for word in words if "/" in word),
        text="\n".join(chunks),
        code_words=code_words,
        templates=tuple(templates.values()),
        root=root,
    )


def _normalize(token: str) -> str:
    token = token.strip().rstrip("\"'.,;:)").lstrip("\"'(")
    for prefix in ("$PLUGIN_ROOT/", '"$PLUGIN_ROOT"/', "$(pwd)/", '"$(pwd)"/', "./"):
        if token.startswith(prefix):
            token = token[len(prefix) :]
    while token.startswith("../"):
        token = token[3:]
    return token


def is_artifact(token: str) -> bool:
    return token.rsplit(".", 1)[-1].lower() in ARTIFACT_EXTENSIONS


def _file_grounded(token: str, corpus: Corpus) -> bool:
    token = _normalize(token)
    if is_artifact(token):
        # "a.json/b.json" lists alternatives; each must have a producer.
        parts = token.split("/")
        if len(parts) > 1 and all(FILE_TOKEN.fullmatch(part) for part in parts):
            return all(corpus.has_artifact(part) for part in parts)
        return corpus.has_artifact(token)
    if "/" in token and "<" not in token and "$" not in token:
        # A concrete relative path (e.g. visual-debug/comparison-fix.md) must resolve
        # under skills/, a skill dir, or the repo root; the basename fallback would
        # hide typos. "a.tsx/b.tsx" is prose listing alternatives: ground each part.
        if token in corpus.paths or corpus.has_path_suffix(token):
            return True
        parts = token.split("/")
        if all(FILE_TOKEN.fullmatch(part) for part in parts):
            return all(corpus.has_file(part) for part in parts)
        return False
    if corpus.has_word(token) or corpus.has_file(token):
        return True
    stripped = PLACEHOLDER.sub("", token).strip("/")
    return bool(stripped) and (corpus.has_word(stripped) or corpus.has_file(stripped))


def _identifier_grounded(token: str, corpus: Corpus) -> bool:
    token = _normalize(token)
    return corpus.has_word(token) or corpus.has_file(token) or token in corpus.gates


def extract_tokens(text: str) -> list[tuple[str, str, bool]]:
    """Return (kind, token, blocking) triples found in one eval string."""
    found: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, token: str, blocking: bool) -> None:
        key = (kind, token)
        if key in seen:
            return
        seen.add(key)
        found.append((kind, token, blocking))

    for span in BACKTICK.findall(text):
        span = span.strip()
        if not span:
            continue
        if FILE_TOKEN.fullmatch(span):
            add("file", span, True)
        elif FLAG.fullmatch(span):
            add("flag", span, True)
        elif ENV_VAR.fullmatch(span):
            add("env", span, True)
        elif MODULE.fullmatch(span):
            add("module", span, True)
        elif PROSE_CHARS.search(span) or len(span) < 3:
            add("code-span", span, False)
        else:
            add("identifier", span, True)

    plain = BACKTICK.sub(" ", text)
    for token in FILE_TOKEN.findall(plain):
        add("file", token, True)
    plain = FILE_TOKEN.sub(" ", plain)  # a file name is not also a hyphenated prose id
    for token in FLAG.findall(plain):
        add("flag", token, True)
    for token in ENV_VAR.findall(plain):
        add("env", token, True)
    for token in MODULE.findall(plain):
        add("module", token, True)
    for token in HYPHEN_ID.findall(plain):
        add("hyphen-id", token, False)
    for token in STEP_LABEL.findall(plain):
        add("step-label", token.rstrip(".-"), False)
    for token in THRESHOLD.findall(plain):
        add("threshold", re.sub(r"\s+", " ", token), False)
    return found


def _token_grounded(kind: str, token: str, corpus: Corpus) -> bool:
    if kind == "file":
        return _file_grounded(token, corpus)
    if kind in {"flag", "env", "module", "identifier", "hyphen-id"}:
        if kind == "hyphen-id" and token in corpus.gates:
            return True
        return _identifier_grounded(token, corpus)
    if kind == "step-label":
        return token in corpus.text
    if kind == "threshold":
        number = re.sub(r"^(?:AE|SSIM|DSSIM)\s*[<>≤≥]=?\s*", "", token)
        return number in corpus.text
    return token in corpus.text


def lint_evals(
    corpus: Corpus, eval_paths: list[pathlib.Path], gates_as_blocking: bool = True
) -> list[Finding]:
    findings: list[Finding] = []
    for path in eval_paths:
        skill = path.parent.parent.name
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("evals", []):
            eval_id = int(entry.get("id", 0))
            fields = {"expected_output": [str(entry.get("expected_output", ""))]}
            fields["expectations"] = [str(item) for item in entry.get("expectations", [])]
            for field_name, strings in fields.items():
                for text in strings:
                    for kind, token, blocking in extract_tokens(text):
                        if _token_grounded(kind, token, corpus):
                            continue
                        if kind == "hyphen-id" and gates_as_blocking and _looks_like_gate(token):
                            blocking = True
                        findings.append(Finding(skill, eval_id, field_name, kind, token, blocking))
    return findings


GATE_LIKE_SUFFIXES = ("-check", "-compare", "-coverage", "-gate", "-detect", "-plan", "-diff")
# "re-compare", "non-check" and similar are English prose, not check ids.
PROSE_PREFIXES = ("re-", "non-", "un-", "self-", "auto-", "multi-", "then-")


def _looks_like_gate(token: str) -> bool:
    return token.endswith(GATE_LIKE_SUFFIXES) and not token.startswith(PROSE_PREFIXES)


def eval_paths(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    return sorted(root.glob(EVAL_GLOB))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--advisory", action="store_true", help="also list advisory tokens")
    args = parser.parse_args(argv)

    corpus = build_corpus(ROOT)
    findings = lint_evals(corpus, eval_paths(ROOT))
    blocking = [item for item in findings if item.blocking]
    advisory = [item for item in findings if not item.blocking]

    if args.json:
        print(json.dumps([item.__dict__ for item in findings], indent=2))
        return int(bool(blocking))

    print(
        f"eval grounding: {len(blocking)} blocking, {len(advisory)} advisory ungrounded token(s)"
        f" across {len(eval_paths(ROOT))} eval files"
    )
    rows = blocking + (advisory if args.advisory else [])
    for item in rows:
        marker = "BLOCK" if item.blocking else "advisory"
        print(
            f"  [{marker}] {item.skill} eval {item.eval_id} {item.field_name} {item.kind}: {item.token}"
        )
    if blocking:
        print(
            "Fix: align the eval wording with the current doc/script name, or add the"
            " missing artifact to the skill docs.",
            file=sys.stderr,
        )
    return int(bool(blocking))


if __name__ == "__main__":
    raise SystemExit(main())
