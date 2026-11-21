#!/bin/sh
# usage: tools/lobby.sh <seeds> '<name>:<flags-json>' '<name>:<flags-json>'   (2 test slots in a mostly-aggressive lobby)
cd "$(dirname "$0")/.."
N=$1; n1=${2%%:*}; f1=${2#*:}; n2=${3%%:*}; f2=${3#*:}
C='{"mode":"squad","focus":0,"dmgup":0,"cubegrab":0}'
D5='{"mode":"squad"}'
GL='{"mode":"squad","gl":1}'
D4='{"mode":"squad","retreat":1,"r1":0.25,"r2":0.25,"r3":0.25,"rexit":0.6}'
ZR='{"mode":"squad","zaprespect":1,"peek":1}'
V5='{"mode":"squad","retreat":1,"selective":1,"peek":1,"zaprespect":1,"chase":0,"focus":0,"dmgup":0,"cubegrab":0}'
A='{"mode":"squad","avoid":1,"selective":1,"retreat":1,"chase":0}'
HIST=${HIST:-} python3 tools/ffa_sim.py ../kit/mechfight-kit/check/stream.jsonl.gz --seeds $N --start ${START:-1} --procs 4 --lobby "[[\"C\",3,$C],[\"D5\",3,$D5],[\"GL\",2,$GL],[\"D4\",2,$D4],[\"ZR\",2,$ZR],[\"v5\",1,$V5],[\"A\",1,$A],[\"$n1\",1,$f1],[\"$n2\",1,$f2]]" | grep -E "^($n1|$n2|avg) |hist"
