#!/bin/sh
# Checks to run before pushing:  scripts/preflight.sh
# A git hook can check commit messages for you:  git config core.hooksPath .githooks
set -u
cd "$(dirname "$0")/.."
FAILED=0

step() {
    label=$1
    shift
    printf '%-66s ' "$label"
    if output=$("$@" 2>&1); then
        echo ok
    else
        echo FAILED
        echo "$output" | sed 's/^/    /'
        FAILED=1
    fi
}

step "python files compile" sh -c 'for f in install.py src/camera_overlay.py; do python3 -c "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], \"exec\")" "$f" || exit 1; done'
step "shell scripts parse" sh -c 'for f in scripts/*.sh .githooks/*; do sh -n "$f" || exit 1; done'
if command -v node >/dev/null 2>&1; then
    step "menu script has valid JavaScript syntax" node --check src/cam-overlay-menu.js
fi
step "no personal paths, addresses in the files" sh -c '! git grep -n -I -i -E "/home/[a-z]+/|@gmail\.com|192\.168\.[0-9]+\.[0-9]+" -- . ":(exclude)scripts/preflight.sh" ":(exclude).githooks"'

# Words that must never be published (names, hostnames, ...) are kept in a private list on the developer's machine, not in
# this repository, so that the list itself does not leak them. Without that file this check is skipped.
PRIVATE="${PRIVATE_PATTERNS:-$HOME/.config/private-patterns}"
if [ -f "$PRIVATE" ]; then
    step "private word list: none in the files to publish" sh -c "! git grep -n -I -i -E -f '$PRIVATE' -- ."
    base=$(git rev-parse --verify -q origin/main || git rev-list --max-parents=0 HEAD)
    step "private word list: none in the commit messages to push" sh -c "! git log '$base'..HEAD --format=%B | grep -i -E -f '$PRIVATE'"
fi
base=$(git rev-parse --verify -q origin/main || git rev-list --max-parents=0 HEAD)
step "commits to push: no-reply address and UTC timestamps" sh -c "git log '$base'..HEAD --format='%ae %ce %ad %cd' --date=iso | awk '{ if (\$1 !~ /@users\\.noreply\\.github\\.com\$/ || \$2 !~ /@users\\.noreply\\.github\\.com\$/ || \$5 != \"+0000\" || \$8 != \"+0000\") { print \"not allowed: \" \$0; bad=1 } } END { exit bad }'"

[ "$FAILED" = 0 ] && echo "all checks passed" || { echo "some checks failed"; exit 1; }
