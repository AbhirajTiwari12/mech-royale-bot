"""Robustness audit of bot.py: exceptions, worst-case CPU per tick, memory growth, malformed observations.

usage: python3 tools/audit.py <stream.jsonl.gz> [matches]
"""
import gc, gzip, json, math, os, random, sys, time, traceback, collections, copy, tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import arena_sim as A
import ffa_sim as Fs

K = sys.argv[1]
NM = int(sys.argv[2]) if len(sys.argv) > 2 else 40
hello = json.loads(gzip.open(K, "rt").readline())
mod = A.load_mod(os.path.join(A.ROOT, "bot.py"), "auditbot")
gc.disable()

LOBBY = [("auto", {}), ("auto", {}), ("auto", {}), ("auto", {}), ("ffa_roof", {"mode": "ffa"}), ("ffa_den", {"mode": "ffa", "roof": 0, "den": 1}),
         ("ffa_ramp", {"mode": "ffa", "ramp_t": 30, "ramp_alive": 5}), ("ffa_flee", {"mode": "ffa", "flee_t": 60, "armfirst": 120}),
         ("squad", {"mode": "squad"}), ("squad_s3", {"mode": "squad", "drops": 1, "econ_t": 75, "near_r": 100}),
         ("squad_c", {"mode": "squad", "focus": 0, "dmgup": 0, "cubegrab": 0, "gl": 0}), ("v5", {"mode": "squad", "retreat": 1, "selective": 1, "peek": 1, "zaprespect": 1, "chase": 0}),
         ("avoid", {"mode": "squad", "avoid": 1, "selective": 1, "retreat": 1, "chase": 0}), ("farm", {"mode": "squad", "farm_until": 90, "selective": 1, "retreat": 1}),
         ("roofsquad", {"mode": "squad", "roof": 1, "retreat": 1, "selective": 1}), ("kite", {"mode": "squad", "kite": 35, "retreat": 1, "rearpref": 1})]

exc = collections.Counter()
times = []
ticks = 0
recorded = []
rng = random.Random(7)


def wrap(bot, name):
    orig = bot.act

    def act(obs):
        global ticks
        ticks += 1
        if rng.random() < 0.004:
            recorded.append(copy.deepcopy(obs))
        c0 = time.process_time()
        try:
            a = orig(obs)
            json.dumps(a)
            assert a.get("tick") == obs["tick"]
            mv = a.get("move", [0, 0])
            assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in mv), mv
            if "aim" in a:
                assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in a["aim"]), a["aim"]
        except Exception:
            tb = traceback.format_exc().strip().splitlines()
            exc[name + " | " + " / ".join(tb[-3:])[:300]] += 1
            a = {"tick": obs["tick"]}
        times.append(time.process_time() - c0)
        return a
    bot.act = act


t0 = time.time()
longest = 0
for seed in range(1, NM + 1):
    sim = Fs.FFASim(hello, list(LOBBY), 1000 + seed, mod)
    for m in sim.bots:
        wrap(m["bot"], m["name"])
    r, t, ms = sim.run()
    longest = max(longest, t)
times.sort()
n = len(times)
print("PART 1  mixed lobbies: %d matches, %d bot-ticks, longest match %.0f s, wall %.0f s" % (NM, ticks, longest, time.time() - t0))
print("  exceptions: %d" % sum(exc.values()))
for k, v in exc.most_common(8):
    print("   %5d  %s" % (v, k))
