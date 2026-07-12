# Code Atlas Toolkit

Portable toolkit for building a **Code Atlas** in any repository: a hierarchical navigation index for humans and AI agents.

> Spec: see conversation artifact `code_atlas_spec.md` (universal requirements).  
> This directory is the **implementation** you can copy into any project.

## Why

Agents and humans waste time on:

- repo-wide grep for generic names
- reading multi-hundred-line files end-to-end
- missing landmines / wrong module

Atlas answers **“where do I look first?”** before code is opened.

## Layers

| Layer | Artifact | Author |
|---|---|---|
| L0 rules | `project-context.md` / `AGENTS.md` (snippet provided) | human |
| L1 phone book | `docs/map/ATLAS.md` | human |
| L2 module cards | `docs/map/modules/<id>.md` | human |
| L3 index | `docs/map/generated/*` | `atlas.py gen` |

## Requirements

- Python 3.10+ (stdlib only)
- Optional: `git` for churn-based hot-file ranking
- Optional: `rsync` for `install.sh` copy mode (macOS/Linux usually have it)

### Directory symlinks (e.g. `./claudia`)

By default (`follow_dir_symlinks: true`) Atlas **enters directory symlinks** such as:

```text
ai-proxy/claudia -> /Volumes/.../claudia
```

Indexed paths stay **logical** under the repo root (`claudia/src/...`), not the realpath target.  
Mounts are recorded in `generated/meta.json` as `symlink_mounts`. Cycle-safe via realpath of entered dirs.

Disable if needed:

```yaml
follow_dir_symlinks: false
```

## Install system-wide (macOS / Linux user install)

Recommended: put the CLI on your PATH once, then use it in any repo.

```bash
# from the toolkit directory
./tools/code-atlas/install.sh

# or explicitly
./tools/code-atlas/install.sh --prefix "$HOME/.local"
```

What it installs:

| Path | Role |
|---|---|
| `~/.local/share/code-atlas/` | toolkit copy (templates + `atlas.py`) |
| `~/.local/bin/atlas` | launcher on PATH |

Dev mode (live symlink to this checkout instead of copy):

```bash
./tools/code-atlas/install.sh --link
```

Uninstall:

```bash
./tools/code-atlas/install.sh --uninstall
```

If `~/.local/bin` is not on PATH (zsh):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

Smoke:

```bash
atlas --help
```

> **Note:** system install gives you the **CLI**. Each repository still needs its own `docs/map/` (see below).

## Install into a repo (vendored copy, no global CLI)

```bash
# from your repo root
mkdir -p tools
cp -R /path/to/this/code-atlas tools/code-atlas
# or submodule / subtree — your choice
```

## Bootstrap atlas in a repository

After system or vendored install:

```bash
cd /path/to/your/project
atlas init
# or: python3 tools/code-atlas/atlas.py init

atlas init --module auth --module api --module worker
# edit docs/map/ATLAS.md + modules/*.md + atlas.config.yaml
atlas gen
atlas validate
```

Paste `docs/map/L0-snippet.md` into `AGENTS.md` / `project-context.md` / `.cursor/rules`.

### Agent skill (optional)

Copy the protocol skill where your agent loads skills from, e.g.:

```bash
mkdir -p .agents/skills/navigate-code-atlas
cp "$(dirname $(readlink -f $(which atlas) 2>/dev/null || which atlas))/../share/code-atlas/agent-skill.md" \
  .agents/skills/navigate-code-atlas/SKILL.md
```

Simpler if you know the share path:

```bash
mkdir -p .agents/skills/navigate-code-atlas
cp ~/.local/share/code-atlas/agent-skill.md .agents/skills/navigate-code-atlas/SKILL.md
```

## Commands

```bash
# 1) Bootstrap skeleton
atlas init
atlas init --module auth --module api --module worker

# 2) Edit hand-written layers
#    docs/map/ATLAS.md
#    docs/map/modules/*.md
#    docs/map/atlas.config.yaml   # module path globs

# 3) Generate L3
atlas gen

# 4) Validate
atlas validate

# Debug outline for one file
atlas outline path/to/file.go
```

Useful flags:

```bash
atlas --root /path/to/repo gen
atlas --config docs/map/atlas.config.yaml validate
```

## Generated outputs

Under `docs/map/generated/`:

| File | Purpose |
|---|---|
| `tree.txt` | all indexed files + line counts + module |
| `hot-files.json` | top files by size/churn |
| `outlines.md` | definition outlines for large files |
| `symbols.json` / `symbols.tsv` | jump table symbol → file:line |
| `meta.json` | timestamp, counts, warnings |

## Agent protocol (mandatory)

```text
1. Read L0 prohibitions
2. Open docs/map/ATLAS.md → Intent table → module
3. Open docs/map/modules/<id>.md
4. Use generated outlines/symbols for large files
5. Search only inside module paths
6. Read ±60–100 lines around the symbol
7. Open listed tests before behavior changes
```

Copy `templates/L0-snippet.md` (or `docs/map/L0-snippet.md` after init) into your agent rules.

Skill pack for agents: [`agent-skill.md`](agent-skill.md).

## Config sketch

See [`config.example.yaml`](config.example.yaml). Critical part:

```yaml
modules:
  auth:
    paths:
      - "**/auth/**"
      - "**/identity/**"
  api:
    paths:
      - "**/api/**"
```

First matching glob wins. Unmapped hot files are reported as warnings.

## Validation

`validate` checks:

- required ATLAS sections
- module rows ↔ `modules/<id>.md` cards
- referenced concrete file paths exist
- module card soft size budget
- generated artifacts presence / generator warnings

Exit code `1` on errors, `0` if only warnings/ok.

## Suggested PR rule

If a PR moves public entrypoints or module boundaries, it must update:

- Intent row and/or module card
- module globs in `atlas.config.yaml` when paths change

Optionally run in CI:

```bash
python3 tools/code-atlas/atlas.py gen
python3 tools/code-atlas/atlas.py validate
```

## Design constraints

- No third-party Python deps
- Language adapters are **heuristic** (regex definitions). Good enough for navigation; not a full LSP.
- Hand-written content must stay small; generated content must be rebuildable anytime.

## Layout of this toolkit

```text
tools/code-atlas/
  atlas.py              # CLI
  config.example.yaml
  README.md
  agent-skill.md
  templates/
    ATLAS.md
    module.md
    L0-snippet.md
    map-README.md
  tests/
    test_atlas.py
    fixtures/sample_repo/
```

## Non-goals

- Full call graphs
- Replacing IDE go-to-definition for interactive humans
- Architecture decision records / product docs
