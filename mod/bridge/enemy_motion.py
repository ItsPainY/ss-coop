"""Enemy motion: each living enemy is run by one game (its owner); the other game shows it at the owner's position.

Rules from coop-anything/playbook/actor-ownership.md, cut down to two players:
- The host decides every owner: the player whose Link is nearer to the enemy (the host's copy of it). An owner is
  kept at least HOLD seconds and replaced only by a player CHALLENGE times nearer, so two players at about the same
  distance do not make it flap.
- Every change of owner takes a new epoch from one counter that only goes up (also across area changes), and OWN
  messages carry (enemy, owner, epoch). The joiner takes an entry only if its epoch is newer than what it has, so a
  replayed or reordered old assignment changes nothing.
- MOTION (position, facing, health of each enemy the sender has) is used for following only if the sender is the
  owner at exactly the epoch we know. Older epochs (a past owner still sending) and unknown enemies are dropped.
- Health is merged without ownership: both games take the lower value (health only goes down), so hits from either
  player count.
- Handoff: the old owner's last pose is already in the new owner's game, because it was following until then, and
  pose is all the state that is synced. So the new owner simply stops getting FOLLOW orders; no Transfer/Ack/Commit
  handshake is needed for this state.
- Everything only within one stage, after SETTLE seconds there, like enemy_sync.py.
- Animation (enemy types the puppet knows, see sEnemyAnimTypes): MOTION also carries the owner's current animation
  (index in the enemy's animation file), play mode and frame; the follower's copy switches to it and jumps to the
  frame each time a new one arrives (frame_seq), playing on by itself in between.

The game side (mod/puppet: updateEnemies) lists this game's enemies and applies the orders written here.
"""

import math

HOLD = 1.5               # seconds an owner is kept at least
CHALLENGE = 0.75         # a challenger must be nearer than this fraction of the owner's distance
SETTLE = 1.0
OWN_EVERY = 0.5          # resend the ownership table this often (and at once on a change)
MOTION_EVERY = 1 / 15
FOLLOW_FRESH = 0.5       # follow the owner's pose only if heard within this time
HEALTH_FRESH = 2.0
HOST, JOIN = 0, 1
FAR = float("inf")
NO_HEALTH = 0xFFFF
NO_ANIM = 0xFFFF
ORDER_FOLLOW = 1
ORDER_ANIM = 2
MAX_ENEMIES = 32


def dist(a, b):
    return math.dist(a, b) if a is not None and b is not None else FAR


