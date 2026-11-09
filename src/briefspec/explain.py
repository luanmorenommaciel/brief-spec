"""Diff-grounded explain-back: a frame built from git, a checker, and an opt-in Stop gate.

Everything here is local: git subprocesses and file reads only, no model or network call.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from briefspec.config import briefspec_home
from briefspec.delivery import _phone_block
from briefspec.errors import BriefSpecError
from briefspec.models import Runtime
from briefspec.state import atomic_write

DEFAULT_BASE = "main"
EXPLAIN_START = "<!-- brief-spec:explain:v1"
EXPLAIN_END = "<!-- /brief-spec:explain -->"
MAX_LOOK_HERE = 3
MAX_PROSE_LINES = 6
HOOK_RUNTIMES = frozenset({Runtime.CLAUDE, Runtime.CODEX})
HOOK_BUDGET_SECONDS = 0.9
_GIT_TIMEOUT_SECONDS = 10.0
_FRAME_SYMBOLS_PER_FILE = 12
_FRAME_SIGNATURES = 12
_MERMAID_FILES = 12

_DEFINITIONS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)",
        r"^\s*class\s+([A-Za-z_]\w*)",
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)",
        r"^\s*(?:export\s+)?(?:declare\s+)?(?:interface|type|enum)\s+([A-Za-z_]\w*)",
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)",
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|trait|impl)\s+([A-Za-z_]\w*)",
        r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
        r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
        r"^([A-Z][A-Z0-9_]{2,})\s*(?::[^=]*)?=(?!=)",
    )
)
_CONSTANT = _DEFINITIONS[-1]
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".lua": "lua",
}


# --------------------------------------------------------------------------- git


class _Git:
    """Run read-only git commands in one repository, optionally under a shared deadline."""

    def __init__(self, cwd: Path, deadline: float | None = None) -> None:
        self.deadline = deadline
        self.root = Path(self.run("rev-parse", "--show-toplevel", cwd=cwd).strip())

    def _timeout(self) -> float:
        if self.deadline is None:
            return _GIT_TIMEOUT_SECONDS
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("explain time budget exhausted")
        return remaining

    def run(self, *args: str, cwd: Path | None = None, stdin: bytes | None = None) -> str:
        return self.run_bytes(*args, cwd=cwd, stdin=stdin).decode("utf-8", errors="replace")

    def run_bytes(self, *args: str, cwd: Path | None = None, stdin: bytes | None = None) -> bytes:
        directory = cwd if cwd is not None else self.root
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=directory,
                input=stdin,
                capture_output=True,
                timeout=self._timeout(),
                check=False,
            )
        except FileNotFoundError as exc:
            raise BriefSpecError("git is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"git {args[0]} timed out") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            message = detail[-1] if detail else f"exit {result.returncode}"
            raise BriefSpecError(f"git {' '.join(args[:2])} failed: {message}")
        return result.stdout


def _resolve_base(git: _Git, base: str | None) -> tuple[str, str]:
    """Return (base ref as named, merge-base commit with HEAD)."""
    candidates = [base] if base else [DEFAULT_BASE, f"origin/{DEFAULT_BASE}"]
    last_error: BriefSpecError | None = None
    for candidate in candidates:
        try:
            merge_base = git.run("merge-base", candidate, "HEAD").strip()
        except BriefSpecError as exc:
            last_error = exc
            continue
        return candidate, merge_base
    raise BriefSpecError(
        f"cannot find the merge-base of {' or '.join(candidates)} with HEAD"
        + (f" ({last_error})" if last_error else "")
    )


# --------------------------------------------------------------------- skeleton


@dataclass(slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    first_changed: int
    changed: int = 0


@dataclass(slots=True)
class FileChange:
    display: str
    path: str
    status: str = "modified"
    binary: bool = False
    added: int = 0
    deleted: int = 0
    hunks: list[Hunk] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    signatures: list[str] = field(default_factory=list)
    before_after: list[tuple[str, str]] = field(default_factory=list)

    def ranges(self) -> list[tuple[int, int]]:
        """Line ranges a reader sees in this file's diff (HEAD side; old side if deleted)."""
        spans: list[tuple[int, int]] = []
        for hunk in self.hunks:
            start, count = (
                (hunk.old_start, hunk.old_count)
                if self.status == "deleted"
                else (hunk.new_start, hunk.new_count)
            )
            if count > 0:
                spans.append((start, start + count - 1))
        return spans

    def contains(self, line: int) -> bool:
        return any(start <= line <= end for start, end in self.ranges())


