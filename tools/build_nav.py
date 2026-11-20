"""Offline: build nav.bin from the arena map (v2/world.json + v2/terrain.i16).

usage: python3 tools/build_nav.py <arena_dir> <out nav.bin>

Layout of nav.bin (little-endian):
  header json line (utf-8, '\n' terminated)
  surf: int16[sny*snx]  walk/occlusion surface height in decimeters on a 2 m grid (max pooled)
  nav : uint8[nny*nnx]  3 m grid: 0 blocked, 1 main walkable component, 2 other walkable (roofs...)
"""
import array, json, sys, collections

arena, out = sys.argv[1], sys.argv[2]
w = json.load(open(arena + "/v2/world.json"))
T = w["terrain"]
nx, ny, minX, minY = T["nx"], T["ny"], T["minX"], T["minY"]
ter = array.array("h")
ter.frombytes(open(arena + "/v2/terrain.i16", "rb").read())
if sys.byteorder != "little":
    ter.byteswap()
surf = [v / 100.0 for v in ter]
HEAD = 9.0

for p in w["prisms"]:
    base, top = p["base"], p["top"]
    edges = []
    for ring in [p["ring"]] + list(p.get("holes") or []):
        n = len(ring) // 2
        for i in range(n):
            x0, y0 = ring[2 * i], ring[2 * i + 1]
            x1, y1 = ring[2 * ((i + 1) % n)], ring[2 * ((i + 1) % n) + 1]
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    if not edges:
        continue
    ylo = min(min(e[1], e[3]) for e in edges)
    yhi = max(max(e[1], e[3]) for e in edges)
    for j in range(max(0, int(ylo - minY)), min(ny - 1, int(yhi - minY) + 1) + 1):
        y = minY + j
        xs = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= y < y1) or (y1 <= y < y0):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for a in range(0, len(xs) - 1, 2):
            i0 = max(0, int(xs[a] - minX + 0.999))
            i1 = min(nx - 1, int(xs[a + 1] - minX))
            for i in range(i0, i1 + 1):
                k = j * nx + i
                g = ter[k] / 100.0
                if base <= g + HEAD and top > surf[k]:
                    surf[k] = top

# bad samples: a step/slope a mech cannot walk
STEP = 1.2
bad = bytearray(nx * ny)
for j in range(ny):
    for i in range(nx):
        k = j * nx + i
        v = surf[k]
        if (i + 1 < nx and abs(v - surf[k + 1]) > STEP) or (j + 1 < ny and abs(v - surf[k + nx]) > STEP):
            bad[k] = 1
            if i + 1 < nx and abs(v - surf[k + 1]) > STEP:
                bad[k + 1] = 1
            if j + 1 < ny and abs(v - surf[k + nx]) > STEP:
                bad[k + nx] = 1

B = w["bounds"]
NC = 3.0
nnx = int((nx - 1) / NC) + 1
nny = int((ny - 1) / NC) + 1
nav = bytearray(b"\x02" * (nnx * nny))
R = 3.1
for cj in range(nny):
    for ci in range(nnx):
        x = minX + (ci + 0.5) * NC
        y = minY + (cj + 0.5) * NC
        if x < B["minX"] + 4 or x > B["maxX"] - 4 or y < B["minY"] + 4 or y > B["maxY"] - 4:
            nav[cj * nnx + ci] = 0
off = [(di, dj) for di in range(-4, 5) for dj in range(-4, 5)]
for j in range(ny):
    for i in range(nx):
        if bad[j * nx + i]:
            x = minX + i
            y = minY + j
            c0i = int((x - minX) / NC)
            c0j = int((y - minY) / NC)
            for di in (-2, -1, 0, 1, 2):
                for dj in (-2, -1, 0, 1, 2):
                    ci, cj = c0i + di, c0j + dj
                    if 0 <= ci < nnx and 0 <= cj < nny:
                        cx = minX + (ci + 0.5) * NC
                        cy = minY + (cj + 0.5) * NC
                        if (cx - x) ** 2 + (cy - y) ** 2 <= R * R:
                            nav[cj * nnx + ci] = 0

# components (8-connected, no corner cutting)
comp = [0] * (nnx * nny)
sizes = {}
cid = 0
for s in range(nnx * nny):
    if nav[s] and not comp[s]:
        cid += 1
        comp[s] = cid
        q = collections.deque([s])
        n = 0
        while q:
            c = q.popleft()
            n += 1
            ci, cj = c % nnx, c // nnx
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                a, b = ci + di, cj + dj
                if 0 <= a < nnx and 0 <= b < nny:
                    d = b * nnx + a
                    if nav[d] and not comp[d]:
                        if di and dj and not (nav[cj * nnx + a] and nav[b * nnx + ci]):
                            continue
                        comp[d] = cid
                        q.append(d)
        sizes[cid] = n
main = max(sizes, key=sizes.get)
for s in range(nnx * nny):
    if nav[s]:
        nav[s] = 1 if comp[s] == main else 2

# surface on 2 m grid, max pooled, decimeters
SC = 2.0
snx = (nx + 1) // 2
sny = (ny + 1) // 2
s2 = array.array("h", [0]) * (snx * sny)
for sj in range(sny):
    for si in range(snx):
        m = -1e9
        for j in (2 * sj, 2 * sj + 1):
            for i in (2 * si, 2 * si + 1):
                if i < nx and j < ny:
                    v = surf[j * nx + i]
                    if v > m:
                        m = v
        s2[sj * snx + si] = int(round(m * 10))
if sys.byteorder != "little":
    s2.byteswap()
hdr = {"minX": minX, "minY": minY, "snx": snx, "sny": sny, "sc": SC, "nnx": nnx, "nny": nny, "nc": NC}
with open(out, "wb") as f:
    f.write((json.dumps(hdr) + "\n").encode())
    f.write(s2.tobytes())
    f.write(bytes(nav))
tot = nnx * nny
print(hdr, "main", sizes[main], "other", sum(sizes.values()) - sizes[main], "blocked", tot - sum(sizes.values()), "comps", len(sizes), file=sys.stderr)
# ascii preview
if len(sys.argv) > 3:
    for cj in range(nny - 1, -1, -2):
        print("".join(" .#"[0 if nav[cj * nnx + ci] == 1 else (1 if nav[cj * nnx + ci] == 2 else 2)] for ci in range(0, nnx, 2)))
