"""Opponent archetypes for FFA self-play. Different decision logic from bot.py (they only borrow its
path-finding and aiming helpers): hunter, sniper, looter, edge camper."""
import math, os, random, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import arena_sim as A

NL = A.NL
angdiff = NL.angdiff
norm2 = NL.norm2
TAU = 2 * math.pi


class Arch:
    name = "arch"
    want = ("laser", "lightning", "mortar")

    def __init__(self, hello):
        self.h = NL.Bot(dict(hello))          # helper: nav_move, lead, aim_at, w_slot
        self.h.F["mode"] = "squad"
        self.nav = self.h.nav
        self.rng = random.Random(hello.get("seed", 1))
        self.strafe = 1.0
        self.strafe_until = 0.0
        self.roam = None
        self.roam_t = -99.0
        self.items = {}
        self.hit_t = -99.0
        self.hit_by = None
        self.mode = self.name

    # ---- helpers
    def los(self, me, p, h=4.5):
        x, y, z = me["pos"]
        return self.nav.los(x, y, z + 7, p[0], p[1], p[2] + h)

    def go(self, me, gx, gy):
        self.h.deadline = time.perf_counter() + 0.003
        x, y, _ = me["pos"]
        return self.h.nav_move(x, y, gx, gy)

    def strafe_dir(self, me, tp, t, rad=0.0):
        if t >= self.strafe_until:
            if self.rng.random() < 0.75:
                self.strafe = -self.strafe
            self.strafe_until = t + 0.5 + self.rng.random()
        x, y, _ = me["pos"]
        ux, uy = norm2(tp[0] - x, tp[1] - y)
        mx, my = norm2(ux * rad - uy * self.strafe, uy * rad + ux * self.strafe)
        if self.nav.val(x + mx * 6, y + my * 6) != 1:
            self.strafe = -self.strafe
            mx, my = norm2(ux * rad + uy * self.strafe * -1, uy * rad - ux * self.strafe * -1)
        return [mx, my]

    def shoot(self, me, tgt, act):
        h = self.h
        gi, gun = h.w_slot(me, "gun")
        li, laser = h.w_slot(me, "laser")
        zi, zap = h.w_slot(me, "lightning")
        mi, mortar = h.w_slot(me, "mortar")
        fire = [False, False]
        aim_yaw, aim_pitch = me.get("aim", [0, 0])
        speed = gun["stats"]["speed"] if gun else 1e9
        charge = (laser.get("charge") or 0.0) if laser else 0.0
        if laser and (charge > 0 or not gun):
            speed = 1e9
        lx, ly, lz = h.lead(me, tgt, speed, 0.08)
        yaw, pitch, dist = h.aim_at(me, lx, ly, lz)
        act["aim"] = [yaw, pitch]
        los = self.los(me, tgt["pos"])
        ok = abs(angdiff(aim_yaw, yaw)) < max(0.012, 2.4 / dist) and abs(aim_pitch - pitch) < max(0.015, 3.5 / dist)
        if los:
            if gun and ok and dist < 150 and gun.get("ammo", 1) > 0:
                fire[gi] = True
            if zap and dist < zap["stats"]["range"] * 0.95 and abs(angdiff(aim_yaw, yaw)) < 0.2 and me.get("energy", 0) > 4:
                fire[zi] = True
            if laser and dist < 270:
                full = laser["stats"]["charge"]
                if charge > 0:
                    fire[li] = not (charge >= full - 0.06 and ok)
                elif (laser.get("cooldown") or 0) <= 0.11:
                    fire[li] = True
            if mortar and (mortar.get("cooldown") or 0) <= 0.1 and 25 < dist < 180 and not (laser and charge > 0):
                sol = h.mortar_solution(me, tgt["pos"][0] + tgt.get("vel", [0, 0, 0])[0] * dist / 60, tgt["pos"][1] + tgt.get("vel", [0, 0, 0])[1] * dist / 60, tgt["pos"][2] + 1, mortar["stats"]["speed"])
                if sol:
                    act["aim"] = [sol[0], sol[1]]
                    if abs(angdiff(aim_yaw, sol[0])) < 0.04 and abs(aim_pitch - sol[1]) < 0.03:
                        fire[mi] = True
        act["fire"] = fire
        if gun and gun.get("ammo", 1) <= 0:
            act["reload"] = True

    def take(self, me, obs, act):
        x, y, z = me["pos"]
        ws = me.get("weapons") or [None, None]
        kinds = [w["kind"] if w else None for w in ws]
        for p in obs.get("pickups") or []:
            if p["kind"] in kinds or math.hypot(p["pos"][0] - x, p["pos"][1] - y) > 3.5 or abs(p["pos"][2] - z) > 3.5:
                continue
            if None in kinds:
                act["take"] = p["id"]
                return
            # replace the least wanted
            rank = {k: i for i, k in enumerate(reversed(self.want + ("gun",)))} if "gun" not in self.want else {k: i for i, k in enumerate(reversed(self.want))}
            lo = 0 if rank.get(kinds[0], -1) <= rank.get(kinds[1], -1) else 1
            if rank.get(p["kind"], -1) > rank.get(kinds[lo], -1) and not (ws[lo].get("charge") or 0) > 0:
                act["take"] = p["id"]
                act["drop"] = lo
                return

    def zone_goal(self, me, zn, margin=25.0):
        x, y, _ = me["pos"]
        c = zn["center"]; r = zn["radius"]; nc = zn["next_center"]; nr = zn["next_radius"]
        if math.hypot(x - c[0], y - c[1]) > r - margin or (not me.get("in_zone", True)):
            return nc
        return None

    def nearest_item(self, me, obs, kinds=("pickup", "cube", "crate"), maxd=150):
        x, y, z = me["pos"]
        t = obs["t"]
        for key, typ in (("pickups", "pickup"), ("cubes", "cube"), ("crates", "crate")):
            for it in obs.get(key) or []:
                self.items[it["id"]] = (typ, it.get("kind"), it["pos"], t)
        ws = me.get("weapons") or [None, None]
        have = [w["kind"] for w in ws if w]
        best = None
        bd = maxd
        dead = []
        for iid, (typ, kind, p, seen) in self.items.items():
            d = math.hypot(p[0] - x, p[1] - y)
            if (d < 25 and seen < t - 0.3) or t - seen > 90:
                dead.append(iid)
                continue
            if typ not in kinds or self.nav.val(p[0], p[1]) != 1 or abs(p[2] - self.nav.surf_at(p[0], p[1])) > 3:
                continue
            if typ == "pickup" and (kind in have or (len(have) >= 2 and kind not in self.want[:1])):
                continue
            w = d * (0.6 if typ in ("cube", "pickup") else 1.0)
            if w < bd:
                bd = w
                best = (typ, p, d)
        for iid in dead:
            self.items.pop(iid, None)
        return best

    def wander(self, me, zn, t):
        x, y, _ = me["pos"]
        nc = zn["next_center"]; nr = zn["next_radius"]
        if self.roam is None or t - self.roam_t > 25 or math.hypot(self.roam[0] - x, self.roam[1] - y) < 8:
            a = self.rng.random() * TAU
            r = max(10.0, nr) * 0.6 * math.sqrt(self.rng.random())
            self.roam = (nc[0] + math.cos(a) * r, nc[1] + math.sin(a) * r)
            self.roam_t = t
        return self.go(me, self.roam[0], self.roam[1])

    def base(self, obs):
        me = obs["self"]
        act = {"tick": obs["tick"], "move": [0.0, 0.0], "fire": [False, False]}
        if obs.get("offer") and obs["offer"].get("choices"):
            ids = [c["id"] for c in obs["offer"]["choices"]]
            pick = 0
            for pref in self.upg:
                if pref in ids:
                    pick = ids.index(pref)
                    break
            act["pick"] = pick
        for ev in obs.get("events") or []:
            if ev.get("type") == "damaged" and ev.get("by") is not None:
                self.hit_t = obs["t"]
                self.hit_by = ev["by"]
        mechs = [m for m in obs.get("mechs") or [] if m.get("kind") == "mech"]
        x, y, z = me["pos"]
        for m in mechs:
            m["_d"] = math.dist(m["pos"], me["pos"])
        mechs.sort(key=lambda m: m["_d"])
        self.take(me, obs, act)
        return me, act, mechs

    upg = ("plating", "armor", "nanites")