@dataclass(slots=True)
class Skeleton:
    root: Path
    base: str
    merge_base: str
    head: str
    files: list[FileChange]
    patch: str

    @property
    def display_files(self) -> list[str]:
        return [item.display for item in self.files]

    @property
    def added(self) -> int:
        return sum(item.added for item in self.files)

    @property
    def deleted(self) -> int:
        return sum(item.deleted for item in self.files)

    def by_name(self) -> dict[str, FileChange]:
        index: dict[str, FileChange] = {}
        for item in self.files:
            index[item.path] = item
            index[item.display] = item
        return index


def _definitions(line: str) -> list[str]:
    return [match.group(1) for pattern in _DEFINITIONS if (match := pattern.search(line))]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def _parse_patch(patch: str, displays: list[str], paths: list[str]) -> list[FileChange]:
    sections: list[list[str]] = []
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            sections.append([])
        elif sections:
            sections[-1].append(line)
    if len(sections) != len(paths):
        raise BriefSpecError(
            f"git returned {len(paths)} changed files but {len(sections)} patch sections"
        )
    changes: list[FileChange] = []
    for display, path, lines in zip(displays, paths, sections, strict=True):
        change = FileChange(display=display, path=path)
        removed_defs: dict[str, str] = {}
        added_defs: dict[str, str] = {}
        touched: list[str] = []
        hunk: Hunk | None = None
        old_line = new_line = 0
        for line in lines:
            if hunk is None and not line.startswith("@@"):
                if line.startswith("new file mode"):
                    change.status = "added"
                elif line.startswith("deleted file mode"):
                    change.status = "deleted"
                elif line.startswith("rename to"):
                    change.status = "renamed"
                elif line.startswith(("Binary files", "GIT binary patch")):
                    change.binary = True
                continue
            header = _HUNK_HEADER.match(line)
            if header:
                old_start, old_count, new_start, new_count, context = header.groups()
                hunk = Hunk(
                    old_start=int(old_start),
                    old_count=1 if old_count is None else int(old_count),
                    new_start=int(new_start),
                    new_count=1 if new_count is None else int(new_count),
                    first_changed=0,
                )
                change.hunks.append(hunk)
                old_line, new_line = hunk.old_start, hunk.new_start
                touched.extend(_definitions(context))
                continue
            if line.startswith("+"):
                change.added += 1
                hunk.changed += 1
                hunk.first_changed = hunk.first_changed or new_line
                for name in _definitions(line[1:]):
                    added_defs.setdefault(name, line[1:].strip())
                new_line += 1
            elif line.startswith("-"):
                change.deleted += 1
                hunk.changed += 1
                if not hunk.first_changed:
                    hunk.first_changed = old_line if change.status == "deleted" else new_line
                for name in _definitions(line[1:]):
                    removed_defs.setdefault(name, line[1:].strip())
                old_line += 1
            elif line.startswith(" "):
                old_line += 1
                new_line += 1
        change.symbols = _dedupe([*added_defs, *removed_defs, *touched])
        callables = [name for name in added_defs if not _CONSTANT.match(added_defs[name])]
        change.signatures = [added_defs[name] for name in callables]
        change.before_after = [
            (removed_defs[name], added_defs[name])
            for name in callables
            if name in removed_defs and removed_defs[name] != added_defs[name]
        ]
        changes.append(change)
    return changes


def build_skeleton(
    cwd: Path | None = None,
    base: str | None = None,
    *,
    deadline: float | None = None,
) -> Skeleton:
    """Collect the diff of HEAD against its merge-base with ``base`` (default ``main``)."""
    git = _Git((cwd or Path.cwd()).resolve(), deadline)
    base_ref, merge_base = _resolve_base(git, base)
    head = git.run("rev-parse", "HEAD").strip()
    spec = f"{base_ref}...HEAD"
    displays = [line for line in git.run("diff", "--name-only", spec).split("\n") if line]
    raw = git.run_bytes("diff", "--name-only", "-z", spec)
    paths = [item.decode("utf-8", errors="replace") for item in raw.split(b"\0") if item]
    patch = git.run("diff", "--no-color", "--no-ext-diff", "--no-textconv", "-U3", spec)
    files = _parse_patch(patch, displays, paths)
    return Skeleton(
        root=git.root,
        base=base_ref,
        merge_base=merge_base,
        head=head,
        files=files,
        patch=patch,
    )


