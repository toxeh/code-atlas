#!/usr/bin/env python3
"""Closed-loop publish and sandbox prepare, using local git remotes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
PUBLISH = TOOL_DIR / "ci" / "publish-atlas.sh"
PREPARE = TOOL_DIR / "sandbox" / "prepare.sh"


def run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def check(cmd: list[str], cwd: Path | None = None) -> None:
    proc = run(cmd, cwd=cwd)
    if proc.returncode != 0:
        raise AssertionError(f"{cmd} -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}")


def git_identity(repo: Path) -> None:
    check(["git", "config", "user.email", "atlas-test@example.com"], cwd=repo)
    check(["git", "config", "user.name", "atlas-test"], cwd=repo)


def git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    proc = run(["git", "init", "-b", "main"], cwd=repo)
    if proc.returncode != 0:
        check(["git", "init"], cwd=repo)
        check(["git", "checkout", "-b", "main"], cwd=repo)
    git_identity(repo)


class ClosedLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="atlas-closed-"))
        self.repo = self.tmp / "product"
        self.bare = self.tmp / "atlas.git"
        self._build_product()
        self._init_bare()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_product(self) -> None:
        git_init(self.repo)
        src = self.repo / "src"
        src.mkdir()
        (src / "app.py").write_text("def run_app():\n    return 'ok'\n", encoding="utf-8")
        init = run(
            [sys.executable, str(TOOL_DIR / "atlas.py"), "--root", str(self.repo), "init", "--module", "app"],
        )
        self.assertEqual(init.returncode, 0, init.stdout + "\n" + init.stderr)

    def _finish_map_and_commit(self) -> None:
        (self.repo / "docs" / "map" / "ATLAS.md").write_text(
            """# Code Atlas

## Topology

```text
app
```

## Modules

| ID | Paths | Responsibility | Do not |
|---|---|---|---|
| app | `src` | entry | secrets |

## Intent → landing

| If you need to… | Start here | Then | Module |
|---|---|---|---|
| change the app | `src/app.py` | tests | app |

## Hot files

| File | Why hot | Module |
|---|---|---|
| `src/app.py` | entrypoint | app |

## Landmines

- marker: HAND-FROM-SOURCE

## Navigation protocol

1. Open ATLAS
2. Open the module card
3. Use generated symbols

## Verify cheatsheet

