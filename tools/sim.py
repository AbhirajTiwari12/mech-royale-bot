"""Very rough kinematic smoke-test (NOT the real game): exercises navigation, zone and combat code paths.
usage: python3 tools/sim.py <kit stream.jsonl.gz> [seed] [n_hunters]"""
import gzip, json, math, random, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot as B

hello = json.loads(gzip.open(sys.argv[1], "rt").readline())
obs0 = json.loads(list(gzip.open(sys.argv[1], "rt"))[1])
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
NH = int(sys.argv[3]) if len(sys.argv) > 3 else 3
rng = random.Random(seed)
cfg = hello["config"]
bot = B.Bot(hello)
nav = bot.nav
PROF = {}
ALLMS = []
def wrap(obj, name):
    f = getattr(obj, name)
    def g(*a, **k):
        s = time.perf_counter()
        try: return f(*a, **k)
        finally: PROF[name] = PROF.get(name, 0) + time.perf_counter() - s
    setattr(obj, name, g)
for n_ in ("nav_move", "retreat_move", "loot_move", "fight_move"): wrap(bot, n_)
for n_ in ("nearest_main", "los", "walk_clear"): wrap(nav, n_)
_run = B.Search.run
def run2(self, dl):
    s = time.perf_counter()
    try: return _run(self, dl)
    finally: PROF["search"] = PROF.get("search", 0) + time.perf_counter() - s
B.Search.run = run2
zc = list(cfg["zone"]["start_center"]); zr = cfg["zone"]["start_radius"]
phases = []
t0 = 0; c = zc; r = zr
for ph in cfg["zone"]["phases"]:
    a = rng.random() * 6.28; rr = (r - ph["radius"]) * rng.random()
    nc = [c[0] + math.cos(a) * rr, c[1] + math.sin(a) * rr]
    phases.append(dict(c=c, r=r, nc=nc, nr=ph["radius"], ss=t0 + ph["hold_s"], se=t0 + ph["hold_s"] + ph["shrink_s"], dps=ph["dps"]))
    t0 += ph["hold_s"] + ph["shrink_s"]; c = nc; r = ph["radius"]

def rand_main(cx, cy, rad):
    while True:
        a = rng.random() * 6.28; d = rad * math.sqrt(rng.random())
        x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
        if nav.val(x, y) == 1:
            return [x, y, nav.surf_at(x, y)]

def step(p, v, dt):
    nx, ny = p[0] + v[0] * dt, p[1] + v[1] * dt
    if nav.val(nx, ny) != 0: p[0], p[1] = nx, ny
    elif nav.val(nx, p[1]) != 0: p[0] = nx
    elif nav.val(p[0], ny) != 0: p[1] = ny
    p[2] = nav.surf_at(p[0], p[1])

