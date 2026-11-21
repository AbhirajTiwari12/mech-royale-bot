"""Local match simulator for the practice scenario (1 player bot vs a 15-mech house squad).

Built from protocol.md. The physics/weapons follow the published numbers; the house-squad AI is a GUESS
(tiers easy/medium/hard/expert, shared vision, weakest-first hunters, one early and more later) tuned so
that known versions score roughly what the leaderboard gave them.

usage: python3 tools/arena_sim.py <bot_dir> <hello_stream.jsonl.gz> [--seeds N] [--start S] [--procs P] [--verbose]
"""
import gzip, json, math, os, random, sys, time, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NL = load_mod(os.path.join(ROOT, "bot.py"), "navlib")  # nav + helper for house bots
angdiff = NL.angdiff
norm2 = NL.norm2
DT = 0.05
TIERS = ["easy"] * 4 + ["medium"] * 4 + ["hard"] * 4 + ["expert"] * 3
# aim noise (rad), leads shots, strafes, P(second weapon), reaction delay s, can climb
TIER = {
    "easy":   dict(noise=0.050, lead=False, strafe=0.0, w2=0.35, react=0.6, climb=False, rate=0.6),
    "medium": dict(noise=0.028, lead=True,  strafe=0.5, w2=0.6,  react=0.4, climb=True,  rate=0.8),
    "hard":   dict(noise=0.016, lead=True,  strafe=1.0, w2=0.8,  react=0.25, climb=True, rate=1.0),
    "expert": dict(noise=0.009, lead=True,  strafe=1.0, w2=1.0,  react=0.15, climb=True, rate=1.0),
}
PARAMS = dict(noise_mult=1.5, hunters_per_dead=0.5, hunters_per_s=1.0 / 120.0, all_at=300.0, bystander_range=40.0, provoke=1)


