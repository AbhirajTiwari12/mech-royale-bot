# mech-royale-bot

A bot for the `mechfight/1` protocol (16-mech battle royale). Python, standard library only.

- `bot.py` - the bot: reads observations on stdin, writes actions on stdout.
- `nav.bin` - precomputed navigation data for the arena (walk grid + surface heights).
- `tools/build_nav.py` - regenerates `nav.bin` from the kit's `arena/` folder.
- `tools/sim.py` - rough kinematic smoke test (not the real game).

## Build and check

```sh
docker build --platform linux/amd64 -t mech-bot .
python3 check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "$PWD/arena:/arena:ro" mech-bot
```

## Behaviour

Zone safety first, then retreat to cover when hurt, fight when engaged, otherwise loot and roam.
Legs (movement) and torso (aim/fire) are decided independently.
