"""Enemy sync core: an enemy one player defeats is gone for the other player too, while both are in the same area.

The game keeps defeated enemies as bits in EnemyflagManager (4096 u16 words, id = (room << 10) | placement index,
d_stage_parse.cpp getEnemyDefeatFlagFromId) and wipes them whenever the player enters another scene, which is why
enemies come back. So these bits only mean something together with the area they were set in:

- Each side sends the non-zero words of its own bits with its stage name, repeatedly (no acks: a lost message is
  replaced by the next one, and OR-ing the same bits twice changes nothing).
- The receiver ORs them in only while it is in the same stage and has been there for SETTLE seconds (so bits from
  the area the peer just left can never land in a fresh one). Bits are never cleared by sync; the game clears them
  itself on the next area change.
- Newly received bits also go to the puppet's kill list, so enemies that are alive right now disappear
  (d_a_coop_puppet.cpp applyRemoteKills). Setting the bits alone only stops them from spawning again.
"""

SEND_EVERY = 0.5        # resend everything this often
POLL_EVERY = 0.2        # look for new local kills this often (a change is sent at once)
SETTLE = 1.0            # seconds in the same stage before bits are sent or applied
MAX_ENTRIES = 300       # (index u16, bits u16) pairs per message: ~1.2 KB
KILL_SLOTS = 8          # struct Shared::kills


class EnemySync:
    def __init__(self, side):
        self.side = side
        self.stage = None
        self.stage_since = 0.0
        self.words = None            # own bits at the last poll
        self.next_poll = 0.0
        self.next_send = 0.0
        self.pending_kills = []      # flag ids for the puppet, oldest first

    def _settled(self, now):
        return self.stage is not None and now - self.stage_since >= SETTLE

    def _refresh(self, now):
        """Read the own stage and bits. Returns True if the bits changed since the last read."""
        stage = self.side.enemy_stage()
        if stage != self.stage:
            self.stage, self.stage_since = stage, now
            self.words, self.pending_kills = None, []
        if stage is None:
            return False
        words = self.side.enemy_words()
        if words is None:
            self.stage, self.words = None, None
            return False
        changed = self.words is not None and words != self.words
        self.words = words
        return changed

    def poll(self, now):
        """(stage, [(index, bits), ...]) to send now, or None."""
        if now < self.next_poll:
            return None
        self.next_poll = now + POLL_EVERY
        changed = self._refresh(now)
        if not self._settled(now) or self.words is None:
            return None
        entries = [(i, w) for i, w in enumerate(self.words) if w]
        if not entries or not (changed or now >= self.next_send):
            return None
        self.next_send = now + SEND_EVERY
        return self.stage, entries[:MAX_ENTRIES]

    def apply(self, stage, entries, now):
        """OR the peer's bits in if we are settled in the same stage. Returns the number of new bits."""
        self._refresh(now)
        if stage != self.stage or not self._settled(now) or self.words is None:
            return 0
        new_bits = 0
        for i, bits in entries:
            if not 0 <= i < len(self.words):
                continue
            new = bits & ~self.words[i] & 0xFFFF
            if not new:
                continue
            self.side.enemy_write(i, self.words[i] | new)
            self.words[i] |= new
            for b in range(16):
                if new & (1 << b):
                    self.pending_kills.append(i * 16 + b)
                    new_bits += 1
        self.pump_kills()
        return new_bits

    def pump_kills(self):
        """Hand the next batch of new remote kills to the puppet once it has taken the previous one."""
        if self.pending_kills and self.side.kills_taken():
            batch, self.pending_kills = self.pending_kills[:KILL_SLOTS], self.pending_kills[KILL_SLOTS:]
            self.side.write_kills(batch)
