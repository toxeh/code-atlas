# Code Map

This directory is the **Code Atlas** for the repository: a navigation index for humans and AI agents.

## Layout

| Path | Layer | Who writes |
|---|---|---|
| `ATLAS.md` | L1 phone book | humans |
| `modules/*.md` | L2 module cards | humans (module owners) |
| `generated/*` | L3 symbol/outline index | `atlas gen` only |
| `atlas.config.yaml` | generator config | humans |

## Commands

From repo root (adjust path to toolkit if vendored elsewhere):

```bash
# Bootstrap map skeleton (once)
python3 tools/code-atlas/atlas.py init

# Regenerate L3 index
python3 tools/code-atlas/atlas.py gen

# Validate hand-written + generated consistency
python3 tools/code-atlas/atlas.py validate
```

## Agent rule

**Always** open `ATLAS.md` before broad search or large-file reads. See Navigation protocol inside ATLAS.

## Authoring rules

1. **Intent is hand-written; inventory is generated.**
2. Module = change-boundary (“what is edited together”), not every package.
3. Keep `ATLAS.md` to ~1–2 screens; keep module cards to ~1 screen.
4. When you move an entrypoint, update Intent/card in the same PR.
5. Never hand-edit `generated/`.