# ------------------------------------------------------------------------ frame


def _file_tree(paths: list[str]) -> list[str]:
    tree: dict[str, Any] = {}
    for path in paths:
        node = tree
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node.setdefault(parts[-1], None)

    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        for name in sorted(node, key=lambda item: (not item.endswith("/"), item)):
            lines.append("  " * depth + name)
            if node[name] is not None:
                walk(node[name], depth + 1)

    walk(tree, 0)
    return lines


def _mermaid_label(value: str) -> str:
    return value.replace('"', "#quot;")


def _mermaid_starter(paths: list[str]) -> list[str] | None:
    directories = _dedupe([str(PurePosixPath(path).parent) for path in paths])
    if len(directories) < 2:
        return None
    lines = ["flowchart LR"]
    shown = paths[:_MERMAID_FILES]
    ids: dict[str, str] = {}
    for index, path in enumerate(shown):
        parent = str(PurePosixPath(path).parent)
        if parent not in ids:
            ids[parent] = f"d{len(ids)}"
            label = "./" if parent == "." else parent + "/"
            lines.append(f'  {ids[parent]}["{_mermaid_label(label)}"]')
        name = _mermaid_label(PurePosixPath(path).name)
        lines.append(f'  {ids[parent]} --> f{index}["{name}"]')
    if len(paths) > len(shown):
        lines.append(f"  %% {len(paths) - len(shown)} more changed files are not drawn")
    return lines


def _language(paths: list[str]) -> str:
    languages = {_LANGUAGES.get(PurePosixPath(path).suffix.lower()) for path in paths}
    if len(languages) == 1:
        return languages.pop() or "text"
    return "text"


def _look_here(skeleton: Skeleton) -> list[str]:
    candidates = [
        (hunk.changed, change.display, hunk.first_changed)
        for change in skeleton.files
        for hunk in change.hunks
        if hunk.first_changed and change.contains(hunk.first_changed)
    ]
    candidates.sort(key=lambda item: -item[0])
    chosen: list[str] = []
    seen: set[str] = set()
    for _, display, line in candidates:
        if display in seen:
            continue
        seen.add(display)
        chosen.append(f"{display}:{line}")
        if len(chosen) == MAX_LOOK_HERE:
            break
    return chosen


def _visual_fits(skeleton: Skeleton) -> list[str]:
    files = skeleton.files
    functions = [symbol for change in files for symbol in change.symbols]
    fits = [f"File tree: {len(files)} changed file(s), from git."]
    if _mermaid_starter([change.path for change in files]):
        directories = {str(PurePosixPath(change.path).parent) for change in files}
        fits.append(
            f"Mermaid: the change spans {len(directories)} directories; "
            "redraw the starter as the real flow."
        )
    if any(change.signatures for change in files):
        fits.append(
            "Signatures: definitions were added or changed; lines below come from the diff."
        )
    if any(change.before_after for change in files):
        fits.append("Before / after: at least one definition line changed.")
    if len(functions) >= 2:
        fits.append(
            f"Call stack: {len(functions)} changed symbols; show who calls whom, outermost first."
        )
    if any(hunk.changed >= 20 for change in files for hunk in change.hunks):
        fits.append("Pseudocode: one hunk changes 20+ lines; compress its logic.")
    return fits


def _header(skeleton: Skeleton, output_format: str) -> str:
    return (
        f"{EXPLAIN_START} base={skeleton.base} merge-base={skeleton.merge_base[:12]} "
        f"head={skeleton.head[:12]} format={output_format} -->"
    )


def _diff_line(skeleton: Skeleton) -> str:
    return (
        f"Diff: `{skeleton.base}...HEAD`, {len(skeleton.files)} file(s), "
        f"+{skeleton.added} −{skeleton.deleted}"
    )


def _file_entries(skeleton: Skeleton) -> list[str]:
    entries = []
    for change in skeleton.files:
        size = "binary" if change.binary else f"+{change.added} −{change.deleted}"
        entries.append(f"`{change.display}` ({size}) — ")
    return entries


