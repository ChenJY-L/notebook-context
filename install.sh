#!/usr/bin/env bash
# Install notebook-context as a Claude Code skill.
#
# Default target: ~/.claude/skills/notebook-context (user scope, all projects).
# Override with NOTEBOOK_CONTEXT_TARGET to install elsewhere, e.g. project scope:
#   NOTEBOOK_CONTEXT_TARGET="$PWD/.claude/skills/notebook-context" bash install.sh
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/ChenJY-L/notebook-context/main/install.sh | bash

set -euo pipefail

REPO_URL="${NOTEBOOK_CONTEXT_REPO_URL:-https://github.com/ChenJY-L/notebook-context.git}"
BRANCH="${NOTEBOOK_CONTEXT_BRANCH:-main}"
TARGET="${NOTEBOOK_CONTEXT_TARGET:-$HOME/.claude/skills/notebook-context}"

log()  { printf '[notebook-context] %s\n' "$*"; }
die()  { printf '[notebook-context] error: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git not found in PATH"

PYTHON_BIN=""
PY_VER=""
for candidate in python3 python py; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  # On Windows, python3/python may be Microsoft Store stubs that print nothing
  # and exit non-zero, so verify the candidate actually runs.
  ver=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
  if [ -n "$ver" ]; then
    PYTHON_BIN="$candidate"
    PY_VER="$ver"
    break
  fi
done

if [ -n "$PYTHON_BIN" ]; then
  PY_OK=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)' 2>/dev/null || echo 0)
  if [ "$PY_OK" != "1" ]; then
    log "warning: found Python $PY_VER ($PYTHON_BIN), but the skill scripts require Python >= 3.10."
    log "         the skill will install, but invocation will fail until a newer Python is on PATH."
  else
    log "python check ok ($PYTHON_BIN, $PY_VER)"
  fi
else
  log "warning: no working python found in PATH; install proceeds but the skill scripts will not run."
fi

if [ -d "$TARGET/.git" ]; then
  EXISTING_REMOTE=$(git -C "$TARGET" config --get remote.origin.url || true)
  log "updating existing install: $TARGET (remote: ${EXISTING_REMOTE:-<none>})"
  git -C "$TARGET" fetch --quiet origin "$BRANCH"
  git -C "$TARGET" checkout --quiet "$BRANCH"
  git -C "$TARGET" pull --quiet --ff-only origin "$BRANCH"
elif [ -e "$TARGET" ]; then
  die "target exists and is not a git checkout: $TARGET (remove it or set NOTEBOOK_CONTEXT_TARGET)"
else
  log "cloning $REPO_URL (branch $BRANCH) -> $TARGET"
  mkdir -p "$(dirname "$TARGET")"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$TARGET"
fi

[ -f "$TARGET/SKILL.md" ] || die "SKILL.md missing at $TARGET after install"

log "installed."
log "  skill dir: $TARGET"
log "  invoke:    in Claude Code, ask it to use the notebook-context skill on an .ipynb file."
log "  update:    re-run this script, or 'git -C \"$TARGET\" pull'."
