"""Flag sync core: find local flag changes, apply the peer's, never echo.

A flag "view" is a dict of logical arrays of u16 words (story, item, scene, dungeon, tbox: see
game_side.FLAG_ARRAYS). A change is (array, index, set_mask, clear_mask): the bits that turned on and off. The
receiver replays exactly those bits on its own word, so two players setting different bits of one word both keep
theirs.

Clears (CLEAR_POLICY): story and item always. Scene, dungeon and tbox words switch between the save copy and the
live array at every area change, so a not-yet-committed word could look like a clear. Scene and chest flags only
ever need sets. Dungeon flags hold the small key counter, which goes down when a key is used, so their clears do
travel, but only for the current scene's entry and only when the scene did not change since the last poll.

After a load (the view was unavailable, then comes back) the player may have loaded an older save: flags that
went off then are not clears, so only set bits are sent, and the peer is asked for a full snapshot again
(RESYNC) so lost progress comes back. Progress is only ever added across a load.
"""

# Real play clears a flag or two at a time. More cleared words than this in one poll means the game state was
# wiped or replaced (reset, reload) without us seeing a load: treat it as a load (no clears, RESYNC).
MASS_CLEAR_WORDS = 2

# QA mutation twins (mod/qa/coop_qa.py --mutant): a deliberately broken bridge the QA run must catch. Unset in play.
MUTANT = __import__("os").environ.get("COOP_MUT", "")
if MUTANT == "no_mass_clear_brake":
    MASS_CLEAR_WORDS = 1 << 30

ARRAY_IDS = {"story": 0, "item": 1, "scene": 2, "dungeon": 3, "tbox": 4,
             "resync": 255}  # resync: control entry, not an array
RESYNC = ("resync", 0, 0, 0)
ARRAY_NAMES = {v: k for k, v in ARRAY_IDS.items()}
CLEAR_POLICY = {"story": "always", "item": "always", "scene": "never", "dungeon": "current", "tbox": "never"}
ENTRY_WORDS = {"scene": 8, "dungeon": 8, "tbox": 2}  # words per scene entry in the scene-indexed arrays


def merge(a, b):
    """Combine change a followed by change b on the same word into one change."""
    set1, clear1 = a
    set2, clear2 = b
    return ((set1 & ~clear2) | set2) & 0xFFFF, ((clear1 & ~set2) | clear2) & 0xFFFF


class ChangeQueue:
    """Pending outgoing changes, merged per (array, index), in first-seen order."""

    def __init__(self):
        self.items = {}

    def add(self, name, idx, set_mask, clear_mask):
        key = (name, idx)
        self.items[key] = merge(self.items[key], (set_mask, clear_mask)) if key in self.items else (set_mask, clear_mask)
        if self.items[key] == (0, 0) and name != "resync":
            del self.items[key]

    def take(self, n):
        keys = list(self.items)[:n]
        out = [(k[0], k[1], *self.items.pop(k)) for k in keys]
        return out

    def __len__(self):
        return len(self.items)


class FlagSync:
    """side must provide flag_view() -> dict or None, and flag_write(name, index, value)."""

    def __init__(self, side):
        self.side = side
        self.baseline = None
        self.ready = False        # view was available at the last poll
        self.just_loaded = False  # set by poll() after a load; the bridge sends RESYNC and clears it
        self.last_scene = None    # side.scene_index at the last poll

    def _clear_ok(self, name, i, scene, same_scene):
        policy = CLEAR_POLICY[name]
        if policy == "always":
            return True
        if policy == "current":
            return same_scene and scene is not None and i // ENTRY_WORDS[name] == scene
        return False

    def poll(self):
        """Local changes since the last poll (or since the last applied remote change)."""
        view = self.side.flag_view()
        if view is None:
            self.ready = False
            return []
        scene = getattr(self.side, "scene_index", None)
        same_scene = scene == self.last_scene
        self.last_scene = scene
        if self.baseline is None:
            self.baseline = {k: list(v) for k, v in view.items()}
            self.ready = True
            return []
        after_load = not self.ready
        self.ready = True
        if not after_load:
            cleared_words = sum(1 for name, words in view.items()
                                for i, (old, new) in enumerate(zip(self.baseline[name], words))
                                if old & ~new and self._clear_ok(name, i, scene, same_scene))
            after_load = cleared_words > MASS_CLEAR_WORDS
        if after_load:
            self.just_loaded = True
        changes = []
        for name, words in view.items():
            base = self.baseline[name]
            for i, (old, new) in enumerate(zip(base, words)):
                if old == new:
                    continue
                clear_ok = not after_load and self._clear_ok(name, i, scene, same_scene)
                set_mask = new & ~old & 0xFFFF
                clear_mask = old & ~new & 0xFFFF if clear_ok else 0
                # Scene: keep unsent cleared bits in the baseline (a stale word must not re-send them later).
                # Others: follow the game; a bit that comes back later is just sent again (OR is idempotent).
                base[i] = old | new if CLEAR_POLICY[name] == "never" and name == "scene" else new
                if set_mask or clear_mask:
                    changes.append((name, i, set_mask, clear_mask))
        return changes

    def snapshot(self):
        """Every set bit right now, as changes: what a (re)connecting peer needs (merged with OR, no clears).
        Reads a fresh view and leaves the baseline alone, so unpolled local clears are not lost."""
        view = self.side.flag_view()
        if view is None:
            return None
        if self.baseline is None:
            self.baseline = {k: list(v) for k, v in view.items()}
            self.ready = True
        return [(name, i, w, 0) for name, words in view.items() for i, w in enumerate(words) if w]

    def apply(self, changes):
        """Apply the peer's changes. Returns False (nothing applied) if the game is not ready (loading)."""
        view = self.side.flag_view()
        if view is None:
            return False
        if self.baseline is None:
            self.baseline = {k: list(v) for k, v in view.items()}
        for name, idx, set_mask, clear_mask in changes:
            if name not in view or not 0 <= idx < len(view[name]):  # also skips RESYNC entries
                continue
            cur = view[name][idx]
            new = (cur | set_mask) & ~clear_mask & 0xFFFF
            if new != cur and MUTANT != "no_flag_apply":
                self.side.flag_write(name, idx, new)
                view[name][idx] = new
            # Move the baseline by the remote bits only: local changes not yet polled still show up as changes.
            base = self.baseline[name]
            base[idx] = (base[idx] | set_mask) & ~clear_mask & 0xFFFF
        return True
