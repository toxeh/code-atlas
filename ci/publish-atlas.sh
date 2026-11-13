#!/usr/bin/env bash
# Publish a Code Atlas snapshot of the current source checkout.
#
# Reads the hand-written map from the source repo, rebuilds L3 there, and
# pushes docs/map plus a SOURCE pin to a separate GitLab project. Does not
# commit anything in the source repo.
#
# Required (one of):
#   ATLAS_REMOTE          git URL or local path of the snapshot repo
#   ATLAS_PROJECT_PATH    GitLab path, e.g. group/name-atlas
#                         (with CI_SERVER_URL and ATLAS_PUSH_TOKEN)
#
# Optional:
#   SOURCE_ROOT           source checkout (default: CI_PROJECT_DIR, else cwd)
#   ATLAS_TOOL_DIR        directory that contains atlas.py (default: this repo)
#   ATLAS_BRANCH          snapshot branch (default: main)
#   CI_PROJECT_PATH       recorded in SOURCE as the product path
#   CI_COMMIT_REF_NAME    recorded in SOURCE as the ref
#   PYTHON                python interpreter (default: python3)
#
# A second publish of the same source commit does nothing when the only
# differences are meta.json generated_at and the absolute root path.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ATLAS_TOOL_DIR="${ATLAS_TOOL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON="${PYTHON:-python3}"
ATLAS_BRANCH="${ATLAS_BRANCH:-main}"
SOURCE_ROOT="${SOURCE_ROOT:-${CI_PROJECT_DIR:-$PWD}}"
SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"

if [[ ! -f "$ATLAS_TOOL_DIR/atlas.py" ]]; then
  echo "error: atlas.py not found under ATLAS_TOOL_DIR=$ATLAS_TOOL_DIR" >&2
  exit 2
fi

if ! git -C "$SOURCE_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: source is not a git checkout: $SOURCE_ROOT" >&2
  exit 1
fi

if [[ -n "${ATLAS_PROJECT_PATH:-}" && -n "${CI_PROJECT_PATH:-}" && "$ATLAS_PROJECT_PATH" == "$CI_PROJECT_PATH" ]]; then
  echo "error: ATLAS_PROJECT_PATH must be a separate snapshot project, not the source" >&2
  exit 2
fi

sha="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
if [[ -n "${CI_COMMIT_SHA:-}" && "$CI_COMMIT_SHA" != "$sha" ]]; then
  echo "error: CI_COMMIT_SHA ($CI_COMMIT_SHA) != source HEAD ($sha)" >&2
  exit 1
fi

ref="${CI_COMMIT_REF_NAME:-$(git -C "$SOURCE_ROOT" rev-parse --abbrev-ref HEAD)}"
project="${CI_PROJECT_PATH:-$(basename "$SOURCE_ROOT")}"
if git -C "$ATLAS_TOOL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
  tool_sha="$(git -C "$ATLAS_TOOL_DIR" rev-parse HEAD)"
else
  tool_sha="unknown"
fi

if [[ ! -f "$SOURCE_ROOT/docs/map/ATLAS.md" ]]; then
  echo "error: $SOURCE_ROOT/docs/map/ATLAS.md is missing; the hand-written map lives in the source repo" >&2
  exit 1
fi

echo "gen+validate ${project} @ ${sha}"
"$PYTHON" "$ATLAS_TOOL_DIR/atlas.py" --root "$SOURCE_ROOT" gen
"$PYTHON" "$ATLAS_TOOL_DIR/atlas.py" --root "$SOURCE_ROOT" validate

if [[ -n "${ATLAS_REMOTE:-}" ]]; then
  remote="$ATLAS_REMOTE"
elif [[ -n "${ATLAS_PROJECT_PATH:-}" ]]; then
  : "${ATLAS_PUSH_TOKEN:?set ATLAS_PUSH_TOKEN (write access to the snapshot project only)}"
  : "${CI_SERVER_URL:?set CI_SERVER_URL}"
  base="${CI_SERVER_URL%/}/${ATLAS_PROJECT_PATH}.git"
  case "$base" in
    https://*) remote="https://atlas-bot@${base#https://}" ;;
    http://*) remote="http://atlas-bot@${base#http://}" ;;
    *)
      echo "error: CI_SERVER_URL must be http or https" >&2
      exit 2
      ;;
  esac
