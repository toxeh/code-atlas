---
name: create-code-atlas
description: >-
  Create or bootstrap a Code Atlas navigation index for any repository
  (docs/map/ATLAS.md, module cards, generated symbols, agent protocol).
  Use when the user wants to create an atlas, code map, navigation index,
  docs/map, project map for agents, bootstrap code-atlas, or runs
  /create-code-atlas. Also use when agents get lost in large/monolithic codebases
  and need a first-door map.
compatibility: Requires Python 3.10+ and the `atlas` CLI (from code-atlas install.sh or ~/.local/share/code-atlas/atlas.py).
metadata:
  short-description: "Bootstrap Code Atlas for a repo"
  author: ToxeH
argument-hint: "[module-ids…]  e.g. /create-code-atlas auth api worker"
---

# Create Code Atlas

Build a **Code Atlas** for the current repository: a hierarchical navigation index so humans and AI agents find the right door before grepping or reading huge files.

## Goal

Ship a working atlas skeleton + L3 generated index + agent protocol hook:

| Layer | Path | Who writes |
|---|---|---|
| L0 rules snippet | paste into `AGENTS.md` / `project-context.md` / `.grok` rules | human+agent |
| L1 phone book | `docs/map/ATLAS.md` | human (+ you draft) |
| L2 module cards | `docs/map/modules/<id>.md` | human (+ you draft) |
| L3 machine index | `docs/map/generated/*` | `atlas gen` only |

## Preconditions

1. Work from the **repository root** (or pass `--root`).
2. Ensure CLI is available:

```bash
command -v atlas || true
atlas --help || python3 "${ATLAS_HOME:-$HOME/.local/share/code-atlas}/atlas.py" --help
```

If missing, install from the **code-atlas** checkout:

```bash
# system install (CLI + this Grok skill)
/path/to/code-atlas/install.sh
# e.g. ~/dev/code-atlas/install.sh
```

Prefer the `atlas` command on PATH after install (`~/.local/bin/atlas`).

## Protocol (do in order)

### Step 0 — Detect state

```bash
pwd
ls -la docs/map 2>/dev/null || true
ls -la docs/map/ATLAS.md docs/map/modules 2>/dev/null || true
```

- If `docs/map/ATLAS.md` already exists: **update / enrich** mode (do not overwrite blindly).
- If missing: **bootstrap** mode.

### Step 1 — Inventory (cheap)

Gather facts with shell (do not read giant files end-to-end):

```bash
# top-level layout
ls -la

# largest source-ish files (adjust globs to stack)
find . -type f \( -name '*.go' -o -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.kt' -o -name '*.java' -o -name '*.rs' \) \
  -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/vendor/*' -not -path '*/dist/*' -not -path '*/build/*' \
  2>/dev/null | head -5 >/dev/null

# line counts for hot files
find . -type f \( -name '*.go' -o -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.kt' -o -name '*.java' -o -name '*.rs' \) \
  -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/vendor/*' -not -path '*/dist/*' -not -path '*/build/*' \
  -print0 2>/dev/null | xargs -0 wc -l 2>/dev/null | sort -n | tail -30

# directory symlinks (must be supported; e.g. ./claudia -> external tree)
find . -maxdepth 2 -type l -ls 2>/dev/null | head -30

# entrypoints heuristics
rg -n --glob '!**/node_modules/**' --glob '!**/.git/**' 'func main\(|^def main\(|fn main\(|public static void main' -S . 2>/dev/null | head -40
```

Infer **change-boundary modules** (what is edited together), not every package:

- services / binaries / plugin surfaces
- major domains (auth, api, ui, worker, proxy, …)
- symlink mounts that act as subprojects

If the user passed module ids as arguments to `/create-code-atlas`, prefer those names.

### Step 2 — Bootstrap skeleton

```bash
# modules from args or inferred list
atlas init --module <m1> --module <m2> --module <m3>
# or without modules first:
# atlas init
```

If `atlas` is not on PATH:

```bash
python3 "$HOME/.local/share/code-atlas/atlas.py" init --module <m1> --module <m2>
```

Do **not** use `--force` unless the user explicitly asks to overwrite existing atlas files.

### Step 3 — Configure module path globs

Edit `docs/map/atlas.config.yaml`:

- Map each module id → path globs (first match wins)
- Keep `follow_dir_symlinks: true` (default) so mounts like `./claudia` are indexed as logical `claudia/...`
- Set `use_git_churn: true` when `.git` exists

Example shape:

```yaml
modules:
  claudia:
    paths:
      - "claudia/**"
  proxy:
    paths:
      - "**/local-proxy/**"
      - "**/remote-proxy/**"
follow_dir_symlinks: true
use_git_churn: true
```

### Step 4 — Draft L1 ATLAS.md (human intent)

Rewrite `docs/map/ATLAS.md` with **real** content for this repo. Keep it ~1–2 screens.

Required sections (headings must remain discoverable for `atlas validate`):

