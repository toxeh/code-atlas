#!/usr/bin/env python3
"""Smoke tests for Code Atlas toolkit (stdlib unittest)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import atlas  # noqa: E402


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_repo"


class AtlasSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="atlas-test-"))
        self.repo = self.tmp / "repo"
        shutil.copytree(FIXTURE, self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_yaml_roundtrip_subset(self) -> None:
        text = """
root: .
exclude:
  - "**/node_modules/**"
thresholds:
  outline_min_lines: 50
  hot_files_top: 5
modules:
  auth:
    paths:
      - "**/auth/**"
  api:
    paths:
      - "**/api/**"
use_git_churn: false
"""
        data = atlas.load_simple_yaml(text)
        self.assertEqual(data["thresholds"]["outline_min_lines"], 50)
        self.assertEqual(data["modules"]["auth"]["paths"], ["**/auth/**"])
        self.assertFalse(data["use_git_churn"])

    def test_extract_go_python_ts_symbols(self) -> None:
        go = (FIXTURE / "src/api/handler.go").read_text(encoding="utf-8")
        syms = atlas.extract_symbols(Path("handler.go"), "src/api/handler.go", go, 50)
        names = {s.symbol for s in syms}
        self.assertIn("HandleLogin", names)
        self.assertIn("Session", names)

        py = (FIXTURE / "src/billing/invoice.py").read_text(encoding="utf-8")
        psyms = atlas.extract_symbols(Path("invoice.py"), "src/billing/invoice.py", py, 50)
        pnames = {s.symbol for s in psyms}
        self.assertIn("Invoice", pnames)
        self.assertIn("create_invoice", pnames)

        ts = (FIXTURE / "src/ui/widget.ts").read_text(encoding="utf-8")
        tsyms = atlas.extract_symbols(Path("widget.ts"), "src/ui/widget.ts", ts, 50)
        tnames = {s.symbol for s in tsyms}
        self.assertIn("renderDashboard", tnames)
        self.assertIn("Widget", tnames)

    def test_init_gen_validate(self) -> None:
        # init
        rc = atlas.main(["--root", str(self.repo), "init", "--module", "auth", "--module", "api", "--module", "billing", "--module", "ui"])
        self.assertEqual(rc, 0)
        atlas_md = self.repo / "docs/map/ATLAS.md"
        self.assertTrue(atlas_md.is_file())

        # rewrite ATLAS with real modules + existing paths
        atlas_md.write_text(
            """# Code Atlas

## Topology

```text
ui -> api -> auth
api -> billing
```

## Modules

| ID | Paths | Responsibility | Do not |
|---|---|---|---|
| auth | `pkg/auth` | tokens | ui |
| api | `src/api` | http handlers | billing rules |
| billing | `src/billing` | invoices | auth |
| ui | `src/ui` | widgets | server secrets |

## Intent → landing

| If you need to… | Start here | Then | Module |
|---|---|---|---|
| change login | `src/api/handler.go` | `pkg/auth/token.go` | api |
| change invoices | `src/billing/invoice.py` | tests | billing |

## Hot files

| File | Why hot | Module |
|---|---|---|
| `src/api/handler.go` | entrypoint | api |

## Landmines

- do not commit secrets

## Navigation protocol

1. Open ATLAS
2. Open module card
3. Use generated symbols

## Verify cheatsheet

- tests: none in fixture
""",
            encoding="utf-8",
        )

        # config with modules + low outline threshold
        (self.repo / "docs/map/atlas.config.yaml").write_text(
            """
root: .
map_dir: docs/map
generated_dir: docs/map/generated
modules_dir: docs/map/modules
exclude:
  - "**/.git/**"
  - "**/docs/map/generated/**"
thresholds:
  outline_min_lines: 10
  hot_files_top: 10
  max_symbols_per_file: 100
  module_card_soft_max_lines: 120
modules:
  auth:
    paths:
      - "**/auth/**"
  api:
    paths:
      - "**/api/**"
  billing:
    paths:
      - "**/billing/**"
  ui:
    paths:
      - "**/ui/**"