print("  CPU per tick ms: p50 %.3f  p99 %.3f  p99.9 %.3f  max %.3f   (>5 ms: %d, >10 ms: %d, >16 ms: %d)" % (
    times[n // 2] * 1e3, times[int(n * 0.99)] * 1e3, times[int(n * 0.999)] * 1e3, times[-1] * 1e3,
    sum(1 for x in times if x > 0.005), sum(1 for x in times if x > 0.010), sum(1 for x in times if x > 0.016)))

# ---------------------------------------------------------------- PART 2: malformed / unexpected observations
def mutate(o, r):
    o = copy.deepcopy(o)
    k = r.randrange(16)
    me = o.get("self", {})
    x, y, z = me.get("pos", [0, 0, 0])
    if k == 0:
        for key in r.sample(list(o.keys()), 2):
            if key not in ("tick", "type", "self"):
                o.pop(key, None)
    elif k == 1:
        for key in r.sample(list(me.keys()), 2):
            if key != "pos":
                me.pop(key, None)
    elif k == 2:
        for key in ("mechs", "projectiles", "crates", "cubes", "pickups", "sounds", "beams", "events", "drops", "offer"):
            if r.random() < 0.4:
                o[key] = None
    elif k == 3:
        o["mechs"] = (o.get("mechs") or []) + [{"id": 100 + i, "kind": "npc", "pos": [x + r.uniform(-60, 60), y + r.uniform(-60, 60), z], "vel": [1, 0, 0], "aim": [0, 0], "hp": 350, "max_hp": 350} for i in range(4)]
    elif k == 4:
        o["drops"] = [{"id": 20000, "pos": [x + r.uniform(-80, 80), y + r.uniform(-80, 80), z], "radius": 8, "lands_in": r.choice([0, 3.5, 20]), "capture_s": 4, "progress": 0, "holder": None}]
    elif k == 5:
        o["sounds"] = [{"kind": r.choice(["gun", "laser", "step", "death", "weird"]), "bearing": r.uniform(-3, 3), "dist": r.choice(["near", "mid", "far"]), "age": 0.1} for _ in range(6)]
        o["beams"] = [{"by": 5, "kind": "laser", "path": [[x, y, z], [x + 10, y, z]], "age": 0.1}]
    elif k == 6:
        o["projectiles"] = [{"id": i, "kind": r.choice(["gun", "mortar", "npc", "plasma"]), "pos": [x + r.uniform(-50, 50), y + r.uniform(-50, 50), z + r.uniform(0, 30)], "vel": [r.uniform(-250, 250), r.uniform(-250, 250), r.uniform(-30, 30)]} for i in range(80)]
    elif k == 7:
        o["pickups"] = [{"id": 8000 + i, "kind": r.choice(["laser", "mortar", "lightning", "gun", "railgun"]), "pos": [x + r.uniform(-90, 90), y + r.uniform(-90, 90), z + r.uniform(-2, 25)]} for i in range(40)]
        o["cubes"] = [{"id": 5000 + i, "pos": [x + r.uniform(-40, 40), y + r.uniform(-40, 40), z]} for i in range(30)]
        o["crates"] = [{"id": 1000 + i, "pos": [x + r.uniform(-90, 90), y + r.uniform(-90, 90), z], "hp": 300} for i in range(36)]
    elif k == 8:
        for m in o.get("mechs") or []:
            for key in r.sample(list(m.keys()), min(2, len(m))):
                if key not in ("id", "pos"):
                    m.pop(key, None)
    elif k == 9:
        me["weapons"] = r.choice([[None, None], [], [{"kind": "railgun", "cooldown": 0, "stats": {}}, None], [(me.get("weapons") or [None])[0]], None])
    elif k == 10:
        o["offer"] = r.choice([{"id": 1, "expires": 9, "choices": []}, {"id": 1, "choices": [{"id": "new_thing"}, {"id": "armor"}]}, {"id": 2}])
    elif k == 11:
        o["events"] = [{"type": r.choice(["damaged", "hit", "effect", "drop", "kill", "pickup", "upgrade", "took", "dropped", "take_failed", "novel"])} for _ in range(5)] + [{"type": "damaged", "amount": 50, "weapon": "zone", "bearing": None, "by": None}]
    elif k == 12:
        o["zone"] = r.choice([{}, {"center": [0, 0], "radius": 0, "next_center": [0, 0], "next_radius": 0, "shrink_start": 0, "shrink_end": 0, "dps": 260}, None])
    elif k == 13:
        me["pos"] = [r.choice([-5000.0, 5000.0, x]), r.choice([-5000.0, 5000.0, y]), r.choice([z, 80.0, -30.0])]
        me["grounded"] = False
    elif k == 14:
        o["mechs"] = [{"id": i, "kind": "mech", "pos": [x + r.uniform(-150, 150), y + r.uniform(-150, 150), z + r.uniform(-3, 20)], "vel": [r.uniform(-19, 19), r.uniform(-19, 19), r.uniform(-9, 9)], "aim": [r.uniform(-3, 3), 0], "hp": r.uniform(1, 3000), "max_hp": 1400, "cubes": r.randrange(40), "weapons": r.sample(["gun", "laser", "mortar", "lightning"], 2), "charging": r.random() < 0.3} for i in range(15)]
    else:
        me["hp"] = r.choice([0, 1, me.get("hp", 1)])
        me["max_hp"] = r.choice([0, me.get("max_hp", 1400)])
        me["energy"] = 0
        me["stats"] = {}
    return o


exc2 = collections.Counter()
t2 = []
nf = 0
r = random.Random(11)
flagsets = [{}, {"mode": "ffa"}, {"mode": "squad", "drops": 1, "econ_t": 75}, {"mode": "ffa", "roof": 0, "den": 1, "flee_t": 60}, {"mode": "squad", "roof": 1, "retreat": 1}]
for fs in flagsets:
    os.environ["MECH_FLAGS"] = json.dumps(fs)
    bot = mod.Bot(dict(hello, slot=1, seed=5))
    for rep in range(3):
        for o in recorded:
            try:
                muts = (o, mutate(o, r), mutate(mutate(o, r), r))
            except Exception:
                muts = (o,)
            for o2 in muts:
                nf += 1
                c0 = time.process_time()
                try:
                    a = bot.act(o2)
                    json.dumps(a)
                except Exception:
                    tb = traceback.format_exc().strip().splitlines()
                    exc2[" / ".join(tb[-3:])[:300]] += 1
                t2.append(time.process_time() - c0)
os.environ.pop("MECH_FLAGS", None)
t2.sort()
print("PART 2  malformed/unexpected observations: %d fed (%d recorded real obs x mutations x %d flag sets)" % (nf, len(recorded), len(flagsets)))
print("  exceptions inside act(): %d (%.2f%%)" % (sum(exc2.values()), 100.0 * sum(exc2.values()) / max(1, nf)))
for k, v in exc2.most_common(12):
    print("   %5d  %s" % (v, k))
print("  CPU per tick ms: p99 %.3f  max %.3f" % (t2[int(len(t2) * 0.99)] * 1e3, t2[-1] * 1e3))

# ---------------------------------------------------------------- PART 3: memory growth, gc disabled, long run
tracemalloc.start()
os.environ["MECH_FLAGS"] = "{}"
bot = mod.Bot(dict(hello, slot=1, seed=5))
snap0 = None
tick = 0
for rep in range(40):
    for o in recorded:
        o = copy.deepcopy(o)
        tick += 2
        o["tick"] = tick
        o["t"] = tick * 0.05
        try:
            bot.act(o)
        except Exception:
            pass
    if rep == 4:
        snap0 = tracemalloc.get_traced_memory()[0]
        n0 = tick
cur = tracemalloc.get_traced_memory()[0]
print("PART 3  memory with gc disabled: %d ticks (= %.0f match-seconds); traced growth after warm-up %.1f KB  (%.2f KB per 1000 ticks)" % (
    tick // 2, tick * 0.05, (cur - snap0) / 1024.0, (cur - snap0) / 1024.0 / max(1, (tick - n0) / 2000.0)))
c0 = time.process_time()
gc.collect()
print("  one full gc.collect() costs %.2f ms; unreachable cycles found: see growth above" % ((time.process_time() - c0) * 1e3))
