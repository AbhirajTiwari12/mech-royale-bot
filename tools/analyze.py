import sys, json, gzip, collections, math
sys.path.insert(0, 'tools')
import arena_sim as A
botdir, K, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
hello = json.loads(gzip.open(K, 'rt').readline())
mod = A.load_mod(botdir + '/bot.py', 'pb')
taken = collections.Counter(); dealt = collections.Counter(); hunters_at_death = collections.Counter(); wp = collections.Counter(); shots = collections.Counter(); seers = []
lab = collections.Counter(); deathlab = collections.Counter(); hp_e = []
for seed in range(1, n + 1):
    sim = A.Sim(hello, mod, seed)
    od = sim.damage
    def dmg(tgt, amount, src, by, weapon, effect=None, od=od, sim=sim):
        if by is not None:
            if tgt['player']: taken[(by['tier'], weapon)] += amount
            else: dealt[weapon] += amount
        od(tgt, amount, src, by, weapon, effect)
    sim.damage = dmg
    r = sim.run()
    hunters_at_death[len([h for h in sim.hunters if sim.mechs[h]['alive']])] += 1
    wp[tuple(sorted(w['kind'] for w in sim.player['weapons'] if w))] += 1
    for k, v in r['labels'].items(): lab[k] += v
    alive = [m for m in sim.house if m['alive']]
    seers.append(sum(1 for m in alive if m['brain']['seen_t'] >= 0))
    hp_e.append(sum(m['hp'] for m in alive if m['id'] in sim.hunters) / max(1, len([m for m in alive if m['id'] in sim.hunters])))
print('taken', {k: round(v / n) for k, v in taken.most_common()})
print('dealt', {k: round(v / n) for k, v in dealt.most_common()})
print('hunters at death', sorted(hunters_at_death.items()))
print('weapons at death', wp.most_common())
print('labels', lab.most_common())
print('seers at death avg', sum(seers) / n, 'avg hunter hp at death', sum(hp_e) / n)