class Hunter(Arch):
    """Chases the weakest visible mech and brawls at its weapons' range."""
    name = "hunter"
    want = ("lightning", "laser", "mortar")
    upg = ("gun_damage", "laser_damage", "lightning_efficiency", "plating", "armor")

    def act(self, obs):
        me, act, mechs = self.base(obs)
        t = obs["t"]; zn = obs["zone"]
        zg = self.zone_goal(me, zn)
        vis = [m for m in mechs if m["_d"] < 200]
        tgt = min(vis, key=lambda m: m.get("hp", 1400) + m["_d"] * 2) if vis else None
        if tgt is not None:
            self.shoot(me, tgt, act)
        else:
            act["aim"] = [me["aim"][0] + 1.0, 0.0]
        if zg is not None:
            act["move"] = list(self.go(me, zg[0], zg[1]))
        elif tgt is not None:
            _, zap = self.h.w_slot(me, "lightning")
            R = 25.0 if zap else 40.0
            d = tgt["_d"]
            if not self.los(me, tgt["pos"]):
                act["move"] = list(self.go(me, tgt["pos"][0], tgt["pos"][1]))
            else:
                act["move"] = self.strafe_dir(me, tgt["pos"], t, 1.0 if d > R + 8 else (-1.0 if d < R - 8 else 0.0))
                act["boost"] = bool(zap and d > 45 and me.get("energy", 0) > 50)
        else:
            it = self.nearest_item(me, obs, maxd=120)
            if it:
                typ, p, d = it
                if typ == "crate" and d < 35 and self.los(me, p, 1.2):
                    yaw, pitch, _ = self.h.aim_at(me, p[0], p[1], p[2] + 1.2)
                    act["aim"] = [yaw, pitch]
                    act["fire"] = [abs(angdiff(me["aim"][0], yaw)) < 0.03, False]
                else:
                    act["move"] = list(self.go(me, p[0], p[1]))
            else:
                act["move"] = list(self.wander(me, zn, t))
        return act


