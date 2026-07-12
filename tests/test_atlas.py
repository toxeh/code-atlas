#!/usr/bin/env python3
"""Smoke tests for Code Atlas toolkit (stdlib unittest)."""

from __future__ import annotations

import json
import shutil
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

    def test_follow_dir_symlink_like_claudia(self) -> None:
        """Directory symlink mount: repo/plugin -> external tree (ai-proxy/claudia pattern)."""
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


if __name__ == "__main__":
    unittest.main()
