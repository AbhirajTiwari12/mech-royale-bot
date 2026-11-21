import sys, json, gzip, os, collections
sys.path.insert(0, 'tools')
import arena_sim as A
K, n = sys.argv[1], int(sys.argv[2])
hello = json.loads(gzip.open(K, 'rt').readline())
mod = A.load_mod('./bot.py', 'pb')
rows = []
for seed in range(1, n + 1):
    sim = A.Sim(hello, mod, seed)
    pl = sim.player
    marks = {}
    orig = sim.bot.act
    st = dict(sw=None, first_kill=None)
    def act(obs, orig=orig, sim=sim, marks=marks, st=st):
        t = obs["t"]
        for T in (40, 60, 90, 120, 170):
            if T not in marks and t >= T: marks[T] = obs["self"]["cubes"]
        a = orig(obs)
        if st["sw"] is None and sim.bot.econ_done: st["sw"] = (t, obs["self"]["cubes"], round(obs["self"]["hp"]), len(obs["self"].get("upgrades") or []))
        if st["first_kill"] is None and sim.bot.kills > 0: st["first_kill"] = t
        return a
    sim.bot.act = act
    r = sim.run()
    rows.append((r["score"], r["t"], marks, st["sw"], st["first_kill"], r["labels"]))
def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / max(1, len(xs))
print("score %.2f  death_t %.0f" % (avg([r[0] for r in rows]), avg([r[1] for r in rows])))
for T in (40, 60, 90, 120, 170):
    print("  cubes at %3d s: %.1f (alive in %d/%d)" % (T, avg([r[2].get(T) for r in rows]), sum(1 for r in rows if T in r[2]), n))
sw = [r[3] for r in rows if r[3]]
print("  reached phase 2 in %d/%d: at t=%.0f with %.1f cubes, hp %.0f, %.1f upgrades" % (len(sw), n, avg([s[0] for s in sw]), avg([s[1] for s in sw]), avg([s[2] for s in sw]), avg([s[3] for s in sw])))
print("  first kill at t=%.0f (in %d games)" % (avg([r[4] for r in rows]), sum(1 for r in rows if r[4] is not None)))
lab = collections.Counter()
for r in rows:
    for k, v in r[5].items(): lab[k] += v
print("  labels", dict(lab.most_common(8)))