class Sniper(Arch):
    """Keeps 110-170 m from everyone and trades at long range with a laser; backs off when approached."""
    name = "sniper"
    want = ("laser", "mortar", "lightning")
    upg = ("laser_damage", "laser_charge", "gun_velocity", "plating", "armor")

    def act(self, obs):
        me, act, mechs = self.base(obs)
        t = obs["t"]; zn = obs["zone"]
        x, y, z = me["pos"]
        zg = self.zone_goal(me, zn, 35.0)
        vis = [m for m in mechs if m["_d"] < 260 and self.los(me, m["pos"])]
        tgt = min(vis, key=lambda m: m.get("hp", 1400) + m["_d"]) if vis else None
        if tgt is not None:
            self.shoot(me, tgt, act)
        else:
            act["aim"] = [me["aim"][0] + 1.0, 0.0]
        _, laser = self.h.w_slot(me, "laser")
        near = mechs[0] if mechs else None
        if zg is not None:
            act["move"] = list(self.go(me, zg[0], zg[1]))
        elif near is not None and near["_d"] < 110:
            # back away, staying inside the zone
            ux, uy = norm2(x - near["pos"][0], y - near["pos"][1])
            best = None
            for k in range(-3, 4):
                a = math.atan2(uy, ux) + k * 0.4
                cx, cy = x + math.cos(a) * 14, y + math.sin(a) * 14
                if self.nav.val(cx, cy) == 1 and math.hypot(cx - zn["center"][0], cy - zn["center"][1]) < zn["radius"] - 30:
                    if best is None or abs(k) < abs(best[0]):
                        best = (k, math.cos(a), math.sin(a))
            if best:
                act["move"] = [best[1], best[2]]
                act["boost"] = near["_d"] < 60 and me.get("energy", 0) > 20
            else:
                act["move"] = self.strafe_dir(me, near["pos"], t, -0.5)
        elif not laser:
            it = self.nearest_item(me, obs, kinds=("pickup",), maxd=200)
            act["move"] = list(self.go(me, it[1][0], it[1][1])) if it else list(self.wander(me, zn, t))
        elif tgt is not None:
            d = tgt["_d"]
            act["move"] = self.strafe_dir(me, tgt["pos"], t, 0.6 if d > 170 else 0.0)
        else:
            it = self.nearest_item(me, obs, kinds=("cube", "pickup"), maxd=80)
            act["move"] = list(self.go(me, it[1][0], it[1][1])) if it else list(self.wander(me, zn, t))
        return act


