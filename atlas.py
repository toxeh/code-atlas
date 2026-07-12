#!/usr/bin/env python3
"""Code Atlas — portable navigation index toolkit (stdlib only).

Commands:
  init      Bootstrap docs/map skeleton from templates
  gen       Generate L3 index (tree, hot files, outlines, symbols)
  validate  Check ATLAS/modules/generated consistency
  outline   Print outline for a single file (debug)

Designed to be copied into any repository under tools/code-atlas/.
No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

TOOL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = TOOL_DIR / "templates"
DEFAULT_CONFIG_NAME = "atlas.config.yaml"

# ---------------------------------------------------------------------------
# Minimal YAML subset (enough for our config; no PyYAML required)
# ---------------------------------------------------------------------------

def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "" or s in ("null", "Null", "NULL", "~"):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # int / float
    try:
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
    except ValueError:
        pass
    # inline comment strip for bare words
    if " #" in s:
        s = s.split(" #", 1)[0].rstrip()
    return s


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i].rstrip()
    return line.rstrip()


def load_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a restricted YAML subset used by atlas configs.

    Supports: nested maps, lists of scalars, nested maps under list-less keys,
    modules.<id>.paths lists. Does not support advanced YAML features.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]  # indent, container

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()

        # pop to parent
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"Invalid YAML indent near: {raw!r}")
        parent = stack[-1][1]

        if content.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"List item without list parent near: {raw!r}")
            item_raw = content[2:].strip()
            if item_raw.endswith(":") and not item_raw.startswith("{"):
                # map item in list (rare) — create dict
                key = item_raw[:-1].strip()
                d: dict[str, Any] = {}
                # not fully supported; store as {key: None}
                parent.append({key: None})
                stack.append((indent, parent[-1]))
            else:
                parent.append(_parse_scalar(item_raw))
            continue

        if ":" not in content:
            raise ValueError(f"Expected key: value near: {raw!r}")

        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()

        if not isinstance(parent, dict):
            raise ValueError(f"Key under non-dict near: {raw!r}")

        if rest == "" or rest == "|" or rest == ">":
            # Lookahead: list or map or empty
            # Peek next non-empty
            j = i
            next_line = None
            while j < len(lines):
                peek = lines[j]
                if peek.strip() and not peek.lstrip().startswith("#"):
                    next_line = _strip_comment(peek)
                    break
                j += 1
            if next_line is None:
                parent[key] = None
            else:
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                next_content = next_line.strip()
                if next_indent > indent and next_content.startswith("- "):
                    lst: list[Any] = []
                    parent[key] = lst
                    stack.append((indent, lst))
                elif next_indent > indent:
                    d = {}
                    parent[key] = d
                    stack.append((indent, d))
                else:
                    parent[key] = None
        else:
            parent[key] = _parse_scalar(rest)

    return root


def dump_simple_yaml(data: dict[str, Any]) -> str:
    """Serialize a limited dict/list structure back to YAML-ish text."""

    def emit(obj: Any, indent: int = 0) -> list[str]:
        sp = "  " * indent
        out: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    out.append(f"{sp}{k}:")
                    out.extend(emit(v, indent + 1))
                elif v is None:
                    out.append(f"{sp}{k}:")
                elif isinstance(v, bool):
                    out.append(f"{sp}{k}: {'true' if v else 'false'}")
                else:
                    out.append(f"{sp}{k}: {v}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    out.append(f"{sp}-")
                    out.extend(emit(item, indent + 1))
                else:
                    out.append(f"{sp}- {item}")
        else:
            out.append(f"{sp}{obj}")
        return out

    return "\n".join(emit(data)) + "\n"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE = [
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/node_modules/**",
    "**/vendor/**",
    "**/dist/**",
    "**/build/**",
    "**/.gradle/**",
    "**/.idea/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/target/**",
    "**/docs/map/generated/**",
]

DEFAULT_EXTENSIONS = [
    ".go", ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".bash", ".zsh",
    ".sql", ".proto", ".graphql", ".vue", ".svelte",
]


@dataclass
class Thresholds:
    outline_min_lines: int = 200
    hot_files_top: int = 30
    max_symbols_per_file: int = 200
    module_card_soft_max_lines: int = 120


@dataclass
class AtlasConfig:
    root: Path
    map_dir: Path
    generated_dir: Path
    modules_dir: Path
    exclude: list[str]
    include_extensions: set[str]
    thresholds: Thresholds
    modules: dict[str, list[str]]  # module_id -> path globs
    use_git_churn: bool = True
    git_churn_max_commits: int = 200
    # Follow directory symlinks (e.g. ./claudia -> /other/claudia).
    # Index paths stay logical under root (claudia/...), not the realpath target.
    follow_dir_symlinks: bool = True
    config_path: Path | None = None


def default_config(repo_root: Path) -> AtlasConfig:
    return AtlasConfig(
        root=repo_root,
        map_dir=repo_root / "docs" / "map",
        generated_dir=repo_root / "docs" / "map" / "generated",
        modules_dir=repo_root / "docs" / "map" / "modules",
        exclude=list(DEFAULT_EXCLUDE),
        include_extensions=set(DEFAULT_EXTENSIONS),
        thresholds=Thresholds(),
        modules={},
        use_git_churn=True,
        git_churn_max_commits=200,
        follow_dir_symlinks=True,
    )


def load_config(repo_root: Path, config_path: Path | None) -> AtlasConfig:
    cfg = default_config(repo_root)
    path = config_path
    if path is None:
        candidates = [
            repo_root / "docs" / "map" / DEFAULT_CONFIG_NAME,
            repo_root / DEFAULT_CONFIG_NAME,
            TOOL_DIR / "config.example.yaml",
        ]
        for c in candidates:
            if c.is_file():
                path = c
                break
    if path is None or not path.is_file():
        return cfg

    data = load_simple_yaml(path.read_text(encoding="utf-8"))
    cfg.config_path = path

    root_val = data.get("root") or "."
    root = (repo_root / root_val).resolve() if not Path(root_val).is_absolute() else Path(root_val)
    # When config lives in repo, treat relative root against repo_root (cwd-based).
    cfg.root = root if config_path is None else (repo_root / str(data.get("root") or ".")).resolve()

    def rel(p: str, default: Path) -> Path:
        if not p:
            return default
        pp = Path(p)
        return pp if pp.is_absolute() else (repo_root / pp).resolve()

    cfg.map_dir = rel(str(data.get("map_dir") or "docs/map"), cfg.map_dir)
    cfg.generated_dir = rel(str(data.get("generated_dir") or "docs/map/generated"), cfg.generated_dir)
    cfg.modules_dir = rel(str(data.get("modules_dir") or "docs/map/modules"), cfg.modules_dir)

    if isinstance(data.get("exclude"), list):
        cfg.exclude = [str(x) for x in data["exclude"]]
    if isinstance(data.get("include_extensions"), list):
        exts = []
        for x in data["include_extensions"]:
            s = str(x)
            if not s.startswith("."):
                s = "." + s
            exts.append(s)
        cfg.include_extensions = set(exts)

    th = data.get("thresholds") or {}
    if isinstance(th, dict):
        cfg.thresholds = Thresholds(
            outline_min_lines=int(th.get("outline_min_lines") or cfg.thresholds.outline_min_lines),
            hot_files_top=int(th.get("hot_files_top") or cfg.thresholds.hot_files_top),
            max_symbols_per_file=int(th.get("max_symbols_per_file") or cfg.thresholds.max_symbols_per_file),
            module_card_soft_max_lines=int(
                th.get("module_card_soft_max_lines") or cfg.thresholds.module_card_soft_max_lines
            ),
        )

    mods = data.get("modules") or {}
    if isinstance(mods, dict):
        parsed: dict[str, list[str]] = {}
        for mid, body in mods.items():
            if isinstance(body, dict) and isinstance(body.get("paths"), list):
                parsed[str(mid)] = [str(p) for p in body["paths"]]
            elif isinstance(body, list):
                parsed[str(mid)] = [str(p) for p in body]
        cfg.modules = parsed

    if "use_git_churn" in data:
        cfg.use_git_churn = bool(data["use_git_churn"])
    if "git_churn_max_commits" in data:
        cfg.git_churn_max_commits = int(data["git_churn_max_commits"])
    if "follow_dir_symlinks" in data:
        cfg.follow_dir_symlinks = bool(data["follow_dir_symlinks"])

    return cfg


# ---------------------------------------------------------------------------
# Glob / ignore (simplified ** support)
# ---------------------------------------------------------------------------

def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a simplified glob (with **) to regex matching relative posix paths."""
    p = pattern.replace("\\", "/").strip()
    if p.startswith("/"):
        p = p[1:]
    # Escape then replace glob tokens
    i = 0
    out = ["^"]
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