1. **Topology** — ≤15 nodes, runtime/build
2. **Modules** table — id | paths | responsibility | do not
3. **Intent → landing** — “if you need to… / start here / then / module”
4. **Hot files** — from inventory + why
5. **Landmines** — freezes, ordering, prod resources, secrets
6. **Navigation protocol** — keep the standard 10-step protocol (from template)
7. **Verify cheatsheet** — primary test/build commands for this repo

Rules:

- Navigation over narration — pointers, not essays
- Prefer concrete paths and symbol names
- Intent rows come from real work (recent PR themes, user goals, entrypoints)
- Do not paste large code blocks into ATLAS

### Step 5 — Draft L2 module cards

For each module id in the Modules table, fill `docs/map/modules/<id>.md`:

- Purpose (1–3 sentences)
- Paths
- Entrypoints
- Key types / interfaces (load-bearing only)
- Coarse flow (3–10 edges)
- Hot symbols table
- Tests
- Config & runtime deps
- Anti-patterns / Do not
- Related modules

Soft cap: ~1 screen / ≤ ~120 lines. Split modules if a card balloons.

### Step 6 — Generate L3 + validate

```bash
atlas gen
atlas validate
atlas find <Symbol>
```

`atlas gen` writes `owner`, `signature`, and `role` on each symbol. Main rows land in `docs/map/generated/modules/<id>-symbols.tsv`. Test rows (`src/test`, `src/it`) land in `<id>-tests-symbols.tsv`. Query with `atlas find` instead of opening the monolith.

Expect:

- `docs/map/generated/tree.txt`
- `hot-files.json`, `outlines.md`, `symbols.json`, `symbols.tsv`, `meta.json`
- `modules/<id>-symbols.tsv` and `modules/<id>-tests-symbols.tsv`
- `meta.json.symlink_mounts` lists directory symlink mounts when present

Fix **errors** from validate (missing cards, missing concrete paths, missing sections). Warnings about unmapped hot files → improve module globs or cards.

### Step 7 — Wire L0 agent protocol

1. Ensure `docs/map/L0-snippet.md` exists (from init).
2. If `AGENTS.md`, `project-context.md`, `CLAUDE.md`, or `.grok` project rules exist, **append** the L0 snippet if not already present (do not duplicate).
3. If none exist, create a minimal `AGENTS.md` (or `project-context.md` if the repo already uses that name) containing the L0 navigation rule pointing at `docs/map/ATLAS.md`.

Mandatory rule text (conceptually):

> Before broad repository search or reading large files, open `docs/map/ATLAS.md` and follow its Navigation protocol. Prefer module-scoped search and symbol windows from `docs/map/generated/`.

### Step 8 — Optional companion skill in repo

If the repo uses agent skills (`.agents/skills` or `.grok/skills`), install the navigate protocol:

```bash
mkdir -p .agents/skills/navigate-code-atlas
cp "$HOME/.local/share/code-atlas/agent-skill.md" .agents/skills/navigate-code-atlas/SKILL.md
# and/or
mkdir -p .grok/skills/navigate-code-atlas
cp "$HOME/.local/share/code-atlas/agent-skill.md" .grok/skills/navigate-code-atlas/SKILL.md
```

Only if the user wants repo-local navigation skill; creating the atlas itself does not require this.

### Step 9 — Report back

Summarize for the user:

1. Modules created / updated
2. Path to `docs/map/ATLAS.md`
3. Symlink mounts detected (if any)
4. `atlas validate` result (errors/warnings)
5. Next human edits still needed (thin intent rows, landmines from tribal knowledge)
6. How to refresh later: `atlas gen && atlas validate`

## Update mode (atlas already exists)

When `docs/map/ATLAS.md` exists:

1. Read ATLAS + module cards (not whole codebase)
2. Re-run inventory for drift (new hot files, new symlinks, moved entrypoints)
3. Patch intents/modules/landmines
4. Adjust `atlas.config.yaml` globs
5. `atlas gen && atlas validate`
6. Do **not** wipe hand-written content unless asked

## Hard rules

- **Intent is hand-written; inventory is generated.** Never hand-edit `docs/map/generated/**`.
- **Follow directory symlinks** by default (`follow_dir_symlinks: true`) — support mounts like `./claudia`.
- Index paths must stay **logical** (`claudia/src/...`), not external realpaths.
- Do not invent business architecture essays.
- Do not full-file-read multi-hundred-line sources while building the atlas; use outlines after first `gen` when possible.
- Prefer ≤ 3–10 modules on first bootstrap; expand later.
- If validate fails on missing path references you invented, fix the ATLAS paths to real files.

## Anti-patterns

| Don't | Do |
|---|---|
| One 2000-line ARCHITECTURE.md | short ATLAS + module cards |
| Module per tiny package | change-boundary modules |
| Skip Intent table | intents are the main agent value |
| `atlas init --force` without ask | preserve existing hand maps |
| Ignore symlink subprojects | map them as modules/mounts |

## Slash usage

```text
/create-code-atlas
/create-code-atlas auth api worker
/create-code-atlas   # then infer modules from tree
```

Natural language triggers: “создай атлас”, “create code atlas”, “bootstrap docs/map”, “navigation index for agents”.