use_git_churn: false
""",
            encoding="utf-8",
        )

        rc = atlas.main(["--root", str(self.repo), "--config", str(self.repo / "docs/map/atlas.config.yaml"), "gen"])
        self.assertEqual(rc, 0)

        gen = self.repo / "docs/map/generated"
        self.assertTrue((gen / "symbols.json").is_file())
        self.assertTrue((gen / "hot-files.json").is_file())
        self.assertTrue((gen / "outlines.md").is_file())
        self.assertTrue((gen / "meta.json").is_file())

        symbols = json.loads((gen / "symbols.json").read_text(encoding="utf-8"))
        names = {s["symbol"] for s in symbols}
        self.assertIn("HandleLogin", names)
        self.assertIn("IssueToken", names)
        self.assertIn("create_invoice", names)
        self.assertIn("renderDashboard", names)

        # module assignment
        by_file = {s["file"]: s.get("module") for s in symbols}
        self.assertEqual(by_file.get("pkg/auth/token.go"), "auth")
        self.assertEqual(by_file.get("src/api/handler.go"), "api")

        meta = json.loads((gen / "meta.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(meta["file_count"], 4)
        self.assertGreaterEqual(meta["symbol_count"], 5)

        rc = atlas.main(["--root", str(self.repo), "--config", str(self.repo / "docs/map/atlas.config.yaml"), "validate"])
        self.assertEqual(rc, 0, "validate should pass on well-formed fixture atlas")

    def test_validate_detects_missing_card(self) -> None:
        atlas.main(["--root", str(self.repo), "init", "--module", "onlyone"])
        (self.repo / "docs/map/ATLAS.md").write_text(
            """# Code Atlas
## Topology
x
## Modules
| ID | Paths | Responsibility | Do not |
|---|---|---|---|
| missingmod | `src` | x | y |
## Intent → landing
| If you need to… | Start here | Then | Module |
|---|---|---|---|
| x | `src/api/handler.go` | y | missingmod |
## Hot files
| File | Why hot | Module |
|---|---|---|
| `src/api/handler.go` | e | missingmod |
## Landmines
- none
## Navigation protocol
1. go
## Verify cheatsheet
- none
""",
            encoding="utf-8",
        )
        rc = atlas.main(["--root", str(self.repo), "validate"])
        self.assertEqual(rc, 1)

    def test_follow_dir_symlink(self) -> None:
        """Directory symlink mount: repo/plugin -> external tree."""
        external = self.tmp / "external_project"
        (external / "src").mkdir(parents=True)
        (external / "src" / "main.go").write_text(
            "package main\n\nfunc MainEntry() {}\n",
            encoding="utf-8",
        )
        # local non-symlink file
        (self.repo / "src" / "local.go").parent.mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "local.go").write_text(
            "package local\n\nfunc LocalOnly() {}\n",
            encoding="utf-8",
        )
        plugin = self.repo / "plugin"
        plugin.symlink_to(external, target_is_directory=True)

        cfg = atlas.default_config(self.repo)
        cfg.use_git_churn = False
        cfg.follow_dir_symlinks = True
        files, mounts = atlas.collect_files(cfg)
        rels = {f.rel for f in files}

        self.assertIn("plugin/src/main.go", rels)
        self.assertIn("src/local.go", rels)
        # logical path, not external realpath
        self.assertTrue(all(not r.startswith(str(external)) for r in rels))
        self.assertTrue(any(m["logical"] == "plugin" for m in mounts))
        plugin_files = [f for f in files if f.rel.startswith("plugin/")]
        self.assertTrue(plugin_files)
        self.assertTrue(all(f.via_symlink for f in plugin_files))

        # follow off → mount ignored
        cfg.follow_dir_symlinks = False
        files_off, mounts_off = atlas.collect_files(cfg)
        rels_off = {f.rel for f in files_off}
        self.assertNotIn("plugin/src/main.go", rels_off)
        self.assertIn("src/local.go", rels_off)
        self.assertEqual(mounts_off, [])

        # cycle: plugin/loop -> plugin should not infinite-loop
        cfg.follow_dir_symlinks = True
        (external / "loop").symlink_to(plugin, target_is_directory=True)
        files_cycle, _ = atlas.collect_files(cfg)
        self.assertIn("plugin/src/main.go", {f.rel for f in files_cycle})


def _run_atlas(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = atlas.main(argv)
        except SystemExit as exc:
            code = exc.code
            rc = code if isinstance(code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def _write_config(repo: Path, body: str) -> Path:
    path = repo / "docs" / "map" / "atlas.config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "atlas"
    env["GIT_AUTHOR_EMAIL"] = "atlas@example.com"
    env["GIT_COMMITTER_NAME"] = "atlas"
    env["GIT_COMMITTER_EMAIL"] = "atlas@example.com"
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True, text=True)


def _outline_section(text: str, rel: str) -> str:
    marker = f"## {rel} "
    start = text.index(marker)
    rest = text[start:]
    nxt = rest.find("\n## ")
    return rest if nxt < 0 else rest[:nxt]


class AtlasJavaSymbolTest(unittest.TestCase):
    def test_throw_new_is_not_a_method(self) -> None:
        src = """\