class Sim:
    def __init__(self, hello, botmod, seed, verbose=False):
        self.rng = random.Random(seed)
        self.cfg = cfg = hello["config"]
        self.hello = hello
        self.verbose = verbose
        self.nav = NL.Nav(os.path.join(ROOT, "nav.bin"))
        self.mcfg = cfg["mech"]
        self.wcfg = cfg["weapons"]
        self.t = 0.0
        self.tick = 0
        rng = self.rng
        nav = self.nav
        # zone schedule
        zc = list(cfg["zone"]["start_center"])
        zr = cfg["zone"]["start_radius"]
        self.phases = []
        t0 = 0.0
        c, r = zc, zr
        for ph in cfg["zone"]["phases"]:
            for _ in range(50):
                a = rng.random() * 6.283
                rr = (r - ph["radius"]) * math.sqrt(rng.random())
                nc = [c[0] + math.cos(a) * rr, c[1] + math.sin(a) * rr]
                if nav.minX + 40 < nc[0] < nav.minX + nav.nnx * 3 - 40 and nav.minY + 40 < nc[1] < nav.minY + nav.nny * 3 - 40:
                    break
            self.phases.append(dict(c=c, r=r, nc=nc, nr=ph["radius"], ss=t0 + ph["hold_s"], se=t0 + ph["hold_s"] + ph["shrink_s"], dps=ph["dps"]))
            t0 += ph["hold_s"] + ph["shrink_s"]
            c, r = nc, ph["radius"]
        self.pi = 0
        # mechs
        self.mechs = []
        spawns = []
        while len(spawns) < 16:
            p = self.rand_cell(zc, zr * 0.8, 1)
            if all(math.dist(p[:2], q[:2]) > 130 for q in spawns):
                spawns.append(p)
        pslot = rng.randrange(16)
        tiers = list(TIERS)
        rng.shuffle(tiers)
        for i in range(16):
            m = dict(id=i, pos=spawns[i], vel=[0.0, 0.0, 0.0], aim=[rng.random() * 6.28 - 3.14, 0.0], cmd={}, hp=float(self.mcfg["hp"]),
                     energy=100.0, grounded=True, cubes=0, upgrades=[], stats=dict(self.mcfg), wstats={k: dict(v) for k, v in self.wcfg.items()},
                     alive=True, last_dmg=-99.0, last_spend=-99.0, events=[], short_until=-1.0, offers=[], offer=None, noffer=0, kills=0)
            m["weapons"] = [self.new_weapon(m, "gun"), None]
            if i == pslot:
                m["player"] = True
            else:
                m["player"] = False
                m["tier"] = tiers.pop()
                T = TIER[m["tier"]]
                if rng.random() < T["w2"]:
                    m["weapons"][1] = self.new_weapon(m, rng.choice(["mortar", "laser", "lightning"]))
                m["nav"] = NL.Bot(hello)
                m["brain"] = dict(roam=None, roam_t=-99, strafe=1.0, strafe_until=0.0, noise=[0.0, 0.0], seen_t=-99.0)
            self.mechs.append(m)
        self.player = self.mechs[pslot]
        self.house = [m for m in self.mechs if not m["player"]]
        self.known = None  # (pos, vel, t)
        self.hunters = set()
        # loot
        self.pickups = {}
        self.crates = {}
        self.cubes = {}
        self.nid = dict(p=8000, c=1000, q=5000, pr=1)
        lc = cfg["loot"]
        for i in range(lc["floor_count"]):
            roof = i < lc["floor_count"] * lc["floor_roof_fraction"]
            self.add_pickup(rng.choice(["mortar", "laser", "lightning"]), self.rand_cell(zc, zr, 2 if roof else 1))
        for i in range(cfg["crates"]["count"]):
            roof = i < cfg["crates"]["count"] * cfg["crates"]["roof_fraction"]
            cid = self.nid["c"]; self.nid["c"] += 1
            self.crates[cid] = dict(id=cid, pos=self.rand_cell(zc, zr, 2 if roof else 1), hp=float(cfg["crates"]["hp"]))
        for i in range(cfg["npc"]["count"]):
            self.crates[100 + i] = dict(id=100 + i, pos=self.rand_cell(zc, zr, 1), hp=float(cfg["npc"]["hp"]), npc=True)
        self.projs = []
        self.seen_t = {}
        self.deaths = 0
        self.place = None
        self.bot = botmod.Bot(dict(hello, slot=pslot, seed=seed))
        self.maxms = 0.0
        self.labels = {}

    # ------------------------------------------------------------ helpers
    def rand_cell(self, c, rad, val):
        nav = self.nav
        while True:
            a = self.rng.random() * 6.283
            d = rad * math.sqrt(self.rng.random())
            x, y = c[0] + math.cos(a) * d, c[1] + math.sin(a) * d
            if nav.val(x, y) == val:
                return [x, y, nav.surf_at(x, y)]

    def new_weapon(self, m, kind):
        st = m["wstats"][kind]
        w = dict(kind=kind, cooldown=0.0, stats=st, equip=0.0)
        if kind == "gun":
            w["ammo"] = int(st["mag"]); w["reload"] = 0.0
        if kind == "laser":
            w["charge"] = 0.0
        return w

    def add_pickup(self, kind, pos):
        pid = self.nid["p"]; self.nid["p"] += 1
        self.pickups[pid] = dict(id=pid, kind=kind, pos=list(pos))

    def add_cube(self, pos):
        cid = self.nid["q"]; self.nid["q"] += 1
        a = self.rng.random() * 6.28
        self.cubes[cid] = dict(id=cid, pos=[pos[0] + math.cos(a) * 1.5, pos[1] + math.sin(a) * 1.5, pos[2]])

    def zone(self):
        while self.pi < len(self.phases) - 1 and self.t >= self.phases[self.pi]["se"]:
            self.pi += 1
        ph = self.phases[self.pi]
        f = min(1.0, max(0.0, (self.t - ph["ss"]) / (ph["se"] - ph["ss"])))
        c = [ph["c"][0] + (ph["nc"][0] - ph["c"][0]) * f, ph["c"][1] + (ph["nc"][1] - ph["c"][1]) * f]
        return c, ph["r"] + (ph["nr"] - ph["r"]) * f, ph

    def max_hp(self, m):
        return m["stats"]["hp"] * (1 + self.cfg["cubes"]["hp_per_cube"] * min(m["cubes"], self.cfg["cubes"]["stat_cap"]))

    def dscale(self, m):
        return 1 + self.cfg["cubes"]["damage_per_cube"] * min(m["cubes"], self.cfg["cubes"]["stat_cap"])

    def sees(self, o, p, top=9.0, mid=4.5):
        ox, oy, oz = o["pos"]
        dx, dy = p[0] - ox, p[1] - oy
        d = math.hypot(dx, dy)
        st = o["stats"]
        if d > self.cfg["vision"]["range"] + st.get("vision_bonus", 0):
            return False
        if d > self.cfg["vision"]["near_range"] + st.get("near_range_bonus", 0):
            if abs(angdiff(math.atan2(dy, dx), o["aim"][0])) > math.radians(self.cfg["vision"]["fov_deg"]) / 2:
                return False
        nav = self.nav
        return nav.los(ox, oy, oz + 8, p[0], p[1], p[2] + mid) or nav.los(ox, oy, oz + 8, p[0], p[1], p[2] + top)

    # ------------------------------------------------------------ damage
    def damage(self, tgt, amount, src, by, weapon, effect=None):
        if not tgt["alive"]:
            return
        rear = False
        if src is not None:
            b = math.atan2(src[1] - tgt["pos"][1], src[0] - tgt["pos"][0])
            if abs(angdiff(b, tgt["aim"][0])) > math.radians(self.mcfg["front_arc_deg"]) / 2:
                rear = True
                amount *= self.mcfg["rear_mult"]
        if weapon != "zone":
            amount *= max(0.0, 1 - tgt["stats"].get("armor", 0))
        tgt["hp"] -= amount
        tgt["last_dmg"] = self.t
        if by is not None and not tgt["player"]:
            tgt["brain"]["hit_t"] = self.t
        if by is not None:
            by["last_dmg"] = self.t
            if by["player"]:
                by["events"].append(dict(type="hit", target=tgt["id"], amount=round(amount, 1), weapon=weapon))
        if tgt["player"]:
            ev = dict(type="damaged", amount=round(amount, 1), weapon=weapon, bearing=(math.atan2(src[1] - tgt["pos"][1], src[0] - tgt["pos"][0]) if src is not None else None), by=(by["id"] if by else None))
            if rear:
                ev["rear"] = True
            tgt["events"].append(ev)
        if tgt["hp"] <= 0:
            self.kill(tgt, by)

    def kill(self, m, by):
        m["alive"] = False
        alive = sum(1 for q in self.mechs if q["alive"])
        m["place"] = alive + 1
        if by is not None and by["player"]:
            by["events"].append(dict(type="kill", target=m["id"]))
            by["kills"] += 1
        for w in m["weapons"]:
            if w and w["kind"] != "gun":
                self.add_pickup(w["kind"], m["pos"])
        for _ in range(m["cubes"] + self.cfg["cubes"]["death_drop_extra"]):
            self.add_cube(m["pos"])
        if self.verbose:
            print("  t=%.0f %s dies (%s) alive=%d" % (self.t, "PLAYER" if m["player"] else m["tier"], "by %s" % by["id"] if by else "zone", alive))

    def hit_crate(self, c, amount, by):
        c["hp"] -= amount
        if by is not None and by["player"]:
            by["events"].append(dict(type="hit", target=c["id"], amount=amount, weapon="gun"))
            by["last_dmg"] = self.t
        if c["hp"] <= 0 and c["id"] in self.crates and c.get("npc"):
            del self.crates[c["id"]]
            for _ in range(self.cfg["npc"]["cubes"]):
                self.add_cube(c["pos"])
            return
        if c["hp"] <= 0 and c["id"] in self.crates:
            del self.crates[c["id"]]
            self.add_cube(c["pos"])
            if self.rng.random() < self.cfg["loot"]["crate_weapon_chance"]:
                self.add_pickup(self.rng.choice(["mortar", "laser", "lightning"]), c["pos"])

    def enemies(self, m):
        if m["player"]:
            return [q for q in self.house if q["alive"]]
        return [self.player] if self.player["alive"] else []

    # ------------------------------------------------------------ weapons
    def muzzle(self, m, h=7.0):
        yaw, pitch = m["aim"]
        d = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
        o = [m["pos"][0] + d[0] * 3.0, m["pos"][1] + d[1] * 3.0, m["pos"][2] + h + d[2] * 3.0]
        return o, d

    def ray_hit(self, m, o, d, rng_):
        """First enemy mech / crate along a ray; returns (dist, obj, is_crate)."""
        best = (rng_, None, False)
        targets = [(q, 2.5, 9.0, False) for q in self.enemies(m)]
        if m["player"]:
            targets += [(c, 1.4, 2.4, True) for c in self.crates.values() if abs(c["pos"][0] - o[0]) < rng_ and abs(c["pos"][1] - o[1]) < rng_]
        hl = math.hypot(d[0], d[1])
        for q, rad, h, isc in targets:
            p = q["pos"]
            if hl < 1e-6:
                continue
            s = ((p[0] - o[0]) * d[0] + (p[1] - o[1]) * d[1]) / (hl * hl)
            if s <= 0 or s >= best[0]:
                continue
            cx, cy = o[0] + d[0] * s, o[1] + d[1] * s
            if (cx - p[0]) ** 2 + (cy - p[1]) ** 2 > rad * rad:
                continue
            z = o[2] + d[2] * s
            if z < p[2] - 0.3 or z > p[2] + h + 0.3:
                continue
            best = (s, q, isc)
        if best[1] is not None:
            e = [o[0] + d[0] * best[0], o[1] + d[1] * best[0], o[2] + d[2] * best[0]]
            if not self.nav.los(o[0], o[1], o[2], e[0], e[1], e[2]):
                return (rng_, None, False)
        return best

    def fire_weapons(self, m, fire):
        t = self.t
        for i, w in enumerate(m["weapons"]):
            if not w:
                continue
            w["cooldown"] = max(0.0, w["cooldown"] - DT)
            w["equip"] = max(0.0, w["equip"] - DT)
            trig = bool(fire[i]) if i < len(fire) else False
            st = w["stats"]
            k = w["kind"]
            if k == "gun":
                if w["reload"] > 0:
                    w["reload"] -= DT
                    if w["reload"] <= 0:
                        w["ammo"] = int(st["mag"])
                elif m["cmd"].get("reload") and w["ammo"] < st["mag"]:
                    w["reload"] = st["reload"]
                elif trig and w["cooldown"] <= 0 and w["equip"] <= 0 and w["ammo"] > 0:
                    w["ammo"] -= 1
                    w["cooldown"] = st["cooldown"]
                    if w["ammo"] == 0:
                        w["reload"] = st["reload"]
                    o, d = self.muzzle(m)
                    sp = math.radians(st["spread_deg"])
                    yaw = m["aim"][0] + self.rng.gauss(0, sp)
                    pitch = m["aim"][1] + self.rng.gauss(0, sp)
                    v = [math.cos(pitch) * math.cos(yaw) * st["speed"], math.cos(pitch) * math.sin(yaw) * st["speed"], math.sin(pitch) * st["speed"]]
                    self.projs.append(dict(id=self.nid["pr"], kind="gun", pos=o, vel=v, by=m, left=st["range"], dmg=st["damage"] * self.dscale(m)))
                    self.nid["pr"] += 1
            elif k == "mortar":
                if trig and w["cooldown"] <= 0 and w["equip"] <= 0:
                    w["cooldown"] = st["cooldown"]
                    o, d = self.muzzle(m, 9.5)
                    self.projs.append(dict(id=self.nid["pr"], kind="mortar", pos=o, vel=[d[0] * st["speed"], d[1] * st["speed"], d[2] * st["speed"]], by=m, left=st["ttl"], st=st, scale=self.dscale(m)))
                    self.nid["pr"] += 1
            elif k == "laser":
                full = st["charge"]
                rel = False
                if w["charge"] > 0:
                    if trig and w["charge"] < full + st["hold_s"]:
                        w["charge"] += DT
                    elif w["charge"] >= st["min_charge"]:
                        rel = True
                    else:
                        w["charge"] += DT
                elif trig and w["cooldown"] <= 0 and w["equip"] <= 0 and t >= m["short_until"]:
                    w["charge"] = DT
                if rel:
                    frac = st["min_frac"] + (1 - st["min_frac"]) * min(1.0, w["charge"] / full)
                    w["charge"] = 0.0
                    w["cooldown"] = st["cooldown"]
                    o, d = self.muzzle(m)
                    s, q, isc = self.ray_hit(m, o, d, st["range"])
                    if q is not None:
                        dmg = st["damage"] * frac * self.dscale(m)
                        if isc:
                            self.hit_crate(q, dmg, m)
                        else:
                            if any(x and x["kind"] == "mortar" for x in q["weapons"]):
                                dmg *= 1 + st["cookoff"]
                            self.damage(q, dmg, o, m, "laser")
            elif k == "lightning":
                if trig and w["cooldown"] <= 0 and w["equip"] <= 0 and m["energy"] >= st["energy"]:
                    w["cooldown"] = st["cooldown"]
                    m["energy"] -= st["energy"]
                    m["last_spend"] = t
                    o = [m["pos"][0], m["pos"][1], m["pos"][2] + 7]
                    cands = [(q, 4.5, False) for q in self.enemies(m)]
                    if m["player"]:
                        cands += [(c, 1.2, True) for c in self.crates.values()]
                    best = None
                    bd = st["range"]
                    for q, h, isc in cands:
                        p = q["pos"]
                        dd = math.dist(o, (p[0], p[1], p[2] + h))
                        if dd < bd and abs(angdiff(math.atan2(p[1] - o[1], p[0] - o[0]), m["aim"][0])) < math.radians(st["cone_deg"]) and self.nav.los(o[0], o[1], o[2], p[0], p[1], p[2] + h):
                            bd = dd
                            best = (q, isc)
                    dmg = st["damage"] * self.dscale(m)
                    hit = set()
                    n = 0
                    while best is not None and n <= st["chain"]:
                        q, isc = best
                        hit.add(q["id"])
                        if isc:
                            self.hit_crate(q, dmg, m)
                        else:
                            for x in q["weapons"]:
                                if x and x["kind"] == "laser":
                                    x["charge"] = 0.0
                                    q["short_until"] = t + st["short_s"]
                            self.damage(q, dmg, o, m, "lightning")
                        dmg *= st["chain_falloff"]
                        n += 1
                        nb = None
                        nd = st["chain_range"]
                        for q2, h, isc2 in cands:
                            if q2["id"] in hit or (not isc2 and not q2["alive"]):
                                continue
                            dd = math.dist(q["pos"], q2["pos"])
                            if dd < nd:
                                nd = dd
                                nb = (q2, isc2)
                        best = nb

    def step_projs(self):
        nav = self.nav
        keep = []
        for pr in self.projs:
            p = pr["pos"]
            v = pr["vel"]
            by = pr["by"]
            if pr["kind"] == "gun":
                sp = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
                seg = sp * DT
                d = (v[0] / sp, v[1] / sp, v[2] / sp)
                s, q, isc = self.ray_hit(by, p, d, seg)
                e = [p[0] + v[0] * DT, p[1] + v[1] * DT, p[2] + v[2] * DT]
                if q is not None:
                    if isc:
                        self.hit_crate(q, pr["dmg"], by)
                    else:
                        self.damage(q, pr["dmg"], (p[0] - d[0] * 20, p[1] - d[1] * 20), by, "gun")
                    continue
                mid = ((p[0] + e[0]) / 2, (p[1] + e[1]) / 2, (p[2] + e[2]) / 2)
                if nav.surf_at(e[0], e[1]) > e[2] or nav.surf_at(mid[0], mid[1]) > mid[2]:
                    continue
                pr["pos"] = e
                pr["left"] -= seg
                if pr["left"] > 0:
                    keep.append(pr)
            else:
                st = pr["st"]
                v[2] -= st["gravity"] * DT
                e = [p[0] + v[0] * DT, p[1] + v[1] * DT, p[2] + v[2] * DT]
                boom = nav.surf_at(e[0], e[1]) >= e[2]
                direct = None
                for q in self.enemies(by):
                    qp = q["pos"]
                    if (e[0] - qp[0]) ** 2 + (e[1] - qp[1]) ** 2 < 6.5 and qp[2] - 0.5 < e[2] < qp[2] + 9.5:
                        direct = q
                        boom = True
                pr["pos"] = e
                pr["left"] -= DT
                if boom or pr["left"] <= 0:
                    if direct is not None:
                        self.damage(direct, st["damage"] * pr["scale"], e, by, "mortar")
                    R = st["splash_radius"]
                    for q in list(self.enemies(by)) + [by]:
                        if not q["alive"]:
                            continue
                        qp = q["pos"]
                        dd = math.dist(e, (qp[0], qp[1], qp[2] + 4.5)) - 2.5
                        dd = max(0.0, dd)
                        if dd < R:
                            fall = 1 - (1 - st["splash_edge"]) * dd / R
                            dmg = st["splash"] * fall * pr["scale"] * (self.mcfg["self_damage_mult"] if q is by else 1.0)
                            ux, uy = norm2(qp[0] - e[0], qp[1] - e[1])
                            q["vel"][0] += ux * st["knock"] * fall
                            q["vel"][1] += uy * st["knock"] * fall
                            q["vel"][2] += st["knock_up"] * fall
                            q["grounded"] = False
                            q["energy"] = max(0.0, q["energy"] - st["drain"] * fall)
                            self.damage(q, dmg, e, by, "mortar")
                    continue
                keep.append(pr)
        self.projs = keep

    # ------------------------------------------------------------ physics
    def step_mech(self, m, zc, zr, dps):
        nav = self.nav
        cmd = m["cmd"]
        st = m["stats"]
        t = self.t
        mv = cmd.get("move") or [0.0, 0.0]
        ml = math.hypot(mv[0], mv[1])
        if ml > 1:
            mv = [mv[0] / ml, mv[1] / ml]
        boost = bool(cmd.get("boost"))
        gr = m["grounded"]
        v = m["vel"]
        p = m["pos"]
        sp = st["walk_speed"]
        if boost and gr and ml > 0.05 and m["energy"] > 0:
            sp = st["boost_speed"]
            m["energy"] = max(0.0, m["energy"] - st["boost_drain"] * DT)
            m["last_spend"] = t
        for w in m["weapons"]:
            if w and w["kind"] == "laser" and w["charge"] > 0:
                sp *= st["charge_move_mult"]
        if cmd.get("jump") and gr and not m.get("jumped"):
            v[2] = st["jump_speed"]
            gr = False
            m["jumped"] = True
        acc = st["ground_accel"] if gr else st["air_accel"]
        for q in (0, 1):
            dv = mv[q] * sp - v[q]
            lim = acc * DT
            v[q] += max(-lim, min(lim, dv))
        for q in (0, 1):
            np_ = [p[0], p[1]]
            np_[q] += v[q] * DT
            if nav.surf_at(np_[0], np_[1]) <= p[2] + st["step_up"] and nav.minX + 2 < np_[0] < nav.minX + nav.nnx * 3 - 2 and nav.minY + 2 < np_[1] < nav.minY + nav.nny * 3 - 2:
                p[q] = np_[q]
            else:
                v[q] = 0.0
        g = nav.surf_at(p[0], p[1])
        if not gr:
            if boost and m["energy"] > 0:
                v[2] = min(st["max_rise_speed"], v[2] + st["jet_accel"] * DT)
                m["energy"] = max(0.0, m["energy"] - st["jet_drain"] * DT)
                m["last_spend"] = t
            v[2] -= st["gravity"] * DT
            p[2] += v[2] * DT
            if p[2] <= g:
                p[2] = g
                gr = True
                v[2] = 0.0
        else:
            if p[2] > g + 0.5:
                gr = False
            else:
                p[2] = g
        m["grounded"] = gr
        # aim
        a = cmd.get("aim")
        if a:
            dy = angdiff(a[0], m["aim"][0])
            lim = st["turn_rate"] * DT
            m["aim"][0] = (m["aim"][0] + max(-lim, min(lim, dy)) + math.pi) % (2 * math.pi) - math.pi
            tp = max(st["min_pitch"], min(st["max_pitch"], a[1]))
            lim = st["pitch_rate"] * DT
            m["aim"][1] += max(-lim, min(lim, tp - m["aim"][1]))
        if t - m["last_spend"] > st["energy_regen_delay"]:
            m["energy"] = min(st["energy"], m["energy"] + st["energy_regen"] * DT)
        self.fire_weapons(m, cmd.get("fire") or [False, False])
        if not m["alive"]:
            return
        inz = math.hypot(p[0] - zc[0], p[1] - zc[1]) <= zr
        m["in_zone"] = inz
        if not inz:
            self.damage(m, dps * DT, None, None, "zone")
        elif t - m["last_dmg"] > st["regen_delay"]:
            m["hp"] = min(self.max_hp(m), m["hp"] + st["regen_per_s"] * DT)

    # ------------------------------------------------------------ player obs / actions
    def apply_upgrade(self, m, uid):
        for u in self.cfg["upgrades"]:
            if u["id"] == uid:
                for e in u["effects"]:
                    path = e["path"].split(".")
                    d = m["stats"] if path[0] == "mech" else m["wstats"][path[1]]
                    key = path[-1]
                    old = self.max_hp(m)
                    if "mul" in e:
                        d[key] = d[key] * e["mul"]
                    if "add" in e:
                        d[key] = d[key] + e["add"]
                    if key == "hp":
                        m["hp"] += self.max_hp(m) - old
                m["upgrades"].append(uid)
                m["events"].append(dict(type="upgrade", id=uid))

    def player_obs(self, zc, zr, ph):
        m = self.player
        t = self.t
        x, y, z = m["pos"]
        seen = []
        for q in self.house:
            if not q["alive"]:
                continue
            if self.sees(m, q["pos"]):
                self.seen_t[q["id"]] = t
            if t - self.seen_t.get(q["id"], -99) <= self.cfg["vision"]["memory_s"]:
                ch = any(w and w["kind"] == "laser" and w["charge"] > 0 for w in q["weapons"])
                seen.append(dict(id=q["id"], kind="mech", pos=[round(v, 2) for v in q["pos"]], vel=[round(v, 2) for v in q["vel"]], aim=[round(v, 3) for v in q["aim"]],
                                 hp=round(q["hp"], 1), max_hp=self.max_hp(q), cubes=q["cubes"], weapons=[w["kind"] for w in q["weapons"] if w], charging=ch))
        vis = lambda p, top: (abs(p[0] - x) < 230 and abs(p[1] - y) < 230) and self.sees(m, p, top, top * 0.5)
        ws = []
        for w in m["weapons"]:
            if not w:
                ws.append(None)
                continue
            d = dict(kind=w["kind"], cooldown=round(max(w["cooldown"], w["equip"]), 3), stats=w["stats"])
            if w["kind"] == "gun":
                d["ammo"] = w["ammo"]; d["reload"] = round(max(0.0, w["reload"]), 3)
            if w["kind"] == "laser":
                d["charge"] = round(w["charge"], 3)
            ws.append(d)
        # offers
        if m["offer"] is None and m["offers"]:
            m["offers"].pop(0)
            avail = []
            have = [w["kind"] for w in m["weapons"] if w]
            for u in self.cfg["upgrades"]:
                if m["upgrades"].count(u["id"]) < u["max"] and (not u.get("requires") or u["requires"] in have):
                    avail.append(u)
            self.rng.shuffle(avail)
            m["noffer"] += 1
            m["offer"] = dict(id=m["noffer"], expires=t + 5, choices=[dict(id=u["id"], name=u["name"], desc=u["desc"]) for u in avail[:3]])
        for c in self.crates.values():
            if c.get("npc") and vis(c["pos"], 3.5):
                seen.append(dict(id=c["id"], kind="npc", pos=[round(v, 2) for v in c["pos"]], vel=[0, 0, 0], aim=[0, 0], hp=c["hp"], max_hp=350))
                # the drone shoots back at close range (25 dmg/s, ~60% of shots land)
                if math.dist(c["pos"], m["pos"]) < 45 and self.rng.random() < 0.06:
                    self.damage(m, 25.0, c["pos"], None, "npc")
        ev = m["events"]
        m["events"] = []
        obs = dict(type="obs", tick=self.tick, t=round(t, 2), alive=sum(1 for q in self.mechs if q["alive"]),
                   self=dict(id=m["id"], pos=[round(v, 2) for v in m["pos"]], vel=[round(v, 2) for v in m["vel"]], aim=[round(v, 3) for v in m["aim"]], hp=round(m["hp"], 1), max_hp=self.max_hp(m),
                             energy=round(m["energy"], 1), max_energy=m["stats"]["energy"], grounded=m["grounded"], cubes=m["cubes"], upgrades=list(m["upgrades"]),
                             in_zone=m.get("in_zone", True), weapons=ws, stats=m["stats"]),
                   mechs=seen,
                   projectiles=[dict(id=pr["id"], kind=pr["kind"], pos=[round(v, 2) for v in pr["pos"]], vel=[round(v, 2) for v in pr["vel"]]) for pr in self.projs if pr["by"] is not m and abs(pr["pos"][0] - x) < 200 and abs(pr["pos"][1] - y) < 200],
                   crates=[dict(id=c["id"], pos=c["pos"], hp=c["hp"]) for c in self.crates.values() if not c.get("npc") and vis(c["pos"], 2.4)],
                   cubes=[dict(id=c["id"], pos=c["pos"]) for c in self.cubes.values() if vis(c["pos"], 1.0)],
                   pickups=[dict(id=c["id"], kind=c["kind"], pos=c["pos"]) for c in self.pickups.values() if vis(c["pos"], 1.0)],
                   sounds=[], beams=[], events=ev, drops=[],
                   zone=dict(center=zc, radius=zr, next_center=ph["nc"], next_radius=ph["nr"], shrink_start=ph["ss"], shrink_end=ph["se"], dps=ph["dps"]),
                   offer=m["offer"])
        return obs

    def post_action(self, m, a):
        t = self.t
        m["jumped"] = False
        if m["offer"] is not None:
            pick = a.get("pick")
            if pick is None and t >= m["offer"]["expires"]:
                pick = 0
            if pick is not None and m["offer"]["choices"]:
                pick = min(max(int(pick), 0), len(m["offer"]["choices"]) - 1)
                self.apply_upgrade(m, m["offer"]["choices"][pick]["id"])
                m["offer"] = None
            elif not m["offer"]["choices"]:
                m["offer"] = None
        if a.get("take") is not None:
            pk = self.pickups.get(a["take"])
            if pk and math.hypot(pk["pos"][0] - m["pos"][0], pk["pos"][1] - m["pos"][1]) <= self.cfg["loot"]["pickup_radius"] and abs(pk["pos"][2] - m["pos"][2]) <= 4:
                kinds = [w["kind"] if w else None for w in m["weapons"]]
                if pk["kind"] not in kinds:
                    slot = None
                    if None in kinds:
                        slot = kinds.index(None)
                    elif a.get("drop") in (0, 1):
                        slot = a["drop"]
                        old = m["weapons"][slot]
                        if old["kind"] == "laser" and old["charge"] > 0:
                            slot = None
                        else:
                            self.add_pickup(old["kind"], m["pos"])
                    if slot is not None:
                        w = self.new_weapon(m, pk["kind"])
                        w["equip"] = max(self.cfg["loot"]["equip_s"], w["stats"]["cooldown"])
                        m["weapons"][slot] = w
                        del self.pickups[pk["id"]]
                        m["events"].append(dict(type="took", id=pk["id"], kind=pk["kind"], slot=slot))
            else:
                m["events"].append(dict(type="take_failed", id=a["take"], reason="gone" if not pk else "range"))

    # ------------------------------------------------------------ house AI
    def house_act(self, m, zc, zr, ph):
        T = TIER[m["tier"]]
        b = m["brain"]
        rng = self.rng
        t = self.t
        pl = self.player
        x, y, z = m["pos"]
        act = dict(move=[0.0, 0.0], fire=[False, False])
        hunter = m["id"] in self.hunters
        see = pl["alive"] and self.sees(m, pl["pos"])
        if see:
            if b["seen_t"] < 0:
                b["seen_t"] = t
        else:
            b["seen_t"] = -1.0
        # ---- movement
        dzc = math.hypot(x - zc[0], y - zc[1])
        dnc = math.hypot(x - ph["nc"][0], y - ph["nc"][1])
        goal = None
        if dzc > zr - 30 or (t > ph["ss"] - 15 and dnc > ph["nr"] - 15):
            goal = (ph["nc"][0], ph["nc"][1])
        px, py, pz = pl["pos"]
        d = math.dist(m["pos"], pl["pos"])
        w2 = m["weapons"][1]["kind"] if m["weapons"][1] else None
        pref = {"lightning": 25.0, "laser": 80.0, "mortar": 60.0}.get(w2, 45.0)
        provoked = d < PARAMS["bystander_range"] or (PARAMS["provoke"] and t - b.get("hit_t", -99) < 12)
        engaged = see and (hunter or provoked)
        if goal is None and engaged:
            if t >= b["strafe_until"]:
                b["strafe"] = -b["strafe"]
                b["strafe_until"] = t + 0.5 + rng.random() * 1.2
            ux, uy = norm2(px - x, py - y)
            rad = 1.0 if d > pref + 8 else (-1.0 if d < pref - 10 else 0.0)
            if not hunter and rad > 0:
                rad = 0.0
            sw = T["strafe"]
            mx, my = norm2(ux * rad - uy * b["strafe"] * sw, uy * rad + ux * b["strafe"] * sw)
            act["move"] = [mx, my]
            if T["climb"] and hunter and pz > z + 4 and math.hypot(px - x, py - y) < 35:
                act["move"] = [ux, uy]
                act["jump"] = m["grounded"]
                act["boost"] = not m["grounded"]
        else:
            if goal is None and hunter and pl["alive"]:
                if self.known is not None and t - self.known[1] < 20:
                    goal = (self.known[0][0], self.known[0][1])
                else:
                    if b.get("guess_t", -99) < t - 10:
                        b["guess_t"] = t
                        a = rng.random() * 6.28
                        b["guess"] = (px + math.cos(a) * 50, py + math.sin(a) * 50)
                    goal = b["guess"]
                if T["climb"] and pz > z + 4 and math.hypot(goal[0] - x, goal[1] - y) < 30:
                    ux, uy = norm2(px - x, py - y)
                    act["move"] = [ux, uy]
                    act["jump"] = m["grounded"]
                    act["boost"] = not m["grounded"]
                    goal = "climb"
            if goal is None:
                if b["roam"] is None or t - b["roam_t"] > 25 or math.hypot(b["roam"][0] - x, b["roam"][1] - y) < 8:
                    a = rng.random() * 6.28
                    r = ph["nr"] * 0.7 * math.sqrt(rng.random())
                    b["roam"] = (ph["nc"][0] + math.cos(a) * r, ph["nc"][1] + math.sin(a) * r)
                    b["roam_t"] = t
                goal = b["roam"]
            if goal != "climb":
                nv = m["nav"]
                nv.deadline = time.perf_counter() + 0.002
                mv = nv.nav_move(x, y, goal[0], goal[1])
                act["move"] = [mv[0], mv[1]]
                # simple unstick
                if math.hypot(m["vel"][0], m["vel"][1]) < 1.0 and (abs(mv[0]) + abs(mv[1])) > 0.5:
                    b["stuck"] = b.get("stuck", 0) + 1
                    if b["stuck"] > 10:
                        act["jump"] = m["grounded"]
                        act["boost"] = not m["grounded"]
                        if b["stuck"] > 40:
                            b["stuck"] = 0
                            nv.path = None
                else:
                    b["stuck"] = 0
        # ---- aim / fire
        shoot = see and (hunter or provoked) and t - b["seen_t"] >= T["react"]
        if see:
            n = b["noise"]
            sg = T["noise"] * PARAMS["noise_mult"]
            n[0] = n[0] * 0.8 + rng.gauss(0, sg) * 0.6
            n[1] = n[1] * 0.8 + rng.gauss(0, sg * 0.5) * 0.6
            gun = m["weapons"][0]
            tl = (d / gun["stats"]["speed"] + 0.08) if T["lead"] else 0.0
            w = m["weapons"][1]
            if w and w["kind"] == "laser" and w["charge"] > 0:
                tl = 0.08 if T["lead"] else 0.0
            tx, ty, tz = px + pl["vel"][0] * tl, py + pl["vel"][1] * tl, pz + 4.5
            yaw = math.atan2(ty - y, tx - x)
            pitch = math.atan2(tz - (z + 7), max(1.0, math.hypot(tx - x, ty - y)))
            if w and w["kind"] == "mortar" and w["cooldown"] <= 0.3 and 25 < d < 180 and shoot:
                tf = d / 60.0
                sol = NL.Bot.mortar_solution(m["nav"], dict(pos=m["pos"], stats=m["stats"]), px + pl["vel"][0] * tf * (1 if T["lead"] else 0), py + pl["vel"][1] * tf * (1 if T["lead"] else 0), pz + 1, w["stats"]["speed"])
                if sol:
                    yaw, pitch = sol[0], sol[1]
                    if abs(angdiff(m["aim"][0], yaw)) < 0.05 and abs(m["aim"][1] - pitch) < 0.04:
                        act["fire"][1] = True
            act["aim"] = [yaw + n[0], pitch + n[1]]
            if shoot:
                aligned = abs(angdiff(m["aim"][0], yaw)) < 0.12
                if aligned and d < 160 and rng.random() < T["rate"]:
                    act["fire"][0] = True
                if w and w["kind"] == "lightning" and d < 44:
                    act["fire"][1] = True
                if w and w["kind"] == "laser" and d < 270:
                    act["fire"][1] = w["charge"] < w["stats"]["charge"] and aligned
        else:
            mv = act["move"]
            base = math.atan2(mv[1], mv[0]) if (abs(mv[0]) + abs(mv[1])) > 0.1 else m["aim"][0]
            if hunter and self.known is not None and t - self.known[1] < 6:
                base = math.atan2(self.known[0][1] - y, self.known[0][0] - x)
            act["aim"] = [base + 0.9 * math.sin(t * 1.3 + m["id"]), 0.0]
            if m["weapons"][0]["ammo"] < 6:
                act["reload"] = True
        return act

    def squad_update(self):
        t = self.t
        pl = self.player
        alive = [m for m in self.house if m["alive"]]
        if pl["alive"]:
            for m in alive:
                if m["brain"]["seen_t"] >= 0:
                    self.known = (list(pl["pos"]), t)
                    break
        dead = 15 - len(alive)
        n = 1 + int(dead * PARAMS["hunters_per_dead"]) + int(t * PARAMS["hunters_per_s"])
        if t >= PARAMS["all_at"]:
            n = 15
        order = sorted(alive, key=lambda m: (TIERS.index(m["tier"]), m["id"]))
        self.hunters = set(m["id"] for m in order[:n])

    # ------------------------------------------------------------ run
    def run(self):
        pl = self.player
        dur = self.cfg["match"]["duration_s"]
        while self.t < dur and pl["alive"]:
            zc, zr, ph = self.zone()
            if self.tick % 2 == 0:
                obs = json.loads(json.dumps(self.player_obs(zc, zr, ph)))
                s = time.perf_counter()
                try:
                    a = self.bot.act(obs)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    a = {}
                ms = (time.perf_counter() - s) * 1000
                self.maxms = max(self.maxms, ms)
                lab = (a.get("debug") or {}).get("label", "?").split()[0]
                self.labels[lab] = self.labels.get(lab, 0) + 1
                for k in ("move", "boost", "aim"):
                    if k not in a and k in pl["cmd"]:
                        a[k] = pl["cmd"][k]
                pl["cmd"] = a
                self.post_action(pl, a)
                self.squad_update()
                for m in self.house:
                    if m["alive"]:
                        m["jumped"] = False
                        m["cmd"] = self.house_act(m, zc, zr, ph)
                if self.verbose and self.tick % 200 == 0:
                    print("t=%.0f %s hp=%.0f pos=%s w=%s cubes=%d alive=%d hunters=%d" % (self.t, lab, pl["hp"], [round(v) for v in pl["pos"]], [w and w["kind"] for w in pl["weapons"]], pl["cubes"], sum(1 for q in self.mechs if q["alive"]), len(self.hunters)))
            for m in self.mechs:
                if m["alive"]:
                    self.step_mech(m, zc, zr, ph["dps"])
            self.step_projs()
            # cubes
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
            self.t = self.tick * DT
            if not any(m["alive"] for m in self.house):
                break
        place = pl.get("place", 1)
        return dict(place=place, score=16 - place, t=round(self.t, 1), kills=pl["kills"], cubes=pl["cubes"], maxms=round(self.maxms, 1), labels=self.labels)


