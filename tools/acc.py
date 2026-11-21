import sys, json, gzip, collections, math
sys.path.insert(0, 'tools')
import arena_sim as A
K, n = sys.argv[1], int(sys.argv[2])
hello = json.loads(gzip.open(K, 'rt').readline())
mod = A.load_mod('./bot.py', 'pb')
C = collections.Counter()
for seed in range(1, n + 1):
    sim = A.Sim(hello, mod, seed)
    pl = sim.player
    od = sim.damage
    def dmg(tgt, amount, src, by, weapon, effect=None, od=od):
        if by is not None and by['player']: C['hit_' + weapon] += 1; C['dmg_' + weapon] += amount
        od(tgt, amount, src, by, weapon, effect)
    sim.damage = dmg
    of = sim.fire_weapons
    def fw(m, fire, of=of, sim=sim):
        if m['player']:
            pre = [(w['kind'], w.get('ammo'), w['cooldown'], w.get('charge', 0)) if w else None for w in m['weapons']]
            of(m, fire)
            for p, w in zip(pre, m['weapons']):
                if not p or not w: continue
                if p[0] == 'gun' and w['ammo'] is not None and p[1] is not None and w['ammo'] < p[1]: C['shot_gun'] += 1
                if p[0] == 'laser' and p[3] > 0 and w['charge'] == 0: C['shot_laser'] += 1; C['lcharge'] += p[3]
                if p[0] == 'lightning' and w['cooldown'] > p[2]: C['shot_lightning'] += 1
            lab = (m['cmd'].get('debug') or {}).get('label', '').split()[0] if m['cmd'] else ''
            if lab in ('fight', 'arm'):
                C['fight_ticks'] += 1
                for w in m['weapons']:
                    if w: C['have_' + w['kind']] += 1
        else:
            of(m, fire)
    sim.fire_weapons = fw
    r = sim.run()
    C['kills'] += r['kills']; C['t'] += r['t']
print({k: round(v / n, 1) for k, v in sorted(C.items())})
for w in ('gun', 'laser', 'lightning'):
    if C['shot_' + w]: print(w, 'hit rate %.2f' % (C['hit_' + w] / C['shot_' + w]), 'shots per fight-sec-with-weapon %.2f' % (C['shot_' + w] / max(1, C['have_' + w]) * 20))
