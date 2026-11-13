#!/usr/bin/env bash
# Assemble one tree for an agent that is about to read a GitLab project.
#
# Clones the published atlas snapshot and the source ref into --work, then
# makes docs/map inside the source checkout match the rules from the closed
# loop:
#
#   * same commit as the snapshot: reuse published L3
#   * source ref has its own ATLAS.md: keep that hand-written map and rebuild L3
#   * source ref has no map: overlay the published hand-written map and rebuild L3
#
# The script never pushes. Point the agent at <work>/src and at <work>/READY.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_DIR="${ATLAS_TOOL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON="${PYTHON:-python3}"

usage() {
  echo "usage: prepare.sh --work DIR --src URL|PATH --atlas URL|PATH [--ref NAME|SHA] [--atlas-ref NAME] [--tool DIR]" >&2
  exit 2
}

work=""
src_url=""
atlas_url=""
ref="main"
atlas_ref="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work)
      work="${2:-}"
      shift 2
      ;;
    --src)
      src_url="${2:-}"
      shift 2
      ;;
    --atlas)
      atlas_url="${2:-}"
      shift 2
      ;;
    --ref)
      ref="${2:-}"
      shift 2
      ;;
    --atlas-ref)
      atlas_ref="${2:-}"
      shift 2
      ;;
    --tool)
      TOOL_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage
      ;;
  esac
done

[[ -n "$work" && -n "$src_url" && -n "$atlas_url" ]] || usage
if [[ ! -f "$TOOL_DIR/atlas.py" ]]; then
  echo "error: atlas.py not found under $TOOL_DIR" >&2
  exit 2
fi

mkdir -p "$work"
work="$(cd "$work" && pwd)"
if [[ -n "$(ls -A "$work")" ]]; then
  echo "error: work dir is not empty: $work" >&2
  exit 2
fi

is_sha() {
  [[ "$1" =~ ^[0-9a-fA-F]{40}$ ]]
}

# Local paths keep full history so a raw commit SHA can be checked out.
# Remote URLs stay shallow. Empty arrays are avoided so bash 3.2 + set -u works.
clone_ref() {
  local url="$1"
  local want="$2"
  local dest="$3"
  if is_sha "$want"; then
    if [[ -d "$url" ]]; then
      git clone "$url" "$dest"
    else
      git clone --depth 1 "$url" "$dest"
    fi
    if ! git -C "$dest" cat-file -e "${want}^{commit}" >/dev/null 2>&1; then
      if [[ -d "$url" ]]; then
        git -C "$dest" fetch origin "$want"
      else
        git -C "$dest" fetch --depth 1 origin "$want"
      fi
    fi
    git -C "$dest" checkout --detach "$want"
  elif [[ -d "$url" ]]; then
    git clone --branch "$want" "$url" "$dest"
  else
    git clone --depth 1 --branch "$want" "$url" "$dest"
  fi
}

read_snapshot_sha() {
  local root="$1"
  if [[ -f "$root/SOURCE" ]]; then
    awk -F': ' '$1 == "sha" { print $2; exit }' "$root/SOURCE"
    return
  fi
  if [[ -f "$root/docs/map/generated/meta.json" ]]; then
    "$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("git_head") or "")' \
      "$root/docs/map/generated/meta.json"
  fi
}

clone_ref "$atlas_url" "$atlas_ref" "$work/atlas"
if [[ ! -d "$work/atlas/.git" && ! -f "$work/atlas/.git" ]]; then
  echo "error: atlas clone failed" >&2
  exit 1
fi

clone_ref "$src_url" "$ref" "$work/src"

src="$work/src"
atlas="$work/atlas"
src_sha="$(git -C "$src" rev-parse HEAD)"
atlas_sha="$(read_snapshot_sha "$atlas")"
src_hand=0
atlas_l3=0
[[ -f "$src/docs/map/ATLAS.md" ]] && src_hand=1
[[ -f "$atlas/docs/map/generated/symbols.tsv" ]] && atlas_l3=1

mode=""
if [[ "$src_sha" == "$atlas_sha" && -n "$atlas_sha" && "$atlas_l3" -eq 1 ]]; then
  if [[ "$src_hand" -eq 1 ]]; then
    rm -rf "$src/docs/map/generated"
    mkdir -p "$src/docs/map"
    cp -a "$atlas/docs/map/generated" "$src/docs/map/generated"
    mode="reuse-l3"
  else
    rm -rf "$src/docs/map"
    mkdir -p "$src/docs"
    cp -a "$atlas/docs/map" "$src/docs/map"
    mode="overlay-published"
  fi
else
  if [[ "$src_hand" -eq 0 && -d "$atlas/docs/map" ]]; then
    rm -rf "$src/docs/map"
    mkdir -p "$src/docs"
    cp -a "$atlas/docs/map" "$src/docs/map"
    mode="regen-published-hand"
  else
    mode="regen-l3"
  fi
  rm -rf "$src/docs/map/generated"
  "$PYTHON" "$TOOL_DIR/atlas.py" --root "$src" gen
fi

validate_status="skipped"
if [[ "$mode" == regen-l3 || "$mode" == regen-published-hand ]]; then
  set +e
  validate_out="$("$PYTHON" "$TOOL_DIR/atlas.py" --root "$src" validate 2>&1)"
  validate_rc=$?
  set -e
  if [[ "$validate_rc" -eq 0 ]]; then
    validate_status="ok"
  else
    validate_status="errors"
  fi
  printf '%s\n' "$validate_out" > "$work/validate.txt"
fi

protocol=""
if [[ -f "$atlas/agent-skill.md" ]]; then
  protocol="$atlas/agent-skill.md"
elif [[ -f "$TOOL_DIR/agent-skill.md" ]]; then
  cp "$TOOL_DIR/agent-skill.md" "$work/agent-skill.md"
  protocol="$work/agent-skill.md"
fi

cat > "$work/READY" <<EOF
mode: ${mode}
source: ${src}
source_sha: ${src_sha}
atlas_sha: ${atlas_sha}
ref: ${ref}
atlas: ${atlas}
map: ${src}/docs/map/ATLAS.md
protocol: ${protocol}
validate: ${validate_status}
push: no
EOF

cat "$work/READY"