class Looter(Arch):
    """Avoids mechs, farms crates and cubes, then turns aggressive late with a big cube pool."""
    name = "looter"
    want = ("laser", "lightning", "mortar")
    upg = ("plating", "armor", "gun_damage", "laser_damage", "nanites")

    def act(self, obs):
        me, act, mechs = self.base(obs)
        t = obs["t"]; zn = obs["zone"]
        x, y, z = me["pos"]
        zg = self.zone_goal(me, zn)
        late = obs.get("alive", 16) <= 5 or t > 230
        vis = [m for m in mechs if m["_d"] < 200]
        attacker = t - self.hit_t < 4.0
        tgt = None
        if vis and (late or attacker or vis[0]["_d"] < 55):
            tgt = min(vis, key=lambda m: m["_d"] + (0 if m["id"] == self.hit_by else 60))
            self.shoot(me, tgt, act)
        else:
            act["aim"] = [me["aim"][0] + 1.0, 0.0]
        if zg is not None:
            act["move"] = list(self.go(me, zg[0], zg[1]))
        elif late and tgt is not None:
            d = tgt["_d"]
            if not self.los(me, tgt["pos"]):
                act["move"] = list(self.go(me, tgt["pos"][0], tgt["pos"][1]))
            else:
                act["move"] = self.strafe_dir(me, tgt["pos"], t, 1.0 if d > 50 else (-1.0 if d < 30 else 0.0))
        elif vis and vis[0]["_d"] < 130:
            m = vis[0]
            if tgt is not None and m["_d"] < 55:
                act["move"] = self.strafe_dir(me, m["pos"], t, -0.7)
            else:
                ux, uy = norm2(x - m["pos"][0], y - m["pos"][1])
                act["move"] = [ux, uy] if self.nav.val(x + ux * 8, y + uy * 8) == 1 else self.strafe_dir(me, m["pos"], t, -0.3)
                act["boost"] = m["_d"] < 70 and me.get("energy", 0) > 20
        else:
            it = self.nearest_item(me, obs, maxd=220)
            if it:
                typ, p, d = it
                if typ == "crate" and d < 35 and self.los(me, p, 1.2):
                    yaw, pitch, _ = self.h.aim_at(me, p[0], p[1], p[2] + 1.2)
                    act["aim"] = [yaw, pitch]
                    act["fire"] = [abs(angdiff(me["aim"][0], yaw)) < 0.03, False]
                else:
                    act["move"] = list(self.go(me, p[0], p[1]))
            else:
                act["move"] = list(self.wander(me, zn, t))
        return act


class Camper(Arch):
    """Sits just inside the zone edge, away from the centre, and shoots whatever comes into view."""
    name = "camper"
    want = ("laser", "mortar", "lightning")
    upg = ("armor", "plating", "laser_damage", "nanites")

    def __init__(self, hello):
        super().__init__(hello)
        self.ang = None

    def act(self, obs):
        me, act, mechs = self.base(obs)
        t = obs["t"]; zn = obs["zone"]
        x, y, z = me["pos"]
        vis = [m for m in mechs if m["_d"] < 230 and self.los(me, m["pos"])]
        tgt = min(vis, key=lambda m: m["_d"]) if vis else None
        if tgt is not None:
            self.shoot(me, tgt, act)
        else:
            c = zn["center"]
            act["aim"] = [math.atan2(c[1] - y, c[0] - x) + 0.9 * math.sin(t * 0.9), 0.0]
        nc = zn["next_center"]; nr = zn["next_radius"]
        if self.ang is None:
            self.ang = math.atan2(y - nc[1], x - nc[0])
        r = max(0.0, nr - 28.0)
        gx, gy = nc[0] + math.cos(self.ang) * r, nc[1] + math.sin(self.ang) * r
        d = math.hypot(gx - x, gy - y)
        if tgt is not None and tgt["_d"] < 70 and me.get("in_zone", True) and d < 40:
            act["move"] = self.strafe_dir(me, tgt["pos"], t, -0.3)
        elif d > 6:
            act["move"] = list(self.go(me, gx, gy))
        elif tgt is not None:
            act["move"] = self.strafe_dir(me, tgt["pos"], t, 0.0)
        return act


ARCH = {"hunter": Hunter, "sniper": Sniper, "looter": Looter, "camper": Camper}
