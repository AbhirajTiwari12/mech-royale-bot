#!/bin/sh
# usage: tools/variant.sh <name> '<flags-json>'  -> commits HEAD's tree with flags.json replaced, pushes to branch exp/<name>, prints SHA
set -e
cd "$(dirname "$0")/.."
name=$1; flags=$2
tmp=$(mktemp)
printf '%s\n' "$flags" > "$tmp"
blob=$(git hash-object -w "$tmp")
export GIT_INDEX_FILE=$(mktemp -u)
git read-tree HEAD
git update-index --add --cacheinfo 100644,$blob,flags.json
tree=$(git write-tree)
rm -f "$GIT_INDEX_FILE"; unset GIT_INDEX_FILE
sha=$(git commit-tree "$tree" -p HEAD -m "variant $name: $flags

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
git push -q -f origin "$sha:refs/heads/exp/$name" 2>&1 | grep -v '^remote:' || true
echo "$sha"