- python3 -m unittest
""",
            encoding="utf-8",
        )
        (self.repo / "docs" / "map" / "atlas.config.yaml").write_text(
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
modules:
  app:
    paths:
      - "src/**"
use_git_churn: false
follow_dir_symlinks: false
""",
            encoding="utf-8",
        )
        check(["git", "add", "-A"], cwd=self.repo)
        check(["git", "commit", "-m", "product with map"], cwd=self.repo)

    def _init_bare(self) -> None:
        proc = run(["git", "init", "--bare", "-b", "main", str(self.bare)])
        if proc.returncode != 0:
            check(["git", "init", "--bare", str(self.bare)])
        check(["git", "--git-dir", str(self.bare), "symbolic-ref", "HEAD", "refs/heads/main"])

    def _publish_env(self, **extra: str) -> dict[str, str]:
        env = {
            "SOURCE_ROOT": str(self.repo),
            "ATLAS_REMOTE": str(self.bare),
            "ATLAS_TOOL_DIR": str(TOOL_DIR),
            "CI_PROJECT_PATH": "group/demo",
            "ATLAS_PROJECT_PATH": "group/demo-atlas",
            "GIT_AUTHOR_NAME": "atlas-test",
            "GIT_AUTHOR_EMAIL": "atlas-test@example.com",
            "GIT_COMMITTER_NAME": "atlas-test",
            "GIT_COMMITTER_EMAIL": "atlas-test@example.com",
        }
        env.update(extra)
        return env

    def _publish(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return run(["bash", str(PUBLISH)], env=self._publish_env(**extra))

    def _snapshot_count(self) -> int:
        proc = run(["git", "--git-dir", str(self.bare), "rev-list", "--count", "main"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return int(proc.stdout.strip())

    def _show(self, path: str) -> str:
        proc = run(["git", "--git-dir", str(self.bare), "show", f"main:{path}"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def _product_commits(self) -> int:
        proc = run(["git", "rev-list", "--count", "HEAD"], cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return int(proc.stdout.strip())

    def _prepare(self, work: Path, ref: str) -> subprocess.CompletedProcess[str]:
        return run(
            [
                "bash",
                str(PREPARE),
                "--work",
                str(work),
                "--src",
                str(self.repo),
                "--atlas",
                str(self.bare),
                "--ref",
                ref,
                "--tool",
                str(TOOL_DIR),
            ]
        )

    def test_refuses_to_publish_into_the_source_project(self) -> None:
        self._finish_map_and_commit()
        proc = self._publish(CI_PROJECT_PATH="group/demo", ATLAS_PROJECT_PATH="group/demo")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("separate snapshot project", proc.stderr)

    def test_publish_prepare_and_branch_drift(self) -> None:
        self._finish_map_and_commit()
        before = self._product_commits()
        first = self._publish()
        self.assertEqual(first.returncode, 0, first.stdout + "\n" + first.stderr)
        self.assertEqual(self._product_commits(), before)
        self.assertEqual(self._snapshot_count(), 1)
        source_sha = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        source_text = self._show("SOURCE")
        self.assertIn("project: group/demo\n", source_text)
        self.assertIn(f"sha: {source_sha}\n", source_text)
        self.assertIn("ref: main\n", source_text)
        self.assertIn("run_app", self._show("docs/map/generated/symbols.tsv"))
        self.assertIn("marker: HAND-FROM-SOURCE", self._show("docs/map/ATLAS.md"))
        self.assertIn("navigate-code-atlas", self._show("agent-skill.md"))

        second = self._publish()
        self.assertEqual(second.returncode, 0, second.stdout + "\n" + second.stderr)
        self.assertIn("snapshot unchanged", second.stdout)
        self.assertEqual(self._snapshot_count(), 1)
        self.assertEqual(self._product_commits(), before)

        edited = self.tmp / "snapshot-edit"
        check(["git", "clone", str(self.bare), str(edited)])
        git_identity(edited)
        canary = edited / "docs" / "map" / "generated" / "CANARY"
        canary.write_text("keep\n", encoding="utf-8")
        check(["git", "add", "docs/map/generated/CANARY"], cwd=edited)
        check(["git", "commit", "-m", "canary"], cwd=edited)
        check(["git", "push", "origin", "HEAD:main"], cwd=edited)

        work = self.tmp / "work-main"
        prepared = self._prepare(work, "main")
        self.assertEqual(prepared.returncode, 0, prepared.stdout + "\n" + prepared.stderr)
        ready = (work / "READY").read_text(encoding="utf-8")
        self.assertIn("mode: reuse-l3", ready)
        self.assertIn("push: no", ready)
        self.assertEqual(
            (work / "src" / "docs" / "map" / "generated" / "CANARY").read_text(encoding="utf-8"),
            "keep\n",
        )
        self.assertIn(
            "marker: HAND-FROM-SOURCE",
            (work / "src" / "docs" / "map" / "ATLAS.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(self._snapshot_count(), 2)

        app = self.repo / "src" / "app.py"
        app.write_text(app.read_text(encoding="utf-8") + "\ndef extra():\n    return 1\n", encoding="utf-8")
        check(["git", "add", "src/app.py"], cwd=self.repo)
        check(["git", "commit", "-m", "extra symbol"], cwd=self.repo)
        drifted = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        self.assertNotEqual(drifted, source_sha)

        work_drift = self.tmp / "work-drift"
        drifted_prep = self._prepare(work_drift, "main")
        self.assertEqual(drifted_prep.returncode, 0, drifted_prep.stdout + "\n" + drifted_prep.stderr)
        drift_ready = (work_drift / "READY").read_text(encoding="utf-8")
        self.assertIn("mode: regen-l3", drift_ready)
        self.assertIn("validate: ok", drift_ready)
        self.assertIn(
            "marker: HAND-FROM-SOURCE",
            (work_drift / "src" / "docs" / "map" / "ATLAS.md").read_text(encoding="utf-8"),
        )
        self.assertFalse((work_drift / "src" / "docs" / "map" / "generated" / "CANARY").exists())
        meta = (work_drift / "src" / "docs" / "map" / "generated" / "meta.json").read_text(encoding="utf-8")
        self.assertIn(drifted, meta)
        self.assertIn(
            "extra",
            (work_drift / "src" / "docs" / "map" / "generated" / "symbols.tsv").read_text(encoding="utf-8"),
        )
        self.assertEqual(self._snapshot_count(), 2)

        check(["git", "checkout", "-b", "feature"], cwd=self.repo)
        shutil.rmtree(self.repo / "docs" / "map")
        (self.repo / "src" / "app.py").write_text("def feature_only():\n    return 2\n", encoding="utf-8")
        check(["git", "add", "-A"], cwd=self.repo)
        check(["git", "commit", "-m", "feature without map"], cwd=self.repo)
        feature_sha = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()

        work_feature = self.tmp / "work-feature"
        feature_prep = self._prepare(work_feature, "feature")
        self.assertEqual(feature_prep.returncode, 0, feature_prep.stdout + "\n" + feature_prep.stderr)
        feature_ready = (work_feature / "READY").read_text(encoding="utf-8")
        self.assertIn("mode: regen-published-hand", feature_ready)
        self.assertIn("validate: ok", feature_ready)
        self.assertIn(
            "marker: HAND-FROM-SOURCE",
            (work_feature / "src" / "docs" / "map" / "ATLAS.md").read_text(encoding="utf-8"),
        )
        feature_meta = (work_feature / "src" / "docs" / "map" / "generated" / "meta.json").read_text(encoding="utf-8")
        self.assertIn(feature_sha, feature_meta)
        self.assertNotIn(source_sha, feature_meta)
        symbols = (work_feature / "src" / "docs" / "map" / "generated" / "symbols.tsv").read_text(encoding="utf-8")
        self.assertIn("feature_only", symbols)
        self.assertEqual(self._snapshot_count(), 2)
        self.assertEqual(self._product_commits(), before + 2)


if __name__ == "__main__":
    unittest.main()
