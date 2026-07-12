---
name: navigate-code-atlas
description: >-
  Navigate any codebase using Code Atlas before search or large-file reads.
  Use when editing code in a repo that has docs/map/ATLAS.md, when the user
  asks to find where something lives, or when about to grep/read a large file
  without a known landing path.
---

# Navigate via Code Atlas

## Goal

Reach the correct edit site with minimal tool calls: **door → card → symbol window**, not **repo-wide fishing**.

## When to activate

- Any non-trivial code change / bugfix / “where is X?”
- Before `grep` across the whole repo
- Before reading a file that may be large (>~200–300 lines)

## Protocol (mandatory order)

1. **L0 prohibitions**  
   Open `project-context.md`, `AGENTS.md`, `CLAUDE.md`, or equivalent if present. Honor freezes / prod rules.

2. **L1 ATLAS**  
   Open `docs/map/ATLAS.md` (or path from project config).  
   Use:
   - Intent → landing table
   - Modules table
   - Landmines

3. **L2 module card**  
   Open exactly one primary `docs/map/modules/<module-id>.md`.  
   Note entrypoints, hot symbols, tests, Do-not.

4. **L3 symbols/outlines (if file may be large)**  
   Prefer:
   - `docs/map/generated/symbols.tsv` or `symbols.json`
   - `docs/map/generated/outlines.md`
   - `docs/map/generated/hot-files.json`

   Jump by symbol name → file:line.

5. **Scoped search only**  
   Search inside module paths / landing paths from the card.  
   Do **not** start with repo-wide search for `handle|process|run|util|helper`.

6. **Windowed read**  
   Read approximately ±60–100 lines around the target symbol (adjust slightly if needed).  
   Avoid full-file reads of large files unless refactoring truly requires it.

7. **Tests as contract**  
   Open the test files listed on the module card before changing behavior.

8. **After edit**  
   Re-read only touched windows.  
   If entrypoints/module boundaries moved: update ATLAS/card and run:

   ```bash
   python3 tools/code-atlas/atlas.py gen
   python3 tools/code-atlas/atlas.py validate
   ```

9. **Atlas debt**  
   If Intent table had no row for this job, add one in the same session/PR when practical.

## Anti-patterns

| Don't | Do instead |
|---|---|
| Full-file read “for context” on large files | outline + window |
| 10 greps for a generic name | Intent table → module path grep |
| Open 5 modules “just in case” | one module; return to ATLAS if wrong |
| Ignore Landmines / Do not | read them before edit |
| Hand-edit `docs/map/generated/**` | run `atlas.py gen` |

## If Atlas is missing

1. Tell the user navigation index is missing.
2. Optionally bootstrap:

   ```bash
   python3 tools/code-atlas/atlas.py init
   ```

3. For the current task, fall back to: package README → entrypoints (`main`, routes, plugin.xml) → tests → narrow reads.  
4. Do not invent a fake ATLAS in chat without writing files when the user wants it persisted.

## Success bar for a single task

- ≤ ~4–6 tool calls to first correct edit site on a typical change
- Zero exploratory full-file reads of large files
- Module-scoped search first
