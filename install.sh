#!/usr/bin/env bash
# Install Code Atlas CLI system-wide for the current user.
#
# Default layout (matches other local tools on this machine):
#   ~/.local/share/code-atlas/   # toolkit copy
#   ~/.local/bin/atlas           # launcher on PATH
#
# Usage:
#   ./install.sh
#   ./install.sh --prefix "$HOME/.local"
#   ./install.sh --link          # symlink to this checkout instead of copy
#   ./install.sh --uninstall

set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
MODE="copy" # copy | link
ACTION="install"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    --link)
      MODE="link"
      shift
      ;;
    --uninstall)
      ACTION="uninstall"
      shift
      ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
SHARE_DIR="$PREFIX/share/code-atlas"
BIN_DIR="$PREFIX/bin"
LAUNCHER="$BIN_DIR/atlas"

if [[ "$ACTION" == "uninstall" ]]; then
  rm -f "$LAUNCHER"
  rm -rf "$SHARE_DIR"
  GROK_SKILL_DST="${GROK_SKILLS_DIR:-$HOME/.grok/skills}/create-code-atlas"
  if [[ -d "$GROK_SKILL_DST" ]]; then
    rm -rf "$GROK_SKILL_DST"
    echo "removed Grok skill: $GROK_SKILL_DST"
  fi
  echo "uninstalled: $LAUNCHER"
  echo "removed:     $SHARE_DIR"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found on PATH" >&2
  exit 1
fi

mkdir -p "$BIN_DIR" "$PREFIX/share"

if [[ "$MODE" == "link" ]]; then
  rm -rf "$SHARE_DIR"
  ln -sfn "$SRC_DIR" "$SHARE_DIR"
  echo "linked toolkit: $SHARE_DIR -> $SRC_DIR"
else
  mkdir -p "$SHARE_DIR"
  # Refresh copy (keep it simple and deterministic)
  rsync -a --delete \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    --exclude 'tests/fixtures/sample_repo/docs/' \
    "$SRC_DIR/" "$SHARE_DIR/"
  chmod +x "$SHARE_DIR/atlas.py" "$SHARE_DIR/install.sh" \
    "$SHARE_DIR/ci/publish-atlas.sh" "$SHARE_DIR/sandbox/prepare.sh" 2>/dev/null || true
  echo "installed toolkit: $SHARE_DIR"
fi

# Launcher resolves repo root from cwd and delegates to atlas.py
cat > "$LAUNCHER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ATLAS_HOME="${ATLAS_HOME:-}"
if [[ -z "$ATLAS_HOME" ]]; then
  # Prefer the share install next to this launcher: ../share/code-atlas
  SELF="$(cd "$(dirname "$0")" && pwd)"
  CANDIDATE="$(cd "$SELF/../share/code-atlas" 2>/dev/null && pwd || true)"
  if [[ -n "${CANDIDATE:-}" && -f "$CANDIDATE/atlas.py" ]]; then
    ATLAS_HOME="$CANDIDATE"
  else
    echo "error: cannot find atlas.py (set ATLAS_HOME)" >&2
    exit 1
  fi
fi
exec python3 "$ATLAS_HOME/atlas.py" "$@"
EOF
chmod +x "$LAUNCHER"

echo "installed launcher: $LAUNCHER"

# Global Grok skill: /create-code-atlas
GROK_SKILL_SRC="$SRC_DIR/skills/create-code-atlas/SKILL.md"
# When installed via copy, skill lives under share after rsync
if [[ ! -f "$GROK_SKILL_SRC" && -f "$SHARE_DIR/skills/create-code-atlas/SKILL.md" ]]; then
  GROK_SKILL_SRC="$SHARE_DIR/skills/create-code-atlas/SKILL.md"
fi
GROK_SKILL_DST="${GROK_SKILLS_DIR:-$HOME/.grok/skills}/create-code-atlas"
if [[ -f "$GROK_SKILL_SRC" ]]; then
  mkdir -p "$GROK_SKILL_DST"
  cp "$GROK_SKILL_SRC" "$GROK_SKILL_DST/SKILL.md"
  echo "installed Grok skill: $GROK_SKILL_DST/SKILL.md"
  echo "  invoke: /create-code-atlas"
else
  echo "note: Grok skill source missing ($SRC_DIR/skills/create-code-atlas/SKILL.md)"
fi

# PATH hint
case ":$PATH:" in
  *":$BIN_DIR:"*)
    echo "PATH ok: $BIN_DIR already on PATH"
    ;;
  *)
    echo ""
    echo "Add to PATH in your shell startup file:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

echo ""
echo "Smoke:"
echo "  atlas --help"
echo "  cd /path/to/repo && atlas init"
echo "  atlas gen && atlas validate"
echo "  grok: /create-code-atlas"