def _symbol_entries(skeleton: Skeleton) -> list[str]:
    entries = []
    for change in skeleton.files:
        if change.symbols:
            names = ", ".join(f"`{name}`" for name in change.symbols[:_FRAME_SYMBOLS_PER_FILE])
            extra = len(change.symbols) - _FRAME_SYMBOLS_PER_FILE
            entries.append(
                f"`{change.display}`: {names}" + (f" (+{extra} more)" if extra > 0 else "")
            )
    return entries


def render_frame_markdown(skeleton: Skeleton) -> str:
    paths = [change.path for change in skeleton.files]
    out = [_header(skeleton, "markdown"), f"## Explain-back · {_diff_line(skeleton)}", ""]
    if not skeleton.files:
        out += ["No committed changes against the base; nothing to explain.", EXPLAIN_END]
        return "\n".join(out) + "\n"
    out += [
        "### Summary",
        "<!-- Exactly 3 lines: what changed, why, and what to watch. -->",
        "",
        "### Visuals",
        "<!-- Keep the visuals that fit this change and delete the rest. Every name in a call "
        "stack, signature or pseudocode block must appear in the diff or a touched file; a file "
        "tree may list only changed files. -->",
        *(f"<!-- Fits: {fit} -->" for fit in _visual_fits(skeleton)),
        "",
        "#### File tree",
        "```text",
        *_file_tree(paths),
        "```",
    ]
    mermaid = _mermaid_starter(paths)
    if mermaid:
        out += ["", "#### Mermaid", "```mermaid", *mermaid, "```"]
    signatures = _dedupe([line for change in skeleton.files for line in change.signatures])
    if signatures:
        language = _language([c.path for c in skeleton.files if c.signatures])
        out += ["", "#### Signatures", f"```{language}", *signatures[:_FRAME_SIGNATURES], "```"]
    pairs = [pair for change in skeleton.files for pair in change.before_after]
    if pairs:
        out += ["", "#### Before / after", "```diff"]
        for before, after in pairs[:_FRAME_SIGNATURES]:
            out += [f"- {before}", f"+ {after}"]
        out.append("```")
    out += [
        "",
        "### Files",
        "<!-- One line per file, after the dash. -->",
        *(f"- {entry}" for entry in _file_entries(skeleton)),
        "",
        "### Look here first",
        f"<!-- At most {MAX_LOOK_HERE}, each path:line inside the diff. Suggested from the "
        "largest hunks. -->",
        *(f"- `{entry}` — " for entry in _look_here(skeleton)),
    ]
    symbols = _symbol_entries(skeleton)
    if symbols:
        out += ["", "### Changed symbols", *(f"- {entry}" for entry in symbols)]
    out.append(EXPLAIN_END)
    return "\n".join(out) + "\n"


def render_frame_phone(skeleton: Skeleton) -> str:
    """Answer first, short stacked blocks, no tables and no Mermaid (it does not render)."""
    paths = [change.path for change in skeleton.files]
    blocks = [_header(skeleton, "phone") + "\n**" + _diff_line(skeleton).replace(":", ":**", 1)]
    if not skeleton.files:
        blocks.append("No committed changes against the base; nothing to explain.")
        return "\n\n".join(blocks) + "\n" + EXPLAIN_END + "\n"
    blocks += [
        "**Summary:**\n<!-- Exactly 3 short lines: what changed, why, what to watch. -->",
        _phone_block("Look here first", [f"`{entry}` — " for entry in _look_here(skeleton)]),
        _phone_block("Files", _file_entries(skeleton)),
        "**File tree:**\n```text\n" + "\n".join(_file_tree(paths)) + "\n```",
    ]
    symbols = _symbol_entries(skeleton)
    if symbols:
        blocks.append(_phone_block("Changed symbols", symbols))
    return "\n\n".join(blocks) + "\n" + EXPLAIN_END + "\n"


def render_explain_frame(skeleton: Skeleton, output_format: str = "markdown") -> str:
    if output_format == "phone":
        return render_frame_phone(skeleton)
    if output_format == "markdown":
        return render_frame_markdown(skeleton)
    raise ValueError(f"Unsupported explain format: {output_format}")