class EnemyMotion:
    def __init__(self, side, role, mutant=""):
        self.side = side
        self.me = HOST if role == "host" else JOIN
        self.mutant = mutant
        self.stage = None
        self.stage_since = 0.0
        self.table = []            # own enemies: dicts objId, profile, pos, rot, health
        self.owners = {}           # objId -> (owner, epoch)  (host: decided here; join: from OWN)
        self.since = {}            # host: objId -> time the current owner got it
        self.epoch_counter = 0     # host only; never reset
        self.peer = {}             # objId -> dict(epoch, owner, pos, rot, health, t)
        self.next_own = 0.0
        self.next_motion = 0.0
        self.own_dirty = False
        self.frame_seq = {}        # objId -> counter of accepted owner frames (u8)

    # --- stage ---
    def _stage_tick(self, now):
        stage = self.side.enemy_stage()
        if stage != self.stage:
            self.stage, self.stage_since = stage, now
            self.owners, self.since, self.peer, self.table, self.frame_seq = {}, {}, {}, [], {}
            self.own_dirty = True
        return stage is not None and now - self.stage_since >= SETTLE

    # --- host: decide owners ---
    def _decide(self, now, my_link, peer_link):
        for e in self.table:
            oid = e["objId"]
            d = {HOST: dist(e["pos"], my_link), JOIN: dist(e["pos"], peer_link)}
            nearest = min(d, key=d.get)
            cur = self.owners.get(oid)
            if cur is None:
                self._grant(oid, nearest, now)
            elif cur[0] != nearest and now - self.since.get(oid, now) >= HOLD and d[nearest] < CHALLENGE * d[cur[0]]:
                self._grant(oid, nearest, now)

    def _grant(self, oid, owner, now):
        self.epoch_counter += 1
        self.owners[oid] = (owner, self.epoch_counter)
        self.since[oid] = now
        self.own_dirty = True

    # --- messages ---
    def on_own(self, stage, entries, now):
        """Joiner: ownership entries (objId, owner, epoch) from the host."""
        if self.me == HOST or stage != self.stage:
            return
        for oid, owner, epoch in entries:
            known = self.owners.get(oid)
            if known is None or epoch > known[1] or self.mutant == "no_epoch":
                self.owners[oid] = (owner, epoch)

    def on_motion(self, stage, entries, now):
        """Peer's enemies: (objId, epoch, pos, rot, health, anim, mode, frame)."""
        if stage != self.stage:
            return
        sender = JOIN if self.me == HOST else HOST
        for oid, epoch, pos, rot, health, *anim in entries:
            p = self.peer.setdefault(oid, {})
            p["health"], p["health_t"] = health, now
            known = self.owners.get(oid)
            if known is None or known[0] != sender:
                continue                      # not the owner (or unknown yet): health only
            if epoch != known[1] and self.mutant != "no_epoch":
                continue                      # stale (past owner) or ahead of our view
            p.update(epoch=epoch, pos=pos, rot=rot, t=now, anim=tuple(anim) if anim else (NO_ANIM, 0, 0.0))
            self.frame_seq[oid] = (self.frame_seq.get(oid, 0) + 1) & 0xFF

    # --- every tick ---
    def tick(self, now, my_link, peer_link, peer_stage):
        """Returns messages to send: list of (kind, stage, entries), kind 'own' or 'motion'."""
        out = []
        if not self._stage_tick(now):
            return out
        table = self.side.enemy_table()
        if table is None:
            return out
        self.table = table[:MAX_ENEMIES]
        same = peer_stage == self.stage
        if self.me == HOST:
            self._decide(now, my_link, peer_link if same else None)
            if self.owners and (self.own_dirty or now >= self.next_own):
                self.next_own, self.own_dirty = now + OWN_EVERY, False
                out.append(("own", self.stage, [(oid, o, ep) for oid, (o, ep) in self.owners.items()]))
        if same and now >= self.next_motion and self.table:
            self.next_motion = now + MOTION_EVERY
            entries = []
            for e in self.table:
                ep = self.owners.get(e["objId"], (None, 0))[1]
                entries.append((e["objId"], ep, e["pos"], e["rot"], e["health"], e.get("anim", NO_ANIM),
                                e.get("mode", 0), e.get("frame", 0.0)))
            out.append(("motion", self.stage, entries))
        self.side.write_enemy_orders(self.orders(now))
        return out

    def orders(self, now):
        """Orders for this game's enemies: follow the owner's pose if the other game owns it, health cap."""
        other = JOIN if self.me == HOST else HOST
        out = []
        for e in self.table:
            oid = e["objId"]
            p = self.peer.get(oid, {})
            cap = p["health"] if now - p.get("health_t", -1e9) <= HEALTH_FRESH else NO_HEALTH
            owner = self.owners.get(oid, (None, 0))[0]
            follow = owner == other and now - p.get("t", -1e9) <= FOLLOW_FRESH
            if follow:
                anim, mode, frame = p.get("anim", (NO_ANIM, 0, 0.0))
                flags = ORDER_FOLLOW | (ORDER_ANIM if anim != NO_ANIM and self.mutant != "no_enemy_anim" else 0)
                out.append((oid, p["rot"], p["pos"], cap, flags, (anim, mode, self.frame_seq.get(oid, 0), frame)))
            elif cap != NO_HEALTH and cap < e["health"]:
                out.append((oid, e["rot"], e["pos"], cap, 0, (NO_ANIM, 0, 0, 0.0)))
        return out