class PathMatcher:
    def __init__(self, patterns: Iterable[str]):
        self._regs = [_pattern_to_regex(p) for p in patterns if p and not p.strip().startswith("#")]

    def match(self, rel_posix: str) -> bool:
        rel = rel_posix.replace("\\", "/")
        return any(r.match(rel) for r in self._regs)


def should_exclude(rel_posix: str, excl: PathMatcher) -> bool:
    rel = rel_posix.replace("\\", "/")
    if excl.match(rel):
        return True
    # Also match if any parent segment-only patterns implicitly covered by **
    # Quick hard ignores:
    parts = rel.split("/")
    hard = {
        ".git", "node_modules", "vendor", "dist", "build", ".gradle", ".idea",
        ".venv", "venv", "__pycache__", "target", ".tox", "coverage",
    }
    return any(p in hard for p in parts)


# ---------------------------------------------------------------------------
# Symbol extraction adapters (heuristic, multi-language)
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    symbol: str
    file: str
    line: int
    kind: str
    language: str
    module: str | None = None
    detail: str | None = None


# language -> list of (kind, regex with group 'name')
# Anchored to definition-ish lines; intentionally precision-over-recall light.
RULES: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "go": [
        ("func", re.compile(r"^func\s+(?:\([^)]+\)\s+)?(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("type", re.compile(r"^type\s+(?P<name>[A-Za-z_]\w*)\s+")),
        ("const", re.compile(r"^const\s+(?P<name>[A-Za-z_]\w*)\s*")),
        ("var", re.compile(r"^var\s+(?P<name>[A-Za-z_]\w*)\s+")),
    ],
    "python": [
        ("class", re.compile(r"^class\s+(?P<name>[A-Za-z_]\w*)\s*[\(:]")),
        ("func", re.compile(r"^(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ],
    "javascript": [
        ("func", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")),
        ("class", re.compile(r"^(?:export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)\b")),
        ("const", re.compile(
            r"^(?:export\s+)?const\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function\b)"
        )),
        ("method", re.compile(r"^\s+(?:async\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\([^;]*\)\s*\{")),
    ],
    "typescript": [
        ("func", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*[<\(]")),
        ("class", re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)\b")),
        ("interface", re.compile(r"^(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)\b")),
        ("type", re.compile(r"^(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=")),
        ("const", re.compile(
            r"^(?:export\s+)?const\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|function\b|<)"
        )),
    ],
    "java": [
        ("class", re.compile(
            r"^(?:public|protected|private|abstract|final|static|\s)+class\s+(?P<name>[A-Za-z_]\w*)\b"
        )),
        ("interface", re.compile(
            r"^(?:public|protected|private|abstract|static|\s)+interface\s+(?P<name>[A-Za-z_]\w*)\b"
        )),
        ("enum", re.compile(
            r"^(?:public|protected|private|static|\s)+enum\s+(?P<name>[A-Za-z_]\w*)\b"
        )),
        ("method", re.compile(
            r"^(?:public|protected|private|static|final|synchronized|abstract|default|\s)+"
            r"(?:[\w.<>,\[\]?]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\("
        )),
    ],
    "kotlin": [
        ("class", re.compile(
            r"^(?:(?:public|private|protected|internal|open|abstract|data|sealed|inner|enum|annotation|value)\s+)*"
            r"(?:class|object|interface)\s+(?P<name>[A-Za-z_]\w*)\b"
        )),
        ("func", re.compile(
            r"^(?:(?:public|private|protected|internal|open|abstract|override|suspend|inline|operator)\s+)*"
            r"fun\s+(?:[A-Za-z0-9_.<>,?\s]+\.)?(?P<name>[A-Za-z_]\w*|`[^`]+`)\s*[<(]"
        )),
    ],
    "rust": [
        ("fn", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*[<(]")),
        ("struct", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("enum", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?enum\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("trait", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?trait\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("type", re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?type\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("impl", re.compile(r"^impl(?:\s*<[^>]+>)?\s+(?P<name>.+?)\s*\{")),
    ],
    "c": [
        ("func", re.compile(
            r"^(?:(?:static|inline|extern|const|volatile|unsigned|signed|struct|enum|union|void|int|long|short|"
            r"char|float|double|size_t|ssize_t|bool|[\w_]+)\s+)+\*?\s*(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{?"
        )),
        ("struct", re.compile(r"^(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("enum", re.compile(r"^(?:typedef\s+)?enum\s+(?P<name>[A-Za-z_]\w*)\b")),
    ],
    "csharp": [
        ("class", re.compile(
            r"^(?:public|private|protected|internal|static|abstract|sealed|partial|\s)+class\s+(?P<name>[A-Za-z_]\w*)\b"
        )),
        ("interface", re.compile(
            r"^(?:public|private|protected|internal|partial|\s)+interface\s+(?P<name>[A-Za-z_]\w*)\b"
        )),
        ("method", re.compile(
            r"^(?:public|private|protected|internal|static|async|virtual|override|sealed|\s)+"
            r"[\w.<>,\[\]?]+\s+(?P<name>[A-Za-z_]\w*)\s*\("
        )),
    ],
    "ruby": [
        ("class", re.compile(r"^class\s+(?P<name>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)")),
        ("module", re.compile(r"^module\s+(?P<name>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)")),
        ("func", re.compile(r"^\s*def\s+(?:self\.)?(?P<name>[A-Za-z_]\w*[!?=]?)")),
    ],
    "php": [
        ("class", re.compile(r"^(?:abstract\s+|final\s+)?class\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("func", re.compile(r"^(?:public|private|protected|static|\s)*function\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ],
    "shell": [
        ("func", re.compile(r"^(?:function\s+)?(?P<name>[A-Za-z_]\w*)\s*\(\)\s*\{")),
    ],
    "sql": [
        ("func", re.compile(
            r"(?i)^create\s+(?:or\s+replace\s+)?(?:function|procedure)\s+(?P<name>[\w.]+)"
        )),
        ("table", re.compile(r"(?i)^create\s+table\s+(?:if\s+not\s+exists\s+)?(?P<name>[\w.]+)")),
    ],
    "proto": [
        ("message", re.compile(r"^message\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("service", re.compile(r"^service\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("enum", re.compile(r"^enum\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("rpc", re.compile(r"^\s*rpc\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ],
    "generic": [
        ("func", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("class", re.compile(r"^(?:export\s+)?class\s+(?P<name>[A-Za-z_]\w*)\b")),
        ("func", re.compile(r"^(?:pub\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*[<(]")),
        ("func", re.compile(r"^(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("func", re.compile(r"^func\s+(?:\([^)]+\)\s+)?(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("func", re.compile(r"^fun\s+(?P<name>[A-Za-z_]\w*)\s*[<(]")),
    ],
}

EXT_LANG = {
    ".go": "go",
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "c",
    ".cpp": "c",
    ".hpp": "c",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".proto": "proto",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".swift": "generic",
    ".scala": "generic",
    ".graphql": "generic",
}


def detect_language(path: Path) -> str:
    return EXT_LANG.get(path.suffix.lower(), "generic")


def extract_symbols(path: Path, rel: str, text: str, max_symbols: int) -> list[Symbol]:
    lang = detect_language(path)
    rules = RULES.get(lang, []) + (RULES["generic"] if lang != "generic" else [])
    # de-dupe rules by pattern string while preserving order
    seen_pat: set[str] = set()
    uniq_rules: list[tuple[str, re.Pattern[str]]] = []
    for kind, rx in rules:
        key = kind + "@@" + rx.pattern
        if key in seen_pat:
            continue
        seen_pat.add(key)
        uniq_rules.append((kind, rx))

    out: list[Symbol] = []
    # Skip huge lines / binary-ish
    for i, line in enumerate(text.splitlines(), start=1):
        if len(line) > 400:
            continue
        stripped = line.rstrip()
        if not stripped:
            continue
        for kind, rx in uniq_rules:
            m = rx.match(stripped)
            if not m:
                continue
            name = m.groupdict().get("name") or m.group(0)
            name = name.strip()
            if not name or name in {"if", "for", "while", "switch", "catch", "return"}:
                continue
            out.append(Symbol(symbol=name, file=rel, line=i, kind=kind, language=lang))
            break
        if len(out) >= max_symbols:
            break
    return out


# ---------------------------------------------------------------------------
# Walk / ranking
# ---------------------------------------------------------------------------

@dataclass
class FileInfo:
    rel: str
    lines: int
    language: str
    module: str | None
    churn: int = 0
    via_symlink: bool = False


@dataclass
class WalkResult:
    files: list[tuple[Path, str, bool]]  # path, logical rel, via_symlink
    symlink_mounts: list[dict[str, str]]


HARD_SKIP_DIR_NAMES = {
    ".git", "node_modules", "vendor", "dist", "build", ".gradle", ".idea",
    ".venv", "venv", "__pycache__", "target", ".tox", "coverage", "obj", "bin",
}


def assign_module(rel: str, modules: dict[str, list[str]]) -> str | None:
    rel_posix = rel.replace("\\", "/")
    for mid, globs in modules.items():
        matcher = PathMatcher(globs)
        if matcher.match(rel_posix):
            return mid
    return None


def _real_key(path: Path) -> str | None:
    """Stable realpath key for cycle detection; None if unresolvable."""
    try:
        return str(path.resolve())
    except OSError:
        return None


def walk_source_tree(cfg: AtlasConfig) -> WalkResult:
    """Walk repo with optional directory-symlink following.

    Paths are reported as *logical* paths under cfg.root, e.g. ``claudia/src/...``
    even when ``claudia`` is a symlink to an external tree (ai-proxy/claudia pattern).
    Cycle-safe via realpath of entered directories.
    """
    excl = PathMatcher(cfg.exclude)
    root = cfg.root
    out_files: list[tuple[Path, str, bool]] = []
    symlink_mounts: list[dict[str, str]] = []
    seen_mounts: set[str] = set()
    visited_real_dirs: set[str] = set()

    def join_rel(rel_dir: str, name: str) -> str:
        return f"{rel_dir}/{name}" if rel_dir else name

    def walk_dir(logical_dir: Path, rel_dir: str, via_symlink: bool) -> None:
        real = _real_key(logical_dir)
        if real is None:
            return
        if real in visited_real_dirs:
            return
        visited_real_dirs.add(real)

        try:
            entries = sorted(logical_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for entry in entries:
            name = entry.name
            rel = join_rel(rel_dir, name).replace("\\", "/")

            try:
                is_link = entry.is_symlink()
            except OSError:
                continue

            # Directory? (follows symlink targets)
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False

            if is_dir:
                if name in HARD_SKIP_DIR_NAMES:
                    continue
                if should_exclude(rel, excl) or should_exclude(rel + "/", excl):
                    continue
                child_via = via_symlink
                if is_link:
                    if not cfg.follow_dir_symlinks:
                        continue
                    child_via = True
                    target = _real_key(entry) or ""
                    if rel not in seen_mounts:
                        seen_mounts.add(rel)
                        symlink_mounts.append(
                            {
                                "logical": rel,
                                "target": target,
                            }
                        )
                walk_dir(entry, rel, child_via)
                continue

            # File or symlink-to-file
            try:
                is_file = entry.is_file()
            except OSError:
                is_file = False
            if not is_file:
                continue
            if should_exclude(rel, excl):
                continue
            if entry.suffix.lower() not in cfg.include_extensions:
                continue
            try:
                if entry.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            out_files.append((entry, rel, via_symlink or is_link))

    walk_dir(root, "", via_symlink=False)
    return WalkResult(files=out_files, symlink_mounts=symlink_mounts)


def iter_source_files(cfg: AtlasConfig) -> Iterator[Path]:
    """Compatibility wrapper: yield paths only."""
    for path, _rel, _via in walk_source_tree(cfg).files:
        yield path


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as f:
            n = 0
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                n += chunk.count(b"\n")
            return n
    except OSError:
        return 0


def git_churn(repo_root: Path, max_commits: int) -> dict[str, int]:
    """Return path -> number of times touched in recent history."""
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(repo_root),
                "log",
                f"--max-count={max_commits}",
                "--name-only",
                "--pretty=format:",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for line in proc.stdout.splitlines():
        p = line.strip().replace("\\", "/")
        if p:
            counts[p] += 1
    return dict(counts)


def collect_files(cfg: AtlasConfig) -> tuple[list[FileInfo], list[dict[str, str]]]:
    churn_map = git_churn(cfg.root, cfg.git_churn_max_commits) if cfg.use_git_churn else {}
    walked = walk_source_tree(cfg)
    files: list[FileInfo] = []
    for path, rel, via_symlink in walked.files:
        files.append(
            FileInfo(
                rel=rel,
                lines=count_lines(path),
                language=detect_language(path),
                module=assign_module(rel, cfg.modules),
                churn=int(churn_map.get(rel, 0)),
                via_symlink=via_symlink,
            )
        )
    return files, walked.symlink_mounts


def rank_hot_files(files: list[FileInfo], top_n: int) -> list[FileInfo]:
    # score: lines + 40*churn (churn heavily weighted when present)
    def score(f: FileInfo) -> tuple[int, int, int]:
        return (f.lines + 40 * f.churn, f.churn, f.lines)

    return sorted(files, key=score, reverse=True)[:top_n]


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def gen_tree(files: list[FileInfo]) -> str:
    lines = ["# path\tlines\tlanguage\tmodule\tvia_symlink"]
    for f in sorted(files, key=lambda x: x.rel):
        via = "1" if f.via_symlink else "0"
        lines.append(f"{f.rel}\t{f.lines}\t{f.language}\t{f.module or '-'}\t{via}")
    lines.append("")
    return "\n".join(lines)


def gen_hot_files_json(hot: list[FileInfo]) -> str:
    payload = [
        {
            "file": f.rel,
            "lines": f.lines,
            "language": f.language,
            "module": f.module,
            "churn": f.churn,
            "score": f.lines + 40 * f.churn,
            "via_symlink": f.via_symlink,
        }
        for f in hot
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def gen_outlines_and_symbols(
    cfg: AtlasConfig, files: list[FileInfo]
) -> tuple[str, list[dict[str, Any]], list[str]]:
    outline_lines: list[str] = [
        "# Generated outlines (do not edit)",
        "",
        f"Threshold: files with lines >= {cfg.thresholds.outline_min_lines}",
        "",
    ]
    symbols: list[dict[str, Any]] = []
    warnings: list[str] = []

    # Always extract symbols for all source files, but only outline large ones fully listed.
    for f in sorted(files, key=lambda x: x.rel):
        path = cfg.root / f.rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            warnings.append(f"cannot read {f.rel}: {e}")
            continue
        syms = extract_symbols(path, f.rel, text, cfg.thresholds.max_symbols_per_file)
        for s in syms:
            s.module = f.module
            symbols.append(asdict(s))

        if f.lines >= cfg.thresholds.outline_min_lines:
            via = ", via_symlink" if f.via_symlink else ""
            outline_lines.append(
                f"## {f.rel} ({f.lines} lines, {f.language}, module={f.module or '-'}{via})"
            )
            outline_lines.append("")
            if not syms:
                outline_lines.append("_no definitions matched heuristics_")
            else:
                for s in syms:
                    outline_lines.append(f"- L{s.line}: `{s.kind}` **{s.symbol}**")
            outline_lines.append("")

    # language coverage warning
    lang_files: dict[str, int] = defaultdict(int)
    lang_syms: dict[str, int] = defaultdict(int)
    for f in files:
        lang_files[f.language] += 1
    for s in symbols:
        lang_syms[s.get("language") or "generic"] += 1
    for lang, nfiles in lang_files.items():
        if nfiles >= 10 and lang_syms.get(lang, 0) == 0:
            warnings.append(f"language {lang}: {nfiles} files but 0 symbols extracted")

    return "\n".join(outline_lines).rstrip() + "\n", symbols, warnings


def cmd_gen(cfg: AtlasConfig) -> int:
    print(f"root: {cfg.root}")
    if cfg.config_path:
        print(f"config: {cfg.config_path}")
    print(f"follow_dir_symlinks: {cfg.follow_dir_symlinks}")
    files, symlink_mounts = collect_files(cfg)
    print(f"indexed files: {len(files)}")
    if symlink_mounts:
        print(f"symlink mounts: {len(symlink_mounts)}")
        for m in symlink_mounts[:10]:
            print(f"  {m['logical']} -> {m['target']}")
    hot = rank_hot_files(files, cfg.thresholds.hot_files_top)
    outlines, symbols, warnings = gen_outlines_and_symbols(cfg, files)

    ensure_dir(cfg.generated_dir)
    write_text(cfg.generated_dir / "tree.txt", gen_tree(files))
    write_text(cfg.generated_dir / "hot-files.json", gen_hot_files_json(hot))
    write_text(cfg.generated_dir / "outlines.md", outlines)
    write_text(
        cfg.generated_dir / "symbols.json",
        json.dumps(symbols, indent=2, ensure_ascii=False) + "\n",
    )
    # also TSV for jq-less grepping
    tsv = ["symbol\tfile\tline\tkind\tlanguage\tmodule"]
    for s in symbols:
        tsv.append(
            f"{s['symbol']}\t{s['file']}\t{s['line']}\t{s['kind']}\t{s['language']}\t{s.get('module') or '-'}"
        )
    write_text(cfg.generated_dir / "symbols.tsv", "\n".join(tsv) + "\n")

    # unmapped hot files
    unmapped_hot = [h.rel for h in hot if not h.module and cfg.modules]
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": "tools/code-atlas/atlas.py",
        "root": str(cfg.root),
        "config": str(cfg.config_path) if cfg.config_path else None,
        "file_count": len(files),
        "symbol_count": len(symbols),
        "hot_files_top": cfg.thresholds.hot_files_top,
        "outline_min_lines": cfg.thresholds.outline_min_lines,
        "follow_dir_symlinks": cfg.follow_dir_symlinks,
        "symlink_mounts": symlink_mounts,
        "modules_configured": sorted(cfg.modules.keys()),
        "unmapped_hot_files": unmapped_hot,
        "warnings": warnings,
        "git_head": _git_head(cfg.root),
    }
    write_text(cfg.generated_dir / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    print(f"wrote: {cfg.generated_dir}")
    print(f"symbols: {len(symbols)}")
    if unmapped_hot:
        print(f"warning: {len(unmapped_hot)} hot files not mapped to any module")
        for u in unmapped_hot[:10]:
            print(f"  - {u}")
    for w in warnings:
        print(f"warning: {w}")
    return 0


def _git_head(repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def _read_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"missing template: {path}")
    return path.read_text(encoding="utf-8")


def cmd_init(cfg: AtlasConfig, force: bool = False, modules: list[str] | None = None) -> int:
    ensure_dir(cfg.map_dir)
    ensure_dir(cfg.modules_dir)
    ensure_dir(cfg.generated_dir)

    def write_if_absent(path: Path, content: str) -> None:
        if path.exists() and not force:
            print(f"skip (exists): {path}")
            return
        write_text(path, content)
        print(f"wrote: {path}")

    write_if_absent(cfg.map_dir / "ATLAS.md", _read_template("ATLAS.md"))
    write_if_absent(cfg.map_dir / "README.md", _read_template("map-README.md"))

    # config
    cfg_path = cfg.map_dir / DEFAULT_CONFIG_NAME
    if not cfg_path.exists() or force:
        example = (TOOL_DIR / "config.example.yaml").read_text(encoding="utf-8")
        write_text(cfg_path, example)
        print(f"wrote: {cfg_path}")
    else:
        print(f"skip (exists): {cfg_path}")

    # L0 snippet helper
    write_if_absent(cfg.map_dir / "L0-snippet.md", _read_template("L0-snippet.md"))

    mods = modules or ["example"]
    card_tmpl = _read_template("module.md")
    for mid in mods:
        card = card_tmpl.replace("{{MODULE_ID}}", mid)
        write_if_absent(cfg.modules_dir / f"{mid}.md", card)

    gitkeep = cfg.generated_dir / ".gitkeep"
    if not gitkeep.exists():
        write_text(gitkeep, "")
        print(f"wrote: {gitkeep}")

    print("")
    print("Next:")
    print(f"  1. Edit {cfg.map_dir / 'ATLAS.md'} (modules, intents, landmines)")
    print(f"  2. Edit {cfg_path} module path globs")
    print(f"  3. python3 tools/code-atlas/atlas.py gen")
    print(f"  4. python3 tools/code-atlas/atlas.py validate")
    print(f"  5. Paste {cfg.map_dir / 'L0-snippet.md'} into project-context / AGENTS.md")
    return 0


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

REQUIRED_ATLAS_HEADINGS = [
    "Topology",
    "Modules",
    "Intent",
    "Hot files",
    "Landmines",
    "Navigation protocol",
]


@dataclass
class Finding:
    level: str  # error | warn
    message: str


def _md_headings(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"(?m)^##\s+(.+)$", text)]


def _extract_table_paths_from_atlas(text: str) -> list[str]:
    """Best-effort: collect backtick paths and bare path-looking cells from tables."""
    paths: list[str] = []
    for m in re.finditer(r"`([^`]+)`", text):
        p = m.group(1).strip()
        if "/" in p or re.search(r"\.\w{1,8}$", p):
            # strip trailing symbol notes: file.ext (symbol)
            p = re.split(r"\s+\(", p, maxsplit=1)[0].strip()
            paths.append(p)
    return paths


def _module_ids_from_atlas_modules_table(text: str) -> list[str]:
    """Parse first column of the Modules table."""
    lines = text.splitlines()
    in_modules = False
    ids: list[str] = []
    header_seen = False
    for line in lines:
        if re.match(r"^##\s+Modules\b", line):
            in_modules = True
            header_seen = False
            continue
        if in_modules and line.startswith("## "):
            break
        if not in_modules:
            continue
        if not line.strip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cols:
            continue
        if re.match(r"^:?-+:?$", cols[0].replace(" ", "")):
            header_seen = True
            continue
        if cols[0].lower() in {"id", "module", "module id"}:
            header_seen = True
            continue
        if not header_seen and cols[0].lower() == "id":
            continue
        mid = cols[0].strip().strip("`")
        if mid and mid not in ids and mid.lower() != "id":
            ids.append(mid)
    return ids


def cmd_validate(cfg: AtlasConfig) -> int:
    findings: list[Finding] = []
    atlas_path = cfg.map_dir / "ATLAS.md"
    if not atlas_path.is_file():
        findings.append(Finding("error", f"missing {atlas_path}"))
        return _print_findings(findings)

    atlas = atlas_path.read_text(encoding="utf-8")
    headings = " ".join(_md_headings(atlas))
    for req in REQUIRED_ATLAS_HEADINGS:
        if req.lower() not in headings.lower():
            findings.append(Finding("error", f"ATLAS.md missing required section heading containing '{req}'"))

    module_ids = _module_ids_from_atlas_modules_table(atlas)
    if not module_ids:
        findings.append(Finding("warn", "could not parse any module IDs from ATLAS Modules table"))

    card_files = {}
    if cfg.modules_dir.is_dir():
        for p in sorted(cfg.modules_dir.glob("*.md")):
            card_files[p.stem] = p
    else:
        findings.append(Finding("error", f"missing modules dir {cfg.modules_dir}"))

    for mid in module_ids:
        if mid not in card_files:
            findings.append(Finding("error", f"module '{mid}' listed in ATLAS but no card modules/{mid}.md"))
        else:
            # soft size
            nlines = len(card_files[mid].read_text(encoding="utf-8").splitlines())
            if nlines > cfg.thresholds.module_card_soft_max_lines:
                findings.append(
                    Finding(
                        "warn",
                        f"module card {mid}.md has {nlines} lines "
                        f"(soft max {cfg.thresholds.module_card_soft_max_lines})",
                    )
                )

    for stem, path in card_files.items():
        if module_ids and stem not in module_ids and stem != "README":
            findings.append(Finding("warn", f"module card {path.name} not referenced in ATLAS Modules table"))

    # path existence for backtick paths that look like files (not globs)
    for p in _extract_table_paths_from_atlas(atlas):
        if any(ch in p for ch in ["*", "?", "<", ">", " "]):
            continue
        if p.endswith("/"):
            continue
        # only check if it has an extension or exists as dir/file
        candidate = cfg.root / p
        if not candidate.exists():
            # allow path prefixes used as directories without trailing slash
            if not (cfg.root / p).exists():
                # only error if looks like a concrete file
                if re.search(r"\.[A-Za-z0-9]{1,8}$", p):
                    findings.append(Finding("error", f"ATLAS references missing path: {p}"))
                else:
                    findings.append(Finding("warn", f"ATLAS references path not found: {p}"))

    # generated checks
    meta_path = cfg.generated_dir / "meta.json"
    if not meta_path.is_file():
        findings.append(Finding("warn", f"generated meta missing ({meta_path}); run: atlas.py gen"))
    else:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for u in meta.get("unmapped_hot_files") or []:
                findings.append(Finding("warn", f"hot file not mapped to module: {u}"))
            for w in meta.get("warnings") or []:
                findings.append(Finding("warn", f"generator: {w}"))
        except json.JSONDecodeError as e:
            findings.append(Finding("error", f"invalid meta.json: {e}"))

    for required_gen in ("tree.txt", "hot-files.json", "symbols.json", "outlines.md"):
        if not (cfg.generated_dir / required_gen).is_file():
            findings.append(Finding("warn", f"missing generated/{required_gen}"))

    # config modules vs cards
    for mid in cfg.modules.keys():
        if mid not in card_files:
            findings.append(Finding("warn", f"config module '{mid}' has no modules/{mid}.md card"))

    return _print_findings(findings)


def _print_findings(findings: list[Finding]) -> int:
    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]
    for f in findings:
        print(f"{f.level.upper()}: {f.message}")
    print(f"summary: {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# Outline single file
# ---------------------------------------------------------------------------

def cmd_outline(cfg: AtlasConfig, file_rel: str) -> int:
    path = cfg.root / file_rel
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    syms = extract_symbols(path, file_rel, text, cfg.thresholds.max_symbols_per_file)
    for s in syms:
        print(f"L{s.line}\t{s.kind}\t{s.symbol}\t{s.language}")
    print(f"# {len(syms)} symbols")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_repo_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists() or (p / "docs" / "map").exists() or (p / "tools" / "code-atlas").exists():
            return p
    return cur


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atlas",
        description="Code Atlas — navigation index toolkit (init / gen / validate)",
    )
    p.add_argument("--root", type=str, default=None, help="repository root (default: auto)")
    p.add_argument("--config", type=str, default=None, help="path to atlas.config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="bootstrap docs/map skeleton")
    sp.add_argument("--force", action="store_true", help="overwrite existing templates")
    sp.add_argument(
        "--module",
        action="append",
        default=[],
        help="module id to create (repeatable); default: example",
    )

    sub.add_parser("gen", help="generate L3 index under docs/map/generated")
    sub.add_parser("validate", help="validate ATLAS / module cards / generated")

    so = sub.add_parser("outline", help="print definition outline for one file")
    so.add_argument("file", help="file path relative to repo root")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.root).resolve() if args.root else find_repo_root()
    config_path = Path(args.config).resolve() if args.config else None
    cfg = load_config(repo_root, config_path)
    # Prefer explicit --root for walk root when provided
    if args.root:
        cfg.root = Path(args.root).resolve()

    if args.cmd == "init":
        return cmd_init(cfg, force=args.force, modules=args.module or None)
    if args.cmd == "gen":
        return cmd_gen(cfg)
    if args.cmd == "validate":
        return cmd_validate(cfg)
    if args.cmd == "outline":
        return cmd_outline(cfg, args.file)
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
