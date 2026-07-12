# Code Atlas

> Navigation index for humans and AI agents. **Not** an architecture essay.
> Protocol: [Navigation protocol](#navigation-protocol).

## Topology

```text
# Replace with coarse runtime/build topology (≤15 nodes).
# Example:
# client → gateway → service-a
#                 → service-b → worker
```

## Modules

| ID | Paths | Responsibility | Do not |
|---|---|---|---|
| example | `path/to/example` | one-line purpose | wrong layer / forbidden deps |

## Intent → landing

| If you need to… | Start here | Then | Module |
|---|---|---|---|
| change X | `path/file.ext` (symbol) | related file / tests | example |

## Hot files

| File | Why hot | Module |
|---|---|---|
| `path/big_file.ext` | size / churn / critical path | example |

> Tip: after `atlas gen`, compare with `generated/hot-files.json`.

## Landmines

- Absolute prohibitions, ordering constraints, freeze zones, secret/config footguns.
- Prefer imperative, testable bullets.

## Navigation protocol

Before broad repository search or reading a large file:

1. Open L0 rules (`project-context.md` / `AGENTS.md`) for prohibitions.
2. Open this `ATLAS.md`.
3. Match the task → **Intent → landing** → module id + landing paths.
4. Open `modules/<module-id>.md`.
5. If the target file is large: use `generated/outlines.md` / `generated/symbols.json` first.
6. Search **only** inside the module paths (or landing paths).
7. Read a window around the symbol (≈ ±60–100 lines), not the whole file.
8. Open the nearest test listed on the module card before changing behavior.
9. After edit: re-read only touched windows; re-run `atlas gen` if entrypoints/symbols moved.
10. If the intent was missing: add/adjust an Intent row (atlas debt), don’t only fix code.

### Anti-patterns

- Full-file read of large files “to understand” without outline/symbols
- Repo-wide grep for generic tokens (`handle`, `process`, `run`, `util`) before module selection
- Opening many candidate modules without returning to the Intent table
- Editing without checking module **Do not** / Landmines

## Verify cheatsheet

- Primary test command: `…`
- Primary build/lint command: `…`

## Ownership (optional)

| Module | Owner |
|---|---|
| example | team-or-person |
