#!/usr/bin/env bash
# Install this repository's git hooks into .git/hooks.
#
# Hooks are per-clone and cannot be committed, so a hook that is only documented
# in a comment is a hook that is installed on nobody's machine. That was the
# state of the secrets scanner: the file existed, the README-style install line
# sat in its header, and `ls .git/hooks` showed only the stock samples. The
# control was real in the repository and absent from every working copy.
#
# Run once per clone:  scripts/install-git-hooks.sh
# Uninstall:           rm .git/hooks/pre-commit

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$(git rev-parse --git-path hooks)"   # honours worktrees and GIT_DIR

mkdir -p "$HOOKS_DIR"

install_hook() {
    local name="$1" source="$2" target="$HOOKS_DIR/$1"

    if [ -e "$target" ] && ! grep -q "scan_staged_secrets" "$target" 2>/dev/null; then
        # Somebody else's hook, or a customised one. Overwriting it silently
        # would be the same class of mistake this script exists to fix.
        echo "⚠️  $target already exists and is not ours -- leaving it alone."
        echo "   Merge this line into it by hand:"
        echo "     exec \"\$(git rev-parse --show-toplevel)/$source\""
        return 1
    fi

    printf '#!/usr/bin/env bash\nexec "$(git rev-parse --show-toplevel)/%s"\n' \
        "$source" > "$target"
    chmod +x "$target"
    echo "✅ installed $name -> $source"
}

install_hook pre-commit scripts/pre-commit-secrets-scan.sh

echo
echo "Verify it is live:"
echo "  cd \"$REPO_ROOT\" && git commit --allow-empty -m 'hook check'"
echo "The scan runs before the commit is created; --no-verify skips it."
