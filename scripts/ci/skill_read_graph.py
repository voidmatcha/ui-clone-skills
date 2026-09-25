#!/usr/bin/env python3
"""Static read-graph of the public skill docs.

Parses each public ``SKILL.md`` and the sub-docs it links, classifies every
link by the sentence that carries it, and reports for each scenario how many
words an agent must load (mandatory path) versus how many it could load
(reachable). Optionally samples Claude session transcripts for real ``Read``
calls that touched ``skills/**/*.md`` — only doc names and counts, never
transcript content.

Usage::

    python3 scripts/ci/skill_read_graph.py [--json] [--top N]
    python3 scripts/ci/skill_read_graph.py --transcripts '*ui-clone-skills*'

Link classes:

* ``always``      — the sentence has no conditional marker ("must read", "read
  once", "see X", ...). Loaded on every run that reaches the doc.
* ``step``        — the link sits in a pipeline step row/table (``Phase``,
  ``Step``, ``Current work`` tables, ``Step T-*`` blocks). Loaded when the
  scenario executes that step (a full clone executes them all).
* ``conditional`` — the sentence carries ``if`` / ``when`` / ``only`` /
  ``unless`` / failure vocabulary. Loaded only when the condition fires.
* ``pointer``     — a cross-reference ("see X", "split from X", "after this
  step return to X") with no read instruction. Reachable, not mandatory.
* ``role``        — subagent contract tables. Read by the delegated worker,
  never by the coordinator; counted as reachable, not mandatory.
* ``index``       — rows of a file index table (``reference-index.md``).
  Reachable, not mandatory.

Links are markdown links, backticked ``x.md`` names, and bare ``x.md`` /
``../skill/x.md`` tokens that resolve to an existing skill doc. Execution verbs
("run", "execute") count as read orders; rows of signal/symptom decision tables
are conditional; a doc cited in parentheses or after "see"/"per" is a pointer.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any

SKILLS_ROOT = pathlib.Path("skills")
PUBLIC_SKILLS = ("ui-reverse-engineering", "ui-capture", "visual-debug")

LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]+>|[^\s)]+)\s*\)")
BACKTICK_DOC_RE = re.compile(r"`([A-Za-z0-9_./-]+\.md)(?:#[^`]*)?`")
# Bare doc names in prose or step rows ("Step T-1: ... — measurement.md"). The
# lookbehind rejects the middle of URLs/paths and link targets already matched by
# LINK_RE; a token only becomes a link when it resolves to an existing skill doc.
BARE_DOC_RE = re.compile(
    r"(?<![\w/.`\[(<-])((?:\.\./)*(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.md)(?![\w/])"
)
CONDITIONAL_RE = re.compile(
    r"\b(if|when|whenever|only|unless|optional(?:ly)?|otherwise|"
    r"on (?:pass|fail(?:ure)?|error)|in case)\b",
    re.I,
)
# Unconditional execution verbs ("Run the classifier eval from X.md", "execute
# the steps in X.md") load the doc just like "read" does.
READ_RE = re.compile(r"\b(read|consult|follow|open|resolve|route[sd]?|run|execute)\b", re.I)
POINTER_RE = re.compile(
    r"^(?:[\s>*→\-]|\*\*)*(?:see|cross-ref|split from|pointer in from|per|from|"
    r"defined in|runs? after|produced by|writes|consults|is a reference for|"
    r"after this step|return to|standard approach from|next:)\b",
    re.I,
)
STEP_TABLE_HEADERS = ("phase", "step", "current work")
ROLE_TABLE_HEADERS = ("role",)
INDEX_TABLE_HEADERS = ("file", "doc", "document", "sub-doc", "sub-document")
# Decision tables whose first column is a detected condition ("| Signal | Next
# step |"): every row fires only when its signal is observed.
CONDITION_TABLE_HEADERS = (
    "signal",
    "symptom",
    "result",
    "condition",
    "outcome",
    "case",
    "trigger",
    "trigger type",
    "triggertype",
    "pattern",
    "bundle pattern",
    "gsap pattern",
    "minified pattern",
    "library",
    "animation type",
    "effect type",
    "content type",
    "state found",
    "problem",
    "mistake",
    "temptation",
    "conflict",
    "engine",
)
# Mid-sentence citation right before the link: "... — see `x.md`", "(per x.md".
CITATION_PREFIX_RE = re.compile(
    r"\b(?:see|cf\.?|per|as in|details in|as described in|defined in)\s*$", re.I
)
STEP_LINE_RE = re.compile(r"^\s*Step\s+T-?\d", re.I)
WORD_RE = re.compile(r"\S+")

CLASS_ORDER = ("always", "step", "conditional", "pointer", "role", "index")


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    klass: str
    context: str


@dataclass
class Scenario:
    name: str
    entry: str
    description: str
    mandatory_classes: frozenset[str]
    force_mandatory: tuple[str, ...] = ()
    exclude: tuple[str, ...] = field(default_factory=tuple)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="reverse-engineering-full-clone",
        entry="skills/ui-reverse-engineering/SKILL.md",
        description="Fresh full-page clone: every pipeline step doc is executed.",
        mandatory_classes=frozenset({"always", "step"}),
    ),
    Scenario(
        name="capture-baseline-only",
        entry="skills/ui-capture/SKILL.md",
        description="Standalone reference capture via the deterministic driver.",
        mandatory_classes=frozenset({"always"}),
        force_mandatory=("skills/ui-capture/references/standalone-driver.md",),
    ),
    Scenario(
        name="visual-debug-single-mismatch",
        entry="skills/visual-debug/SKILL.md",
        description="Diagnose one failing section against existing evidence.",
        mandatory_classes=frozenset({"always"}),
    ),
)


def word_count(path: pathlib.Path) -> int:
    try:
        return len(WORD_RE.findall(path.read_text(encoding="utf-8")))
    except OSError:
        return 0


def _strip_code_fences(text: str) -> list[tuple[str, bool]]:
    """Return (block, in_code_block) pairs with soft-wrapped prose unwrapped.

    Consecutive prose lines of one paragraph or list item are joined into a
    single block so that a sentence split across lines is classified as one
    sentence. Table rows, headings, and code lines stay separate.
    """
    out: list[tuple[str, bool]] = []
    fence = False
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append((" ".join(part.strip() for part in paragraph), False))
            paragraph.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            fence = not fence
            out.append((line, True))
            continue
        if fence:
            flush()
            out.append((line, True))
            continue
        if not stripped or stripped.startswith("#") or "|" in line:
            flush()
            out.append((line, False))
            continue
        if re.match(r"^\s*(?:[-*+]|\d+\.)\s", line) or stripped.startswith(">"):
            flush()
        paragraph.append(line)
    flush()
    return out


def _sentence_for(line: str, position: int) -> str:
    """Return the sentence (or table cell) of ``line`` that contains ``position``."""
    if "|" in line:
        cells = line.split("|")
        offset = 0
        for cell in cells:
            end = offset + len(cell)
            if offset <= position <= end:
                line = cell
                position -= offset
                break
            offset = end + 1
    start = 0
    for match in re.finditer(r"(?<=[.;!?])\s+", line):
        if match.start() <= position:
            start = match.end()
        else:
            break
    end_match = re.search(r"[.;!?](?:\s|$)", line[position:])
    end = position + end_match.end() if end_match else len(line)
    return line[start:end].strip()


def _table_header(lines: list[tuple[str, bool]], index: int) -> str | None:
    """Return the lowercase first header cell of the table containing ``index``."""
    if "|" not in lines[index][0]:
        return None
    cursor = index
    while cursor >= 0 and "|" in lines[cursor][0]:
        cursor -= 1
    header = lines[cursor + 1][0]
    cells = [cell.strip().strip("*").lower() for cell in header.strip().strip("|").split("|")]
    return cells[0] if cells else None


def _parenthetical(line: str, position: int) -> str | None:
    """Return the text of the innermost ``( ... )`` group enclosing ``position``."""
    depth = 0
    start = -1
    for index in range(position - 1, -1, -1):
        char = line[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                start = index
                break
            depth -= 1
    if start < 0:
        return None
    end = line.find(")", position)
    return line[start + 1 : end if end >= 0 else len(line)]


def _is_citation(line: str, position: int) -> bool:
    """True when the doc is cited as a source rather than given as a read order."""
    prefix = line[:position].rstrip("`[ ")
    if CITATION_PREFIX_RE.search(prefix[-40:]):
        return True
    group = _parenthetical(line, position)
    return group is not None and not READ_RE.search(group)


def _classify(context: str, table_header: str | None, line: str, citation: bool = False) -> str:
    if table_header in ROLE_TABLE_HEADERS:
        return "role"
    if table_header in INDEX_TABLE_HEADERS:
        return "index"
    if table_header in CONDITION_TABLE_HEADERS or CONDITIONAL_RE.search(context):
        return "conditional"
    if citation or POINTER_RE.match(context):
        return "pointer"
    if table_header in STEP_TABLE_HEADERS or STEP_LINE_RE.match(line):
        return "step"
    if READ_RE.search(context):
        return "always"
    return "pointer"


def _resolve(source: pathlib.Path, target: str) -> str | None:
    target = target.strip("<>").split("#", 1)[0].split("?", 1)[0]
    if not target.lower().endswith(".md") or target.startswith(("/", "$")):
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
        return None
    cwd = pathlib.Path.cwd().resolve()
    resolved = (source.parent / target).resolve()
    if not resolved.is_file() and target.startswith("skills/"):
        # Repo-root relative spelling (``skills/visual-debug/x.md``).
        resolved = (cwd / target).resolve()
    try:
        rel = resolved.relative_to(cwd)
    except ValueError:
        return None
    if not rel.is_file() or "evals" in rel.parts or not rel.parts or rel.parts[0] != "skills":
        return None
    return rel.as_posix()


def extract_links(path: pathlib.Path) -> list[Link]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = _strip_code_fences(text)
    links: list[Link] = []
    seen: set[tuple[str, str]] = set()
    for index, (line, in_code) in enumerate(lines):
        if in_code and not STEP_LINE_RE.match(line):
            continue
        header = _table_header(lines, index)
        matches = (
            *LINK_RE.finditer(line),
            *BACKTICK_DOC_RE.finditer(line),
            *BARE_DOC_RE.finditer(line),
        )
        for match in matches:
            target = _resolve(path, match.group(1))
            if target is None or target == path.as_posix():
                continue
            context = _sentence_for(line, match.start())
            klass = _classify(context, header, line, _is_citation(line, match.start()))
            key = (target, klass)
            if key in seen:
                continue
            seen.add(key)
            links.append(Link(path.as_posix(), target, klass, context[:160]))
    return links


def build_graph(roots: list[str]) -> dict[str, list[Link]]:
    graph: dict[str, list[Link]] = {}
    queue = list(roots)
    while queue:
        doc = queue.pop()
        if doc in graph:
            continue
        graph[doc] = extract_links(pathlib.Path(doc))
        queue.extend(link.target for link in graph[doc] if link.target not in graph)
    return graph


def _closure(graph: dict[str, list[Link]], start: list[str], classes: frozenset[str]) -> list[str]:
    seen: list[str] = []
    queue = list(start)
    while queue:
        doc = queue.pop(0)
        if doc in seen:
            continue
        seen.append(doc)
        for link in graph.get(doc, []):
            if link.klass in classes and link.target not in seen:
                queue.append(link.target)
    return seen


def _strongest_class(graph: dict[str, list[Link]], doc: str, docs: set[str]) -> str:
    """Return ``<class> <- <source basename>`` for the strongest inbound link."""
    best = len(CLASS_ORDER)
    source_doc = ""
    for source in sorted(docs):
        for link in graph.get(source, []):
            if link.target == doc and CLASS_ORDER.index(link.klass) < best:
                best = CLASS_ORDER.index(link.klass)
                source_doc = "/".join(pathlib.PurePosixPath(source).parts[1:])
    if best == len(CLASS_ORDER):
        return "entry"
    return f"{CLASS_ORDER[best]} <- {source_doc}"


def measure(scenario: Scenario, graph: dict[str, list[Link]], top: int) -> dict[str, Any]:
    start = [scenario.entry, *scenario.force_mandatory]
    mandatory = [
        doc
        for doc in _closure(graph, start, scenario.mandatory_classes)
        if doc not in scenario.exclude
    ]
    reachable = _closure(graph, [scenario.entry], frozenset(CLASS_ORDER))
    words = {doc: word_count(pathlib.Path(doc)) for doc in reachable + mandatory}
    mandatory_set = set(mandatory)
    heavy = sorted(mandatory, key=lambda doc: -words[doc])[:top]
    heavy_reachable = sorted(
        (doc for doc in reachable if doc not in mandatory_set), key=lambda doc: -words[doc]
    )[:top]
    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "entry": scenario.entry,
        "mandatory_docs": len(mandatory),
        "mandatory_words": sum(words[doc] for doc in mandatory),
        "reachable_docs": len(set(reachable) | mandatory_set),
        "reachable_words": sum(words[doc] for doc in set(reachable) | mandatory_set),
        "top_mandatory": [
            {"doc": doc, "words": words[doc], "via": _strongest_class(graph, doc, mandatory_set)}
            for doc in heavy
        ],
        "top_conditional": [{"doc": doc, "words": words[doc]} for doc in heavy_reachable],
        "mandatory": [
            {"doc": doc, "words": words[doc], "via": _strongest_class(graph, doc, mandatory_set)}
            for doc in mandatory
        ],
    }


def measure_all(top: int = 8) -> list[dict[str, Any]]:
    graph = build_graph([scenario.entry for scenario in SCENARIOS])
    return [measure(scenario, graph, top) for scenario in SCENARIOS]


def sample_transcripts(pattern: str) -> dict[str, Any]:
    """Count Read calls on skills/**/*.md in Claude JSONL transcripts (names only)."""
    base = pathlib.Path.home() / ".claude" / "projects"
    doc_re = re.compile(r"(?<![A-Za-z0-9_-])skills/([a-z-]+)/([A-Za-z0-9_./-]+\.md)$")
    counts: collections.Counter[str] = collections.Counter()
    sessions_with_reads = 0
    sessions = 0
    for project in sorted(base.glob(pattern)):
        for transcript in sorted(project.glob("*.jsonl")):
            sessions += 1
            hit = False
            try:
                with transcript.open(encoding="utf-8", errors="ignore") as handle:
                    for raw in handle:
                        if '"Read"' not in raw or "skills/" not in raw:
                            continue
                        try:
                            record = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        content = (record.get("message") or {}).get("content")
                        if not isinstance(content, list):
                            continue
                        for block in content:
                            if not isinstance(block, dict) or block.get("name") != "Read":
                                continue
                            path = str((block.get("input") or {}).get("file_path", ""))
                            match = doc_re.search(path.replace("\\", "/"))
                            if match:
                                counts[f"{match.group(1)}/{match.group(2)}"] += 1
                                hit = True
            except OSError:
                continue
            sessions_with_reads += int(hit)
    return {
        "pattern": pattern,
        "sessions": sessions,
        "sessions_with_skill_reads": sessions_with_reads,
        "reads": [{"doc": doc, "count": count} for doc, count in counts.most_common()],
    }


def _print_report(results: list[dict[str, Any]], transcripts: dict[str, Any] | None) -> None:
    for result in results:
        print(f"== {result['scenario']} — {result['description']}")
        print(
            f"   mandatory: {result['mandatory_words']} words / {result['mandatory_docs']} docs"
            f"   reachable: {result['reachable_words']} words / {result['reachable_docs']} docs"
        )
        print("   heaviest mandatory docs:")
        for row in result["top_mandatory"]:
            print(f"     {row['words']:>6}  {row['doc']}  [{row['via']}]")
        if result["top_conditional"]:
            print("   heaviest conditional-only docs:")
            for row in result["top_conditional"]:
                print(f"     {row['words']:>6}  {row['doc']}")
    if transcripts is not None:
        print(
            f"== transcripts {transcripts['pattern']}: {transcripts['sessions']} sessions,"
            f" {transcripts['sessions_with_skill_reads']} with skill doc reads"
        )
        for row in transcripts["reads"][:25]:
            print(f"     {row['count']:>5}  {row['doc']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--top", type=int, default=8, help="heavy docs per scenario")
    parser.add_argument(
        "--transcripts",
        metavar="GLOB",
        help="sample Read calls from ~/.claude/projects/<GLOB>/*.jsonl (names and counts only)",
    )
    parser.add_argument("--links", action="store_true", help="dump every classified link")
    args = parser.parse_args(argv)

    if not SKILLS_ROOT.is_dir():
        print("run from the repository root (skills/ not found)", file=sys.stderr)
        return 2

    results = measure_all(args.top)
    transcripts = sample_transcripts(args.transcripts) if args.transcripts else None
    if args.links:
        graph = build_graph([scenario.entry for scenario in SCENARIOS])
        for doc, links in sorted(graph.items()):
            for link in links:
                print(f"{doc} -> {link.target} [{link.klass}] :: {link.context}")
        return 0
    if args.json:
        payload: dict[str, Any] = {"scenarios": results}
        if transcripts is not None:
            payload["transcripts"] = transcripts
        print(json.dumps(payload, indent=2))
    else:
        _print_report(results, transcripts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