public class Hello {
    public void realMethod() {}
    throw new IllegalArgumentException("x");
    return compute(
    new Builder(
    catch handler(
    if predicate(
    for iterator(
    while condition(
    switch selector(
    else fallback(
    case label(
}
"""
        syms = atlas.extract_symbols(Path("Hello.java"), "src/main/java/Hello.java", src, 50)
        names = {s.symbol for s in syms}
        self.assertIn("realMethod", names)
        self.assertNotIn("IllegalArgumentException", names)
        for false_name in (
            "compute",
            "Builder",
            "handler",
            "predicate",
            "iterator",
            "condition",
            "selector",
            "fallback",
            "label",
        ):
            self.assertNotIn(false_name, names)
        real = next(s for s in syms if s.symbol == "realMethod")
        self.assertEqual(real.kind, "method")

    def test_method_owner_signature_constructor_and_record(self) -> None:
        src = """\
public class Hello {
    public String greet(String name, int n) {}
    public Hello(String name) {}
}
"""
        syms = atlas.extract_symbols(Path("Hello.java"), "src/main/java/Hello.java", src, 50)
        greets = [s for s in syms if s.symbol == "greet"]
        self.assertEqual(len(greets), 1)
        greet = greets[0]
        self.assertEqual(greet.kind, "method")
        self.assertEqual(getattr(greet, "owner", None), "Hello")
        self.assertEqual(getattr(greet, "signature", None), "greet(String, int)")

        ctors = [s for s in syms if s.symbol == "Hello" and s.kind == "constructor"]
        self.assertEqual(len(ctors), 1)
        self.assertEqual(getattr(ctors[0], "owner", None), "Hello")
        self.assertEqual(getattr(ctors[0], "signature", None), "Hello(String)")

        record_src = "public record Hello(String name) {}\n"
        rsyms = atlas.extract_symbols(Path("Hello.java"), "src/main/java/Hello.java", record_src, 50)
        self.assertFalse(any(s.symbol == "Hello" and s.kind == "method" for s in rsyms))
        records = [s for s in rsyms if s.symbol == "Hello" and s.kind == "class"]
        self.assertEqual(len(records), 1)
        self.assertEqual(getattr(records[0], "owner", None), "")


class AtlasGenShardsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="atlas-shard-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_test_sources_go_to_test_shard(self) -> None:
        main = self.repo / "src" / "main" / "java" / "pkg" / "Hello.java"
        test = self.repo / "src" / "test" / "java" / "pkg" / "HelloTest.java"
        it = self.repo / "src" / "it" / "java" / "pkg" / "HelloIT.java"
        lib = self.repo / "lib" / "src" / "main" / "java" / "pkg" / "Lib.java"
        for path, body in (
            (main, "public class Hello {\n    public void greet() {}\n}\n"),
            (test, "public class HelloTest {\n    public void testGreet() {}\n}\n"),
            (it, "public class HelloIT {\n    public void checkGreet() {}\n}\n"),
            (lib, "public class Lib {\n    public void ping() {}\n}\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        cfg = _write_config(
            self.repo,
            """
root: .
thresholds:
  outline_min_lines: 1
  hot_files_top: 10
  max_symbols_per_file: 50
modules:
  app:
    paths:
      - "src/**"
  lib:
    paths:
      - "lib/**"
use_git_churn: false
""",
        )
        rc, _, err = _run_atlas(["--root", str(self.repo), "--config", str(cfg), "gen"])
        self.assertEqual(rc, 0, err)
        gen = self.repo / "docs" / "map" / "generated"
        shards = gen / "modules"
        self.assertTrue((shards / "app-tests-symbols.tsv").is_file())
        self.assertTrue((shards / "app-tests-outlines.md").is_file())
        self.assertTrue((shards / "lib-tests-symbols.tsv").is_file())
        main_tsv = (shards / "app-symbols.tsv").read_text(encoding="utf-8")
        test_tsv = (shards / "app-tests-symbols.tsv").read_text(encoding="utf-8")
        lib_tests = (shards / "lib-tests-symbols.tsv").read_text(encoding="utf-8")
        self.assertIn("greet", main_tsv)
        self.assertNotIn("src/test/", main_tsv)
        self.assertNotIn("src/it/", main_tsv)
        self.assertIn("testGreet", test_tsv)
        self.assertIn("src/test/", test_tsv)
        self.assertIn("checkGreet", test_tsv)
        self.assertIn("src/it/", test_tsv)
        self.assertNotIn("src/main/", test_tsv)
        self.assertEqual(len([ln for ln in lib_tests.splitlines() if ln.strip()]), 1)
        self.assertTrue(lib_tests.splitlines()[0].endswith("\trole"))

        combined = (gen / "symbols.tsv").read_text(encoding="utf-8")
        self.assertIn("src/test/", combined)
        self.assertIn("src/main/", combined)
        header = combined.splitlines()[0].split("\t")
        self.assertEqual(
            header,
            ["symbol", "file", "line", "kind", "language", "module", "owner", "signature", "role"],
        )
        symbols = json.loads((gen / "symbols.json").read_text(encoding="utf-8"))
        by_name = {s["symbol"]: s for s in symbols}
        self.assertEqual(by_name["greet"]["role"], "main")
        self.assertEqual(by_name["testGreet"]["role"], "test")
        self.assertEqual(by_name["checkGreet"]["role"], "test")
        self.assertEqual(by_name["greet"]["owner"], "Hello")
        self.assertEqual(by_name["greet"]["signature"], "greet()")

        main_outline = (shards / "app-outlines.md").read_text(encoding="utf-8")
        test_outline = (shards / "app-tests-outlines.md").read_text(encoding="utf-8")
        self.assertIn("src/main/", main_outline)
        self.assertNotIn("src/test/", main_outline)
        self.assertNotIn("src/it/", main_outline)
        self.assertIn("src/test/", test_outline)
        self.assertIn("src/it/", test_outline)
        self.assertNotIn("src/main/", test_outline)

    def test_outline_marks_truncation(self) -> None:
        big = self.repo / "src" / "Big.java"
        tiny = self.repo / "src" / "Tiny.java"
        big.parent.mkdir(parents=True)
        methods = "\n".join(f"    public void m{i}() {{}}" for i in range(6))
        big.write_text(f"public class Big {{\n{methods}\n}}\n", encoding="utf-8")
        tiny.write_text("public class Tiny {\n    public void only() {}\n}\n", encoding="utf-8")
        cfg = _write_config(
            self.repo,
            """
root: .
thresholds:
  outline_min_lines: 1
  hot_files_top: 10
  max_symbols_per_file: 2
modules:
  app:
    paths:
      - "src/**"
use_git_churn: false
""",
        )
        rc, _, err = _run_atlas(["--root", str(self.repo), "--config", str(cfg), "gen"])
        self.assertEqual(rc, 0, err)
        gen = self.repo / "docs" / "map" / "generated"
        for outline_path in (gen / "outlines.md", gen / "modules" / "app-outlines.md"):
            text = outline_path.read_text(encoding="utf-8")
            big_sec = _outline_section(text, "src/Big.java")
            tiny_sec = _outline_section(text, "src/Tiny.java")
            big_lines = [ln for ln in big_sec.splitlines() if ln.strip()]
            self.assertEqual(big_lines[-1], "_truncated at max_symbols_per_file_")
            self.assertNotIn("_truncated at max_symbols_per_file_", tiny_sec)


class AtlasChurnTest(unittest.TestCase):
    def test_mount_commit_outranks_longer_untouched_file(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="atlas-churn-"))
        try:
            root = tmp / "atlas"
            external = tmp / "external"
            plain = tmp / "plain"
            root.mkdir()
            (external / "connector").mkdir(parents=True)
            (plain / "src").mkdir(parents=True)
            _git(root, "init")
            _git(external, "init")
            touched = root / "src" / "RootTouched.java"
            touched.parent.mkdir(parents=True)
            touched.write_text("public class RootTouched {\n    public void a() {}\n}\n", encoding="utf-8")
            _git(root, "add", "src/RootTouched.java")
            _git(root, "commit", "-m", "root file")
            long_file = root / "src" / "LongUntouched.java"
            long_file.write_text(
                "public class LongUntouched {\n    public void once() {}\n}\n" + ("// pad\n" * 40),
                encoding="utf-8",
            )
            foo = external / "connector" / "Foo.java"
            foo.write_text("public class Foo {\n    public void ping() {}\n}\n", encoding="utf-8")
            _git(external, "add", "connector/Foo.java")
            _git(external, "commit", "-m", "mount file")
            (plain / "src" / "Bar.java").write_text(
                "public class Bar {\n    public void b() {}\n}\n",
                encoding="utf-8",
            )
            (root / "mounted").symlink_to(external, target_is_directory=True)
            (root / "loose").symlink_to(plain, target_is_directory=True)
            cfg = _write_config(
                root,
                """
root: .
thresholds:
  outline_min_lines: 500
  hot_files_top: 50
  max_symbols_per_file: 50
modules:
  mounted:
    paths:
      - "mounted/**"
  loose:
    paths:
      - "loose/**"
  rootmod:
    paths:
      - "src/**"
use_git_churn: true
git_churn_max_commits: 20
follow_dir_symlinks: true
""",
            )
            rc, _, err = _run_atlas(["--root", str(root), "--config", str(cfg), "gen"])
            self.assertEqual(rc, 0, err)
            hot = json.loads(
                (root / "docs" / "map" / "generated" / "hot-files.json").read_text(encoding="utf-8")
            )
            by_file = {row["file"]: row for row in hot}
            self.assertIn("mounted/connector/Foo.java", by_file)
            self.assertGreaterEqual(by_file["mounted/connector/Foo.java"]["churn"], 1)
            self.assertEqual(by_file["src/LongUntouched.java"]["churn"], 0)
            self.assertEqual(by_file["loose/src/Bar.java"]["churn"], 0)
            self.assertGreaterEqual(by_file["src/RootTouched.java"]["churn"], 1)
            order = [row["file"] for row in hot]
            self.assertLess(order.index("mounted/connector/Foo.java"), order.index("src/LongUntouched.java"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AtlasFindTest(unittest.TestCase):
    def test_find_qualified_line_module_filter_and_miss(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="atlas-find-"))
        try:
            repo = tmp / "repo"
            repo.mkdir()
            hello = repo / "src" / "main" / "java" / "pkg" / "Hello.java"
            hello.parent.mkdir(parents=True)
            hits = "\n".join(f"    public void hit{i}() {{}}" for i in range(25))
            hello.write_text(
                "public class Hello {\n"
                "    public String greet(String name, int n) {}\n"
                f"{hits}\n"
                "}\n",
                encoding="utf-8",
            )
            other = repo / "lib" / "Other.java"
            other.parent.mkdir(parents=True)
            other.write_text("public class Other {\n    public void greetOther() {}\n}\n", encoding="utf-8")
            go = repo / "src" / "api" / "handler.go"
            go.parent.mkdir(parents=True)
            go.write_text("package api\n\nfunc HandleLogin(user string) error {\n\treturn nil\n}\n", encoding="utf-8")
            cfg = _write_config(
                repo,
                """
root: .
thresholds:
  outline_min_lines: 500
  hot_files_top: 5
  max_symbols_per_file: 80
modules:
  app:
    paths:
      - "src/main/**"
  api:
    paths:
      - "src/api/**"
  lib:
    paths:
      - "lib/**"
use_git_churn: false
""",
            )
            base = ["--root", str(repo), "--config", str(cfg)]
            rc, _, err = _run_atlas([*base, "gen"])
            self.assertEqual(rc, 0, err)

            rc, out, err = _run_atlas([*base, "find", "greet"])
            self.assertEqual(rc, 0, err)
            lines = [ln for ln in out.splitlines() if ln.strip()]
            self.assertLessEqual(len(lines), 20)
            self.assertGreaterEqual(len(lines), 1)
            greet_line = next(ln for ln in lines if "greet(String, int)" in ln)
            parts = greet_line.split("\t")
            self.assertEqual(len(parts), 4)
            self.assertEqual(parts[0], "app")
            self.assertEqual(parts[1], "main")
            self.assertEqual(parts[2], "Hello.greet(String, int)")
            self.assertRegex(parts[3], r"^src/main/java/pkg/Hello.java:\d+$")
            self.assertNotIn("{", out)
            self.assertNotIn("public class", out)

            rc, out, _ = _run_atlas([*base, "find", "HandleLogin"])
            self.assertEqual(rc, 0)
            login = [ln for ln in out.splitlines() if ln.strip()]
            self.assertEqual(len(login), 1)
            login_parts = login[0].split("\t")
            self.assertEqual(len(login_parts), 4)
            self.assertEqual(login_parts[0], "api")
            self.assertEqual(login_parts[1], "main")
            header, *rows = (
                repo / "docs" / "map" / "generated" / "symbols.tsv"
            ).read_text(encoding="utf-8").splitlines()
            cols = header.split("\t")
            login_row = next(
                dict(zip(cols, row.split("\t")))
                for row in rows
                if row.split("\t")[0] == "HandleLogin"
            )
            signature = login_row.get("signature", "")
            owner = login_row.get("owner", "")
            self.assertEqual(owner, "")
            self.assertEqual(login_parts[2], signature)
            self.assertNotIn(".", login_parts[2])
            self.assertRegex(login_parts[3], r"^src/api/handler.go:\d+$")

            rc, out, _ = _run_atlas([*base, "find", "greet", "--module", "lib"])
            self.assertEqual(rc, 0)
            filtered = [ln for ln in out.splitlines() if ln.strip()]
            self.assertTrue(filtered)
            self.assertTrue(all(ln.split("\t")[0] == "lib" for ln in filtered))
            self.assertTrue(any("greetOther" in ln for ln in filtered))
            self.assertFalse(any("greet(String, int)" in ln for ln in filtered))

            rc, out, _ = _run_atlas([*base, "find", "Greet"])
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")

            rc, out, _ = _run_atlas([*base, "find", "hit"])
            self.assertEqual(rc, 0)
            hit_lines = [ln for ln in out.splitlines() if ln.strip()]
            self.assertEqual(len(hit_lines), 20)

            rc, out, _ = _run_atlas([*base, "find", "NoSuchSymbolZZZ"])
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")

            tsv = repo / "docs" / "map" / "generated" / "symbols.tsv"
            tsv.write_text(
                "symbol\tfile\tline\tkind\tlanguage\tmodule\n"
                "Orphan\tsrc/Orphan.java\t3\tmethod\tjava\tapp\n",
                encoding="utf-8",
            )
            rc, out, err = _run_atlas([*base, "find", "Orphan"])
            self.assertEqual(rc, 0, err)
            orphan = [ln for ln in out.splitlines() if ln.strip()]
            self.assertEqual(orphan[0].split("\t")[2], "Orphan")

            empty = tmp / "empty"
            empty.mkdir()
            rc, out, err = _run_atlas(["--root", str(empty), "find", "greet"])
            self.assertEqual(rc, 2)
            self.assertEqual(out, "")
            self.assertIn("run atlas gen first", err)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AtlasProtocolTest(unittest.TestCase):
    def test_agent_skill_prefers_find_and_main_shard(self) -> None:
        text = (TOOL_DIR / "agent-skill.md").read_text(encoding="utf-8")
        start = text.index("4. **L3")
        end = text.index("5. **Scoped")
        l3 = text[start:end]
        self.assertIn("atlas find", l3)
        self.assertIn("docs/map/generated/modules/<id>-symbols.tsv", l3)
        self.assertNotIn("symbols.tsv` or `symbols.json", l3)

        readme = (TOOL_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("atlas find", readme)
        self.assertIn("-tests-symbols.tsv", readme)
        self.assertIn("owner", readme)
        skill = (TOOL_DIR / "skills" / "create-code-atlas" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("atlas find", skill)
        self.assertIn("-tests-symbols.tsv", skill)


if __name__ == "__main__":
    unittest.main()
