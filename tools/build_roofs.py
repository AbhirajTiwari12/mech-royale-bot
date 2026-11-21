"""Offline: find climbable flat roofs in nav.bin and write roofs.json.
usage: python3 tools/build_roofs.py nav.bin roofs.json"""
import sys, os, json, math, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot as B
nav = B.Nav(sys.argv[1])
nnx, nny, nv = nav.nnx, nav.nny, nav.nav
comp = {}
roofs = []
seen = bytearray(nnx * nny)
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
for s in range(nnx * nny):
    if nv[s] != 2 or seen[s]:
        continue
    cells = []
    q = collections.deque([s]); seen[s] = 1
    while q:
        c = q.popleft(); cells.append(c)
        ci, cj = c % nnx, c // nnx
        for di, dj in N4:
            a, b = ci + di, cj + dj
            if 0 <= a < nnx and 0 <= b < nny and nv[b * nnx + a] == 2 and not seen[b * nnx + a]:
                seen[b * nnx + a] = 1; q.append(b * nnx + a)
    if len(cells) < 40:
        continue
    cs = set(cells)
    # inset: BFS distance from the component boundary
    inset = {}
    q = collections.deque()
    for c in cells:
        ci, cj = c % nnx, c // nnx
        if any((cj + dj) * nnx + ci + di not in cs for di, dj in N4):
            inset[c] = 1; q.append(c)
    while q:
        c = q.popleft(); ci, cj = c % nnx, c // nnx
        for di, dj in N4:
            d = (cj + dj) * nnx + ci + di
            if d in cs and d not in inset:
                inset[d] = inset[c] + 1; q.append(d)
    camp = max(cells, key=lambda c: inset[c])
    if inset[camp] < 3:
        continue
    cx, cy = nav.center(camp)
    rz = nav.surf_at(cx, cy)
    zs = [nav.surf_at(*nav.center(c)) for c in cells]
    if max(zs) - min(zs) > 3.0:
        continue
    # access pairs: ground main cell g -> roof cell r through blocked cells only
    acc = []
    for c in cells:
        if inset[c] != 1:
            continue
        ci, cj = c % nnx, c // nnx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            for k in range(1, 6):
                a, b = ci + di * k, cj + dj * k
                if not (0 <= a < nnx and 0 <= b < nny):
                    break
                v = nv[b * nnx + a]
                if v == 2:
                    break
                if v == 1:
                    if k < 2:
                        break
                    gx, gy = nav.center(b * nnx + a)
                    rx, ry = nav.center(c)
                    gz = nav.surf_at(gx, gy)
                    H = rz - gz
                    ok = 6.0 <= H <= 38.0
                    n = int(math.hypot(rx - gx, ry - gy))
                    up = False
                    for i in range(n + 1):
                        t = i / max(1, n)
                        z = nav.surf_at(gx + (rx - gx) * t, gy + (ry - gy) * t)
                        if z > rz + 1.0:
                            ok = False
                        if z > gz + 2.0:
                            up = True
                            if z < rz - 1.5:
                                ok = False  # intermediate ledge
                        elif up:
                            ok = False
                    if ok and all(math.hypot(gx - q_[0], gy - q_[1]) > 10 for q_ in acc):
                        acc.append([round(gx, 1), round(gy, 1), round(rx, 1), round(ry, 1), round(gz, 1)])
                    break
    if not acc:
        continue
    step = max(1, len(cells) // 70)
    pts = [[round(nav.center(c)[0], 1), round(nav.center(c)[1], 1), inset[c]] for c in cells[::step]]
    rad = max(math.hypot(nav.center(c)[0] - cx, nav.center(c)[1] - cy) for c in cells)
    roofs.append(dict(z=round(rz, 1), camp=[round(cx, 1), round(cy, 1)], inset=inset[camp], rad=round(rad, 1), n=len(cells), cells=pts, acc=acc))
json.dump(roofs, open(sys.argv[2], "w"), separators=(",", ":"))
print(len(roofs), "roofs", file=sys.stderr)
for r in roofs:
    print(r["camp"], "z", r["z"], "H", round(r["z"] - r["acc"][0][4], 1), "inset", r["inset"], "n", r["n"], "acc", len(r["acc"]), file=sys.stderr)
