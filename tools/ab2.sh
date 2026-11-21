#!/bin/sh
# usage: tools/ab2.sh <seeds> <flags-json>...  : both sim worlds (A passive bystanders, B provoked bystanders)
cd "$(dirname "$0")/.."
N=$1; shift
for f in "$@"; do
  a=$(MECH_FLAGS="$f" python3 tools/arena_sim.py . ../kit/mechfight-kit/check/stream.jsonl.gz --seeds $N --start ${START:-1} --procs 4 --params '{"provoke":0,"bystander_range":0,"noise_mult":1.5}' | head -1 | cut -c1-48)
  b=$(MECH_FLAGS="$f" python3 tools/arena_sim.py . ../kit/mechfight-kit/check/stream.jsonl.gz --seeds $N --start ${START:-1} --procs 4 --params '{"noise_mult":4}' | head -1 | cut -c1-48)
  echo "$f | A: $a | B: $b"
done