else
  echo "error: set ATLAS_REMOTE or ATLAS_PROJECT_PATH" >&2
  exit 2
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/atlas-publish.XXXXXX")"
cleanup() {
  rm -rf "$workdir"
}
trap cleanup EXIT

export GIT_TERMINAL_PROMPT=0
if [[ -n "${ATLAS_PUSH_TOKEN:-}" ]]; then
  askpass="$workdir/askpass"
  cat > "$askpass" <<'EOF'
#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\n' "${GIT_ASKPASS_USERNAME:-atlas-bot}" ;;
  *) printf '%s\n' "$ATLAS_PUSH_TOKEN" ;;
esac
EOF
  chmod 700 "$askpass"
  export GIT_ASKPASS="$askpass"
fi

dest="$workdir/snapshot"
if ! git clone --depth 50 --branch "$ATLAS_BRANCH" "$remote" "$dest"; then
  rm -rf "$dest"
  git clone "$remote" "$dest"
fi

git -C "$dest" config user.name "${ATLAS_BOT_NAME:-atlas-bot}"
git -C "$dest" config user.email "${ATLAS_BOT_EMAIL:-atlas-bot@localhost}"

rm -rf "$dest/docs/map"
mkdir -p "$dest/docs"
cp -a "$SOURCE_ROOT/docs/map" "$dest/docs/map"

cat > "$dest/SOURCE" <<EOF
project: ${project}
sha: ${sha}
ref: ${ref}
tool_sha: ${tool_sha}
EOF

if [[ -f "$ATLAS_TOOL_DIR/agent-skill.md" ]]; then
  cp "$ATLAS_TOOL_DIR/agent-skill.md" "$dest/agent-skill.md"
fi

# Exit 0 when the snapshot already describes this source commit and the index
# differs only by clock and absolute runner path.
snapshot_unchanged() {
  "$PYTHON" - "$dest" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])

def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )

status = git("status", "--porcelain")
if status.returncode != 0:
    sys.exit(1)
lines = [line for line in status.stdout.splitlines() if line.strip()]
if not lines:
    sys.exit(0)

changed: list[str] = []
for line in lines:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    changed.append(path)

if changed != ["docs/map/generated/meta.json"]:
    sys.exit(1)

shown = git("show", "HEAD:docs/map/generated/meta.json")
if shown.returncode != 0:
    sys.exit(1)
previous = json.loads(shown.stdout)
current = json.loads((root / "docs/map/generated/meta.json").read_text(encoding="utf-8"))
for key in ("generated_at", "root"):
    previous.pop(key, None)
    current.pop(key, None)
sys.exit(0 if previous == current else 1)
PY
}

if snapshot_unchanged; then
  echo "snapshot unchanged (${project} @ ${sha}), not pushing"
  exit 0
fi

git -C "$dest" add -A
git -C "$dest" commit -m "atlas: ${project} @ ${sha}"

push_snapshot() {
  local attempt
  for attempt in 1 2 3; do
    if git -C "$dest" rev-parse --verify --quiet "refs/remotes/origin/${ATLAS_BRANCH}" >/dev/null; then
      if ! git -C "$dest" pull --rebase origin "$ATLAS_BRANCH"; then
        echo "rebase onto ${ATLAS_BRANCH} failed, retry ${attempt}/3" >&2
        git -C "$dest" fetch origin "$ATLAS_BRANCH" || true
        sleep $((attempt * 2))
        continue
      fi
    fi
    if git -C "$dest" push origin "HEAD:${ATLAS_BRANCH}"; then
      return 0
    fi
    echo "push rejected, retry ${attempt}/3" >&2
    git -C "$dest" fetch origin "$ATLAS_BRANCH" || true
    sleep $((attempt * 2))
  done
  return 1
}

push_snapshot
echo "published ${project} @ ${sha} -> ${ATLAS_BRANCH}"