# ------------------------------------------------------------------------ check

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_LABEL = re.compile(r"^\*\*(.+?):\*\*")
_STRUCTURED = {"files", "look here first", "changed symbols"}
_KINDS = (
    ("mermaid", "mermaid"),
    ("file tree", "tree"),
    ("tree", "tree"),
    ("call stack", "callstack"),
    ("callstack", "callstack"),
    ("signature", "signatures"),
    ("type", "signatures"),
    ("pseudocode", "pseudocode"),
    ("before", "before-after"),
)
_SYMBOL_KINDS = {"callstack", "signatures", "pseudocode"}
_KEYWORD_TEXT = """
    and as assert async await break case catch class const continue def default del do elif
    else end enum except export extends false finally fn for from func function if impl import
    in interface is lambda let loop match mod mut new nil none not null or pass pub raise return
    self static struct super switch then this throw trait true try type typeof use var void
    when where while with yield None True False Self
    bool bytes dict float int list object set str tuple any all len print range sorted min max
    sum map filter zip enumerate isinstance open repr iter next string number boolean
"""
_KEYWORDS = frozenset(_KEYWORD_TEXT.split())
_PATH_TOKEN = re.compile(
    r"[\w.\-]*/[\w./\-]*|\b[\w\-]+\.(?:py|pyi|js|mjs|cjs|jsx|ts|tsx|go|rs|rb|java|kt|md|json|"
    r"toml|ya?ml|sh|c|h|cc|cpp|hpp|cs|swift|sql|txt|lua|lock)\b(?::\d+)?"
)
_STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
_COMMENT = re.compile(r"(?:^|\s)(?:#|//|--\s).*$")
_CODE_TOKEN = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")


@dataclass(frozen=True, slots=True)
class ExplainCheck:
    valid: bool
    errors: tuple[str, ...]
    base: str
    files: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "base": self.base,
            "files": self.files,
        }


@dataclass(slots=True)
class _Block:
    kind: str | None
    info: str
    line: int
    body: list[str]


def _section_name(line: str) -> str | None:
    match = _HEADING.match(line) or _LABEL.match(line.strip())
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1).strip().strip("*").lower())


def _kind(info: str, section: str | None) -> str | None:
    """A fence's info string names its visual exactly; otherwise its heading does."""
    named = {needle.replace(" ", ""): kind for needle, kind in _KINDS}
    if info.lower() in named:
        return named[info.lower()]
    for needle, kind in _KINDS:
        if needle in (section or ""):
            return kind
    return None


@dataclass(slots=True)
class _Document:
    blocks: list[_Block] = field(default_factory=list)
    sections: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    prose_errors: list[str] = field(default_factory=list)
    base: str | None = None


def _parse_document(text: str) -> _Document:
    document = _Document()
    marker = re.search(r"brief-spec:explain:v1\b[^>]*\bbase=(\S+)", text)
    if marker:
        document.base = marker.group(1)
    section: str | None = None
    fence: tuple[str, _Block] | None = None
    in_comment = False
    run_start = 0
    run = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if fence is not None:
            closing = _FENCE.match(line)
            if (
                closing
                and not closing.group(2)
                and closing.group(1)[0] == fence[0][0]
                and len(closing.group(1)) >= len(fence[0])
            ):
                block = fence[1]
                if block.kind is None and any("├" in item or "└" in item for item in block.body):
                    block.kind = "tree"
                fence = None
            else:
                fence[1].body.append(line)
            continue
        stripped = line.strip()
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        opening = _FENCE.match(line)
        if opening:
            block = _Block(
                kind=_kind(opening.group(2), section), info=opening.group(2), line=number, body=[]
            )
            document.blocks.append(block)
            fence = (opening.group(1), block)
            run = 0
            continue
        name = _section_name(line)
        if name is not None:
            section = name
            if section in _STRUCTURED:
                run = 0
                rest = _LABEL.sub("", stripped, count=1).strip() if _LABEL.match(stripped) else ""
                if rest:
                    document.sections.setdefault(section, []).append((number, rest))
            continue
        if not stripped:
            continue
        if section in _STRUCTURED:
            document.sections.setdefault(section, []).append((number, stripped))
            continue
        if run == 0:
            run_start = number
        run += 1
        if run == MAX_PROSE_LINES + 1:
            document.prose_errors.append(
                f"line {run_start}: prose block runs past {MAX_PROSE_LINES} lines without a "
                "visual; split it with a diagram, tree, call stack, signature or pseudocode"
            )
    if fence is not None:
        document.prose_errors.append(f"line {fence[1].line}: code fence is never closed")
    return document


