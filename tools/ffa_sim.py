"""Free-for-all self-play harness: 16 mechs, each driven by a variant of our bot (flag sets) or a house-style bot.

usage: python3 tools/ffa_sim.py <stream.jsonl.gz> --lobby '<json>' [--seeds N] [--start S] [--procs P]
  lobby json: list of [name, count, flags-dict]  (flags-dict null = house-style expert bot); counts must sum to 16.
Prints mean placement points (16 - place) per variant name.
"""
import gzip, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import arena_sim as A


class _Dummy:
    class Bot:
        def __init__(self, hello):
            pass


class FFASim(A.Sim):
    def __init__(self, hello, entries, seed, botmod):
        super().__init__(hello, _Dummy, seed)
        self.rng.shuffle(entries)
        for i, m in enumerate(self.mechs):
            name, flags = entries[i]
            m["name"] = name
            if flags is None:
                m["player"] = False
                m["tier"] = "expert"
                if "nav" not in m:
                    m["nav"] = A.NL.Bot(hello)
                    m["brain"] = dict(roam=None, roam_t=-99, strafe=1.0, strafe_until=0.0, noise=[0.0, 0.0], seen_t=-99.0)
                if m["weapons"][1] is None:
                    m["weapons"][1] = self.new_weapon(m, self.rng.choice(["mortar", "laser", "lightning"]))
            else:
                m["player"] = True
                m["weapons"][1] = None
                if "arch" in flags:
                    import archetypes
                    os.environ["MECH_FLAGS"] = "{}"
                    m["bot"] = archetypes.ARCH[flags["arch"]](dict(hello, slot=i, seed=seed * 100 + i))
                else:
                    os.environ["MECH_FLAGS"] = json.dumps(flags)
                    m["bot"] = botmod.Bot(dict(hello, slot=i, seed=seed * 100 + i))
                m["seen"] = {}
        os.environ.pop("MECH_FLAGS", None)
        self.bots = [m for m in self.mechs if m["player"]]
        self.housel = [m for m in self.mechs if not m["player"]]
        self.hunters = set(m["id"] for m in self.housel)
        self.known = None
        self.maxms = 0.0

    def enemies(self, m):
        if m["player"]:
            return [q for q in self.mechs if q["alive"] and q is not m]
        return [q for q in self.bots if q["alive"]]

    def obs_for(self, m, zc, zr, ph):
        self.player = m
        self.house = [q for q in self.mechs if q is not m]
        self.seen_t = m["seen"]
        return self.player_obs(zc, zr, ph)

    def squad_update(self):
        pass

    def run(self):
        dur = self.cfg["match"]["duration_s"]
        while self.t < dur:
            alive = [m for m in self.mechs if m["alive"]]
            if len(alive) <= 1:
                break
            zc, zr, ph = self.zone()
            if self.tick % 2 == 0:
                acts = []
                for m in self.bots:
                    if not m["alive"]:
                        continue
                    obs = self.obs_for(m, zc, zr, ph)
                    s = time.perf_counter()
                    try:
                        a = m["bot"].act(obs)
                    except Exception:
                        import traceback
                        traceback.print_exc()
                        a = {}
                    self.maxms = max(self.maxms, (time.perf_counter() - s) * 1000)
                    for k in ("move", "boost", "aim"):
                        if k not in a and k in m["cmd"]:
                            a[k] = m["cmd"][k]
                    acts.append((m, a))
                for m, a in acts:
                    m["cmd"] = a
                    self.post_action(m, a)
                for m in self.housel:
                    if m["alive"]:
                        tg = None
                        bd = 1e18
                        for q in self.bots:
                            if q["alive"]:
                                d = math.dist(q["pos"], m["pos"])
                                if d < bd:
                                    bd = d
                                    tg = q
                        if tg is None:
                            continue
                        self.player = tg
                        m["jumped"] = False
                        m["cmd"] = self.house_act(m, zc, zr, ph)
            for m in self.mechs:
                if m["alive"]:
                    self.step_mech(m, zc, zr, ph["dps"])
            self.step_projs()
            if self.cubes:
                for cid, c in list(self.cubes.items()):
                    best = None
                    bd = self.cfg["cubes"]["pickup_radius"]
                    for m in self.mechs:
                        if m["alive"]:
                            dd = math.hypot(m["pos"][0] - c["pos"][0], m["pos"][1] - c["pos"][1])
                            if dd < bd and abs(m["pos"][2] - c["pos"][2]) < 4:
                                bd = dd
                                best = m
                    if best is not None:
                        del self.cubes[cid]
                        old = self.max_hp(best)
                        best["cubes"] += 1
                        best["hp"] += self.max_hp(best) - old
                        if best["cubes"] in self.cfg["cubes"]["upgrade_thresholds"]:
                            best["offers"].append(1)
                        if best["player"]:
                            best["events"].append(dict(type="pickup", cubes=best["cubes"]))
            self.tick += 1
            self.t = self.tick * A.DT
        # survivors ranked by HP
        surv = sorted([m for m in self.mechs if m["alive"]], key=lambda m: -m["hp"])
        for i, m in enumerate(surv):
            m["place"] = i + 1
        return [(m["name"], 16 - m["place"], m["kills"], getattr(m.get("bot"), "mode", "")) for m in self.mechs], round(self.t, 1), self.maxms


def run_one(args):
    stream, lobby, seed = args
    hello = json.loads(gzip.open(stream, "rt").readline())
    mod = A.load_mod(os.path.join(A.ROOT, "bot.py"), "ffabot_%d" % seed)
    entries = []
    for name, cnt, flags in lobby:
        entries += [(name, flags)] * cnt
    assert len(entries) == 16, len(entries)
    sim = FFASim(hello, entries, seed, mod)
    return sim.run()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("stream")
    ap.add_argument("--lobby", required=True)
    ap.add_argument("--seeds", type=int, default=16)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    lobby = json.loads(a.lobby)
    jobs = [(a.stream, lobby, s) for s in range(a.start, a.start + a.seeds)]
    t0 = time.time()
    if a.procs <= 1:
        res = [run_one(j) for j in jobs]
    else:
        import multiprocessing as mp
        with mp.Pool(min(4, a.procs)) as p:
            res = p.map(run_one, jobs, chunksize=1)
    agg = {}
    modes = {}
    for r, t, ms in res:
        for name, pts, kills, mode in r:
            agg.setdefault(name, []).append((pts, kills))
            if mode:
                modes.setdefault(name, {}).setdefault(mode, 0)
                modes[name][mode] += 1
    for name, v in sorted(agg.items(), key=lambda kv: -sum(p for p, k in kv[1]) / len(kv[1])):
        pts = [p for p, k in v]
        mean = sum(pts) / len(pts)
        sd = (sum((p - mean) ** 2 for p in pts) / max(1, len(pts) - 1)) ** 0.5
        if os.environ.get("HIST"):
            h = [0] * 16
            for p in pts: h[int(p)] += 1
            print("   hist(pts 0..15):", h)
        print("%-10s pts %.2f +- %.2f  kills %.2f  wins %.0f%%  (n=%d) %s" % (name, mean, sd / len(pts) ** 0.5, sum(k for p, k in v) / len(v), 100.0 * sum(1 for p in pts if p == 15) / len(pts), len(pts), modes.get(name, "")))
    print("avg match %.0fs  maxms %.1f  [%.0fs wall]" % (sum(t for r, t, ms in res) / len(res), max(ms for r, t, ms in res), time.time() - t0))


if __name__ == "__main__":
    main()
