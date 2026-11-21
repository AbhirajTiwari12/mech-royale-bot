import sys, json, gzip, collections, math, os
sys.path.insert(0, 'tools')
import arena_sim as A, ffa_sim as Fs
K, n = sys.argv[1], int(sys.argv[2])
flags = json.loads(sys.argv[3])
hello = json.loads(gzip.open(K, 'rt').readline())
mod = A.load_mod('./bot.py', 'pb')
V5 = {"mode": "squad", "retreat": 1, "selective": 1, "peek": 1, "zaprespect": 1, "chase": 0, "focus": 0, "dmgup": 0, "cubegrab": 0}
rows = []
for seed in range(1, n + 1):
    entries = [("C", {"mode": "squad"})] * 8 + [("v5", V5)] * 7 + [("X", flags)]
    sim = Fs.FFASim(hello, entries, seed, mod)
    me = [m for m in sim.mechs if m["name"] == "X"][0]
    info = {}
    ok = sim.kill
    def kill(m, by, ok=ok, sim=sim, me=me, info=info):
        if m is me:
            near = sum(1 for q in sim.mechs if q["alive"] and q is not me and math.dist(q["pos"], me["pos"]) < 110)
            lab = (me["cmd"].get("debug") or {}).get("label", "?")
            info.update(t=round(sim.t), by=(by["name"] if by else "zone"), near=near, lab=lab.split()[0], w=[w["kind"] for w in me["weapons"] if w], cubes=me["cubes"], bycubes=(by["cubes"] if by else 0), alive=sum(1 for q in sim.mechs if q["alive"]))
        ok(m, by)
    sim.kill = kill
    labs = collections.Counter()
    r, t, ms = sim.run()
    pts = [x for x in r if x[0] == "X"][0][1]
    info["pts"] = pts
    rows.append(info)
rows.sort(key=lambda r: r["pts"])
for r in rows: print(r)
print("mean", sum(r["pts"] for r in rows) / len(rows))