def _bullets(entries: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [
        (number, re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", value))
        for number, value in entries
        if re.match(r"^(?:[-*+]|\d+[.)])\s+\S", value)
    ]


def _first_reference(value: str) -> str:
    code = re.match(r"`([^`]+)`", value)
    if code:
        return code.group(1).strip()
    return re.split(r"\s+[—–-]\s+|\s+\(|\s+", value.strip(), maxsplit=1)[0].strip()


def _tree_paths(body: list[str], repo_name: str) -> tuple[list[str], list[str]]:
    """Return (file paths, directory paths) named by a file-tree block."""
    entries: list[tuple[int, str]] = []
    for raw in body:
        line = re.split(r"\s+(?:#|—|–|<-|←)\s*", raw.rstrip(), maxsplit=1)[0]
        match = re.match(r"^([\s│├└┬┼─|`+\-]*)(.*)$", line)
        prefix, name = match.group(1), match.group(2).strip()
        if not name or name in {".", "./"}:
            continue
        entries.append((len(prefix.expandtabs(4)), name))
    if (
        entries
        and entries[0][1].rstrip("/") == repo_name
        and all(depth > entries[0][0] for depth, _ in entries[1:])
    ):
        entries = entries[1:]
    files: list[str] = []
    directories: list[str] = []
    stack: list[tuple[int, str]] = []
    for index, (depth, name) in enumerate(entries):
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = "/".join(part.strip("/") for _, part in stack)
        path = f"{parent}/{name.strip('/')}" if parent else name.strip("/")
        following = entries[index + 1][0] if index + 1 < len(entries) else -1
        if name.endswith("/") or following > depth:
            directories.append(path)
            stack.append((depth, name))
        else:
            files.append(path)
    return files, directories


def _code_symbols(body: list[str], kind: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for offset, raw in enumerate(body):
        line = _STRING.sub(" ", raw)
        line = _COMMENT.sub(" ", line)
        line = _PATH_TOKEN.sub(" ", line)
        for match in _CODE_TOKEN.finditer(line):
            token = match.group(0)
            follows_call = line[match.end() :].lstrip().startswith("(")
            parts = token.split(".")
            for index, part in enumerate(parts):
                if part in _KEYWORDS or len(part) < 2:
                    continue
                is_last = index == len(parts) - 1
                code_like = (
                    len(parts) > 1
                    or (is_last and follows_call)
                    or "_" in part.strip("_")
                    or part.startswith("_")
                    or re.search(r"[a-z][A-Z]", part) is not None
                    or (kind == "signatures" and part[0].isupper())
                )
                if code_like:
                    found.append((offset, part))
    return found


def _vocabulary(skeleton: Skeleton) -> set[str]:
    words = set(_IDENTIFIER.findall(skeleton.patch))
    requests = []
    for change in skeleton.files:
        if change.binary:
            continue
        revision = skeleton.merge_base if change.status == "deleted" else skeleton.head
        requests.append(f"{revision}:{change.path}")
    if not requests:
        return words
    git = _Git(skeleton.root)
    output = git.run_bytes("cat-file", "--batch", stdin=("\n".join(requests) + "\n").encode())
    cursor = 0
    while cursor < len(output):
        newline = output.index(b"\n", cursor)
        header = output[cursor:newline].split()
        cursor = newline + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            words.update(
                _IDENTIFIER.findall(output[cursor : cursor + size].decode("utf-8", "replace"))
            )
            cursor += size + 1
    return words


_MERMAID_TYPES = {
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "classDiagram-v2",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "gantt",
    "pie",
    "journey",
    "gitGraph",
    "mindmap",
    "timeline",
    "quadrantChart",
    "requirementDiagram",
    "C4Context",
    "C4Container",
    "C4Component",
    "sankey-beta",
    "xychart-beta",
    "block-beta",
    "packet-beta",
    "architecture-beta",
    "kanban",
}
_DIRECTIONS = {"TB", "TD", "BT", "RL", "LR"}
_EDGE_END = re.compile(r"(?:--+>?|==+>?|-\.+->?|~~~|--o|--x|<--+>?)\s*(?:\|[^|]*\|)?\s*$")
_EDGE_START = re.compile(r"^(?:--+>?|==+>?|-\.+->?|~~~)")
_PAIRS = {"(": ")", "[": "]", "{": "}"}


def mermaid_errors(source: str) -> list[str]:
    """Offline structural check for a Mermaid block.

    It is not Mermaid's grammar; CI also runs the real Mermaid parser over the generated and
    fixture blocks and asserts that both agree.
    """
    lines = source.splitlines()
    if lines and lines[0].strip() == "---":
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if closing is None:
            return ["front matter is never closed"]
        lines = lines[closing + 1 :]
    body = [
        (number, line.strip())
        for number, line in enumerate(lines, start=1)
        if line.strip() and not line.strip().startswith("%%")
    ]
    if not body:
        return ["diagram is empty"]
    header = body[0][1].split()
    if header[0] not in _MERMAID_TYPES:
        return [f"unknown diagram type {header[0]!r}"]
    errors: list[str] = []
    flow = header[0] in {"flowchart", "graph"}
    if flow and len(header) > 1 and header[1] not in _DIRECTIONS:
        errors.append(f"unknown flowchart direction {header[1]!r}")
    statements = body[1:]
    if not statements:
        errors.append("diagram has no statements")
    depth = 0
    # Flowchart shapes close on their own line; class, state and ER bodies span lines.
    stack: list[tuple[str, int]] = []
    for number, line in statements:
        quote = False
        for char in line:
            if char == '"':
                quote = not quote
            elif quote:
                continue
            elif char in _PAIRS:
                stack.append((_PAIRS[char], number))
            elif char in _PAIRS.values() and (not stack or stack.pop()[0] != char):
                errors.append(f"line {number}: unbalanced {char!r}")
                stack = []
                break
        else:
            if quote:
                errors.append(f"line {number}: unterminated string")
        if flow and stack:
            errors.append(f"line {number}: unclosed {stack[-1][0]!r}")
            stack = []
        if flow:
            first = line.split()[0]
            if first == "subgraph":
                depth += 1
            elif first == "end":
                depth -= 1
                if depth < 0:
                    errors.append(f"line {number}: 'end' without 'subgraph'")
            elif _EDGE_END.search(line) or _EDGE_START.match(line):
                errors.append(f"line {number}: edge has no target")
    if flow and depth > 0:
        errors.append("subgraph is never closed with 'end'")
    if stack:
        errors.append(f"line {stack[-1][1]}: unclosed {stack[-1][0]!r}")
    return errors


def check_explain(text: str, cwd: Path | None = None, base: str | None = None) -> ExplainCheck:
    document = _parse_document(text)
    skeleton = build_skeleton(cwd, base or document.base)
    index = skeleton.by_name()
    errors: list[str] = list(document.prose_errors)

    files = _bullets(document.sections.get("files", []))
    named: list[str] = []
    for number, value in files:
        reference = _first_reference(value)
        if reference not in index:
            errors.append(
                f"line {number}: file summary names {reference!r}, which is not in the diff"
            )
        else:
            named.append(index[reference].display)
    missing = [display for display in skeleton.display_files if display not in named]
    if missing:
        errors.append(
            "file list does not match `git diff --name-only "
            f"{skeleton.base}...HEAD`; missing: " + ", ".join(missing)
        )
    duplicates = sorted({name for name in named if named.count(name) > 1})
    if duplicates:
        errors.append("file list repeats: " + ", ".join(duplicates))

    look = _bullets(document.sections.get("look here first", []))
    if len(look) > MAX_LOOK_HERE:
        errors.append(f"look-here list has {len(look)} entries; the limit is {MAX_LOOK_HERE}")
    for number, value in look:
        reference = _first_reference(value)
        match = re.match(r"^(.+?):(\d+)(?:-(\d+))?$", reference)
        if not match:
            errors.append(f"line {number}: look-here entry {reference!r} is not path:line")
            continue
        path, start, end = (
            match.group(1),
            int(match.group(2)),
            int(match.group(3) or match.group(2)),
        )
        change = index.get(path)
        if change is None:
            errors.append(f"line {number}: look-here file {path!r} is not in the diff")
        elif not (change.contains(start) and change.contains(end)):
            errors.append(f"line {number}: {reference} is outside the diff hunks of {path}")

    visuals = [block for block in document.blocks if block.kind]
    if skeleton.files and not visuals:
        errors.append("no visual: add a file tree, Mermaid, call stack, signatures or pseudocode")
    vocabulary: set[str] | None = None
    raw_paths = {change.path for change in skeleton.files}
    for block in document.blocks:
        if block.kind == "mermaid":
            errors += [
                f"line {block.line}: Mermaid does not parse: {problem}"
                for problem in mermaid_errors("\n".join(block.body))
            ]
        elif block.kind == "tree":
            tree_files, tree_dirs = _tree_paths(block.body, skeleton.root.name)
            for path in tree_files:
                if path not in raw_paths:
                    errors.append(f"line {block.line}: file tree lists {path!r}, not in the diff")
            for path in tree_dirs:
                if not any(item.startswith(path + "/") for item in raw_paths):
                    errors.append(
                        f"line {block.line}: file tree directory {path + '/'!r} has no changed file"
                    )
        elif block.kind in _SYMBOL_KINDS:
            if vocabulary is None:
                vocabulary = _vocabulary(skeleton)
            unknown = _dedupe(
                [
                    name
                    for _, name in _code_symbols(block.body, block.kind)
                    if name not in vocabulary
                ]
            )
            for name in unknown:
                errors.append(
                    f"line {block.line}: {block.kind} names {name!r}, which is not in the diff "
                    "or any touched file"
                )
    return ExplainCheck(
        valid=not errors, errors=tuple(errors), base=skeleton.base, files=len(skeleton.files)
    )


# ------------------------------------------------------------------------- hook


@dataclass(frozen=True, slots=True)
class HookSettings:
    enabled: bool
    min_files: int
    min_lines: int


def hook_settings(config: dict[str, Any]) -> HookSettings:
    section = config.get("explain", {})
    section = section if isinstance(section, dict) else {}

    def positive(name: str, default: int) -> int:
        value = section.get(name, default)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else default
        )

    return HookSettings(
        enabled=section.get("hook") is True,
        min_files=positive("hook_min_files", 3),
        min_lines=positive("hook_min_lines", 100),
    )


def _fingerprint(git: _Git, merge_base: str) -> str:
    trees = git.run("rev-parse", f"{merge_base}^{{tree}}", "HEAD^{tree}").split()
    return hashlib.sha256(" ".join(trees).encode()).hexdigest()


def _fingerprint_path(root: Path) -> Path:
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
    return briefspec_home() / "explain" / f"{key}.json"


def _stored_fingerprint(root: Path) -> str | None:
    path = _fingerprint_path(root)
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("fingerprint") if isinstance(value, dict) else None


def _store_fingerprint(root: Path, fingerprint: str, source: str) -> None:
    record = {
        "fingerprint": fingerprint,
        "source": source,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    atomic_write(_fingerprint_path(root), json.dumps(record, sort_keys=True).encode())


def record_explained(cwd: Path, config: dict[str, Any]) -> None:
    """Remember the diff an explain frame was printed for, so the hook does not ask again."""
    if not hook_settings(config).enabled:
        return
    git = _Git(cwd.resolve())
    _, merge_base = _resolve_base(git, None)
    _store_fingerprint(git.root, _fingerprint(git, merge_base), "explain")


_SHORTSTAT = re.compile(
    r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?"
)


def stop_gate(
    cwd: Path,
    config: dict[str, Any],
    *,
    budget: float | None = None,
) -> str | None:
    """Return a block reason once per large, unexplained diff; otherwise None.

    Callers must treat any exception as pass-through.
    """
    settings = hook_settings(config)
    if not settings.enabled:
        return None
    deadline = time.monotonic() + (HOOK_BUDGET_SECONDS if budget is None else budget)
    git = _Git(cwd.resolve(), deadline)
    base, merge_base = _resolve_base(git, None)
    stat = _SHORTSTAT.search(git.run("diff", "--shortstat", f"{base}...HEAD"))
    if stat is None:
        return None
    files = int(stat.group(1))
    lines = int(stat.group(2) or 0) + int(stat.group(3) or 0)
    if files < settings.min_files and lines < settings.min_lines:
        return None
    fingerprint = _fingerprint(git, merge_base)
    if _stored_fingerprint(git.root) == fingerprint:
        return None
    frame = render_frame_markdown(build_skeleton(git.root, base, deadline=deadline))
    _store_fingerprint(git.root, fingerprint, "hook")
    return (
        f"Brief-Spec explain-back: this branch changed {files} file(s), {lines} line(s) against "
        f"{base} since the last explain. Before you stop, fill this frame in your reply: write "
        "the summary and one line per file, keep the visuals that fit and delete the rest. "
        "`brief-spec explain` prints it again; `brief-spec explain --check <file>` validates "
        "it. This asks once per diff.\n\n" + frame
    )
