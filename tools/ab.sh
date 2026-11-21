#!/bin/sh
# usage: tools/ab.sh <seeds> <flags-json>...   (runs the working-tree bot with each flag set)
cd "$(dirname "$0")/.."
N=$1; shift
for f in "$@"; do
  printf '%s  ' "$f"
  MECH_FLAGS="$f" python3 tools/arena_sim.py . ../kit/mechfight-kit/check/stream.jsonl.gz --seeds $N --procs 4 --params "${SIMP:-{\}}" | head -1
done