def run_one(args):
    botdir, stream, seed, verbose, params = args
    PARAMS.update(params)
    hello = json.loads(gzip.open(stream, "rt").readline())
    mod = load_mod(os.path.join(botdir, "bot.py"), "playerbot_%d" % seed)
    sim = Sim(hello, mod, seed, verbose)
    r = sim.run()
    r["seed"] = seed
    return r


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("botdir")
    ap.add_argument("stream")
    ap.add_argument("--seeds", type=int, default=16)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--params", default="{}")
    a = ap.parse_args()
    params = json.loads(a.params)
    jobs = [(a.botdir, a.stream, s, a.verbose, params) for s in range(a.start, a.start + a.seeds)]
    t0 = time.time()
    if a.procs <= 1 or a.verbose:
        res = [run_one(j) for j in jobs]
    else:
        import multiprocessing as mp
        with mp.Pool(a.procs) as p:
            res = p.map(run_one, jobs, chunksize=1)
    sc = [r["score"] for r in res]
    mean = sum(sc) / len(sc)
    sd = (sum((s - mean) ** 2 for s in sc) / max(1, len(sc) - 1)) ** 0.5
    hist = {}
    for s in sc:
        hist[s] = hist.get(s, 0) + 1
    print("score %.2f +- %.2f (n=%d)  kills %.2f  death_t %.0f  maxms %.1f  [%.0fs]" % (mean, sd / len(sc) ** 0.5, len(sc), sum(r["kills"] for r in res) / len(res), sum(r["t"] for r in res) / len(res), max(r["maxms"] for r in res), time.time() - t0))
    print("hist", dict(sorted(hist.items())))
    if a.verbose:
        for r in res:
            print(r)


if __name__ == "__main__":
    main()