me = dict(pos=rand_main(zc[0], zc[1], zr * 0.8), vel=[0, 0, 0], aim=[0, 0], hp=1400.0, energy=100.0, weapons=[dict(kind="gun", cooldown=0, ammo=12, reload=0, stats=dict(cfg["weapons"]["gun"])), None], cubes=0)
pickups = [dict(id=8000 + i, kind=rng.choice(os.environ.get("KINDS", "mortar,laser,lightning").split(",")), pos=rand_main(zc[0], zc[1], zr)) for i in range(40)]
crates = [dict(id=1000 + i, pos=rand_main(zc[0], zc[1], zr), hp=300) for i in range(36)]
cubes = []
hunters = [dict(id=i + 1, kind="mech", pos=rand_main(zc[0], zc[1], zr * 0.8), vel=[0, 0, 0], aim=[0, 0], hp=1400.0, max_hp=1400, cubes=0, weapons=["gun"], charging=False, start=20 + i * 60) for i in range(NH)]
tick = 0; t = 0.0; out_zone = 0.0; maxms = 0; tot = 0; stuck = 0; last_dmg = -99; lastpos = list(me["pos"]); kills = 0; labels = {}
pi = 0
while t < 480 and me["hp"] > 0:
    while pi < len(phases) - 1 and t >= phases[pi]["se"]: pi += 1
    ph = phases[pi]
    f = min(1, max(0, (t - ph["ss"]) / (ph["se"] - ph["ss"])))
    cc = [ph["c"][0] + (ph["nc"][0] - ph["c"][0]) * f, ph["c"][1] + (ph["nc"][1] - ph["c"][1]) * f]; cr = ph["r"] + (ph["nr"] - ph["r"]) * f
    inz = math.dist(me["pos"][:2], cc) <= cr
    x, y, z = me["pos"]
    vis = lambda p: math.dist(p[:2], (x, y)) < 200 and nav.los(x, y, z + 8, p[0], p[1], p[2] + 2)
    events = []
    alive_h = [h for h in hunters if h["hp"] > 0]
    obs = dict(type="obs", tick=tick, t=round(t, 2), alive=1 + len(alive_h),
               self=dict(id=0, pos=[round(v, 2) for v in me["pos"]], vel=me["vel"], aim=me["aim"], hp=me["hp"], max_hp=1400, energy=me["energy"], max_energy=100, grounded=True, cubes=me["cubes"], upgrades=[], in_zone=inz, weapons=me["weapons"], stats=obs0["self"]["stats"]),
               mechs=[{k: h[k] for k in ("id", "kind", "pos", "vel", "aim", "hp", "max_hp", "cubes", "weapons", "charging")} for h in alive_h if vis(h["pos"])],
               projectiles=[], crates=[c for c in crates if vis(c["pos"])], cubes=[c for c in cubes if vis(c["pos"])], pickups=[p for p in pickups if vis(p["pos"])],
               sounds=[], beams=[], events=me.pop("events", []), drops=[],
               zone=dict(center=cc, radius=cr, next_center=ph["nc"], next_radius=ph["nr"], shrink_start=ph["ss"], shrink_end=ph["se"], dps=ph["dps"]), offer=None)
    obs = json.loads(json.dumps(obs))
    s = time.perf_counter(); a = bot.act(obs); ms = (time.perf_counter() - s) * 1000
    json.dumps(a); maxms = max(maxms, ms); tot += ms; ALLMS.append(ms)
    if ms > 8 and os.environ.get("DBG"): print("SLOW %.1fms t=%.1f %s prof=%s" % (ms, t, a["debug"]["label"], {k: round(v * 1000, 1) for k, v in PROF.items()}))
    PROF.clear()
    lab = a["debug"]["label"].split()[0]; labels[lab] = labels.get(lab, 0) + 1
    ev = []
    mv = a.get("move", [0, 0]); sp = 19.5 if a.get("boost") and me["energy"] > 1 else 11.7
    for w in me["weapons"]:
        if w and w["kind"] == "laser" and w.get("charge", 0) > 0: sp *= 0.5
    me["energy"] = max(0, me["energy"] - 1.7) if a.get("boost") else min(100, me["energy"] + 1.8)
    me["vel"] = [mv[0] * sp, mv[1] * sp, 0]
    step(me["pos"], me["vel"], 0.1)
    if "aim" in a:
        d = B.angdiff(a["aim"][0], me["aim"][0]); me["aim"] = [me["aim"][0] + max(-0.4, min(0.4, d)), a["aim"][1]]
    if "take" in a:
        for p in pickups:
            if p["id"] == a["take"] and math.dist(p["pos"][:2], me["pos"][:2]) < 4:
                st = dict(cfg["weapons"][p["kind"]]); w = dict(kind=p["kind"], cooldown=1, stats=st)
                if p["kind"] == "laser": w["charge"] = 0
                if p["kind"] == "mortar": me["mortar"] = 1
                sl = a.get("drop", 1 if me["weapons"][1] is None else None)
                if sl is not None:
                    me["weapons"][sl] = w; pickups.remove(p); ev.append(dict(type="took", id=p["id"], kind=p["kind"], slot=sl))
                break
    fire = a.get("fire", [False, False])
    for i, w in enumerate(me["weapons"]):
        if not w: continue
        w["cooldown"] = max(0, w["cooldown"] - 0.1)
        tg = None
        for o in alive_h + crates:
            p = o["pos"]; d = math.dist(p[:2], me["pos"][:2])
            if abs(B.angdiff(math.atan2(p[1] - y, p[0] - x), me["aim"][0])) < max(0.03, 3.0 / max(d, 1)) + (0.2 if w["kind"] == "lightning" else 0) and nav.los(x, y, z + 7, p[0], p[1], p[2] + 2):
                if tg is None or d < tg[1]: tg = (o, d)
        dmg = 0
        if w["kind"] == "gun":
            if w["reload"] > 0:
                w["reload"] -= 0.1
                if w["reload"] <= 0: w["ammo"] = 12
            elif a.get("reload") and w["ammo"] < 12: w["reload"] = 2.4
            elif fire[i] and w["ammo"] > 0 and w["cooldown"] <= 0:
                w["ammo"] -= 1; w["cooldown"] = 0.24
                if w["ammo"] == 0: w["reload"] = 2.4
                if tg and tg[1] < 230 and rng.random() < 0.6: dmg = 45
        elif w["kind"] == "lightning":
            if fire[i] and w["cooldown"] <= 0 and me["energy"] >= 3:
                w["cooldown"] = 0.25; me["energy"] -= 3
                if tg and tg[1] < 45: dmg = 60
        elif w["kind"] == "laser":
            if fire[i] and w["cooldown"] <= 0: w["charge"] = w.get("charge", 0) + 0.1
            elif w.get("charge", 0) > 0:
                if tg and tg[1] < 280: dmg = 315 * min(1, w["charge"] / 0.75)
                w["charge"] = 0; w["cooldown"] = 1.65
        if dmg and tg:
            o = tg[0]; o["hp"] -= dmg; ev.append(dict(type="hit", target=o["id"], amount=dmg, weapon=w["kind"]))
            if o["hp"] <= 0:
                if o in crates: crates.remove(o); cubes.append(dict(id=5000 + len(cubes) + tick, pos=o["pos"]))
                else: kills += 1; ev.append(dict(type="kill", target=o["id"]))
    for c in list(cubes):
        if math.dist(c["pos"][:2], me["pos"][:2]) < 3.5: cubes.remove(c); me["cubes"] += 1
    for h in alive_h:
        if t < h["start"]: continue
        d = math.dist(h["pos"][:2], me["pos"][:2])
        dx, dy = B.norm2(me["pos"][0] - h["pos"][0], me["pos"][1] - h["pos"][1])
        if d > 35: h["vel"] = [dx * 11, dy * 11, 0]
        else: h["vel"] = [-dy * 8, dx * 8, 0]
        step(h["pos"], h["vel"], 0.1)
        if d < 140 and nav.los(h["pos"][0], h["pos"][1], h["pos"][2] + 7, x, y, z + 4):
            if rng.random() < 0.18:
                me["hp"] -= 45; last_dmg = t
                ev.append(dict(type="damaged", amount=45, weapon="gun", bearing=math.atan2(-dy, -dx), by=h["id"]))
    if not inz: me["hp"] -= ph["dps"] * 0.1; out_zone += 0.1; last_dmg = t
    elif t - last_dmg > 5: me["hp"] = min(1400, me["hp"] + 4.5)
    me["events"] = ev
    if tick % 20 == 0:
        if math.dist(lastpos[:2], me["pos"][:2]) < 0.5 and math.hypot(*mv) > 0.5:
            stuck += 1
            if os.environ.get("DBG2"):
                px, py = me["pos"][:2]
                print("roam", bot.roam, "goal", bot.loot_goal, "unst", bot.unstuck_until, bot.unstuck_mode, "zone", cc, cr, ph["nc"], ph["nr"])
                for jj in range(3, -4, -1): print("".join(str(nav.val(px + ii * 3, py + jj * 3)) for ii in range(-6, 7)))
            if os.environ.get("DBG"): print("STUCK t=%.1f pos=%s val=%d mv=%s lab=%s path=%s search=%s unst=%s" % (t, [round(v) for v in me["pos"]], nav.val(*me["pos"][:2]), mv, a["debug"]["label"], bot.path and len(bot.path), bot.search is not None, bot.unstuck_until > t))
        lastpos = list(me["pos"])
    tick += 2; t += 0.1
ALLMS.sort(); print("p99 %.2f p99.9 %.2f" % (ALLMS[int(len(ALLMS)*0.99)], ALLMS[int(len(ALLMS)*0.999)]))
print("seed %d end t=%.0f hp=%.0f kills=%d cubes=%d weapons=%s out_zone=%.1fs stuck_s=%d avg=%.2fms max=%.1fms %s" % (seed, t, me["hp"], kills, me["cubes"], [w and w["kind"] for w in me["weapons"]], out_zone, stuck, tot / max(1, tick / 2), maxms, labels))
