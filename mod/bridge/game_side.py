"""One Dolphin running the modded game: read its Link, write its puppet mailbox.

Mailbox layout: struct Shared in mod/puppet/d_a_coop_puppet.h. The hook block is found from the branch the
Gecko hook puts at dScGame_c::execute, not assumed.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import ss_mem as m  # noqa: E402
from held_models import HELD_INDEX  # noqa: E402

HOOK_SITE = 0x801BA700          # dScGame_c::execute
HOOK_FIRST_WORD = 0x9421FFC0    # stwu r1,-0x40(r1): first instruction of our hook code
BLOCK_OFFSET = 0x1C             # shared block starts after the hook's 7-instruction prologue

MAIL_SEQ, MAIL_FLAGS, MAIL_POS, MAIL_TUNIC, MAIL_ANIM_DATA = 0x18, 0x1C, 0x20, 0x2E, 0x40
MAIL_ANIM = 4                    # anim[2] (u16) and frame[2] (f32) at MAIL_ANIM_DATA are valid
TUNIC_TYPE = 0x120B              # daPlayerModelBase_c::mTunicType (0 tunic, 1 Skyloft clothes, 2 Silent Realm)
ANIMATIONS = 0x121A              # daPlayerModelBase_c::mAnimations[6] (u16, sAnimations index; 443 = none)
ANM_CHRS = 0x728                 # daPlayerModelBase_c::mAnmChrs[6], 0x38 bytes each
ANM_CHR_SIZE = 0x38
ANM_FRAME = 0x30                 # m3d::fanm_c::mCurrentFrame (f32)
MAIL_BRIDGE, MAIL_VISIBLE = 1, 2
MAIL_EQUIP = 8                   # equipment at MAIL_EQUIP_DATA is valid
MAIL_EQUIP_DATA = 0x60           # struct Shared::equip (EQUIP layout below, 76 bytes)

# Equipment (sword, sheath, shield). daPlayerModelBase_c keeps them as m3d::smdl_c members of the player
# (d_player_mdl.h); each m3d::scnLeaf_c holds its nw4r::g3d::ScnObj at +0x14. A ScnObj is in this frame's draw
# list exactly when its G3dObj parent (+0x4) is set: m3d::scnLeaf_c::entry() pushes it into the scene root,
# which is cleared every frame (seen 2026-10-25: head/face/hands always have a parent, the sword of a Link
# without a sword has none). MTX_LOCAL at ScnObj+0xC, MTX_WORLD at +0x3C.
EQUIP_MODELS = (0x598, 0x654, 0x5E0)   # mSwordMdl, mSheathMdl, mShieldMdl
BODY_MODEL = 0x3E0                     # mMainMdl: the item matrices are sent relative to its local matrix
SCN_LEAF, G3D_PARENT, MTX_LOCAL, MTX_WORLD = 0x14, 0x04, 0x0C, 0x3C
CURRENT_SWORD = 0x80571C74             # daPlayerActBase_c::sCurrentSword (u8, getSwordName index; 6 = none)
SHIELD_SLOT = 0x53B1                   # SaveFile: equipped shield's pouch slot (FileManager::getShieldPouchSlot)
POUCH_ITEMS = 0x7C0                    # SaveFile: pouch, 8 x u32 (FileManager::getPouchData), item id in low 16 bits
ITEM_WOODEN_SHIELD, SHIELD_IDS = 116, 10  # shields are items 116..125 (isItemShield); shield id = item - 116
NO_SHIELD = 0xFF
# Wire and mailbox layout: drawn bits (1 sword, 2 sheath, 4 shield), sword index, shield id, pad, then per model
# a 3x4 matrix relative to the body as s16: rotation x ROT_SCALE, translation x POS_SCALE.
EQUIP = struct.Struct(">BBBx36h")
ROT_SCALE, POS_SCALE = 8192.0, 16.0
NO_EQUIP = bytes(EQUIP.size)

# Held items: three more m3d::smdl_c in dAcPy_c (+0x169C, +0x16B8, +0x16D4, next to mHeldResFile +0x16F0), loaded
# from PLCompItem by getItemResFile like the sword. Sent as: drawn bits, sHeldModels index per slot (0xFF none,
# from the model's ResMdl name), and the same body-relative matrices as EQUIP.
HELD_MODEL_SLOTS = (0x169C, 0x16B8, 0x16D4)
MAIL_HELD = 16
MAIL_HELD_DATA = 0xD0
HELD = struct.Struct(">B3B36h")
NO_HELD = bytes([0, 0xFF, 0xFF, 0xFF]) + bytes(72)
RESMDL_NAME = 0x48               # ResMdlData: offset of the model name, relative to the model

# Enemy defeat flags (EnemyflagManager): live array and the committed copy in the SaveFile, which is what room
# loading checks (EnemyflagManager::checkFlag reads FileManager::getEnemyDefeatFlagsConst = SaveFile+0x32F4).
ENEMY_LIVE = 0x805A7AD8          # EnemyflagManager::sEnemyDefeatFlags
ENEMY_SAVE = 0x32F4              # SaveFile::getEnemyFlags0/1
ENEMY_WORDS = 0x1000
MAIL_KILL_SEQ, MAIL_KILL_ACK, MAIL_KILLS, KILL_SLOTS = 0xAC, 0xB0, 0xB4, 8

# Enemy motion table (struct EnemyTable in the puppet's heap; its address is in Shared::enemyTable).
SHARED_ENEMY_TABLE, SHARED_TEST_PROFILE = 0x120, 0x124
ENEMY_ENTRY = struct.Struct(">HH3fhHHBxf")
ENEMY_ORDER = struct.Struct(">Hh3fHHHBBf")
TABLE_ENTRIES, TABLE_ORDER_COUNT, TABLE_ORDERS, TABLE_MAX = 0x8, 0x388, 0x38C, 32

# Flags. During gameplay the current SaveFile is FileManager::mFileA (isFileInactive() is only true on the
# title screen, file_manager.cpp). Live arrays are copied over the save copy on commit, so every write goes to
# both. Scene flags: the save holds 8 words per scene, the live array only the current scene's 8 words.
FILE_A = 0x0008                  # FileManager::mFileA
SCENE_INDEX = 0x5318             # SaveFile::scene_flag_index (s16)
FLAG_ARRAYS = {
    # name: (save offset in SaveFile, words, live address or None)
    "story": (0x08E4, 128, 0x805A9AD8),   # StoryflagManager::sFlags
    "item":  (0x09E4, 64, 0x805A9BD8),    # ItemflagManager::sFlags
    "scene": (0x1A64, 208, None),         # 26 scenes x 8 words; live: current scene only
    # dungeon_flags[8][22]: 22 entries x 8 words, entry = scene index (DungeonflagManager::commit writes at
    # (mStageIndex & 0x1FFF) * 8; seen live: sDungeonFlags == entry 0 in Skyloft). Holds the small key counter.
    "dungeon": (0x0A64, 176, None),
    # Chests: 2 words per scene (TBoxflagManager::doCommit at mSceneIndex * 2). getFlagCount() is 0x200 bits,
    # so 32 words = scenes 0..15. Kept at 32 so nothing past the array is written.
    "tbox": (0x2A64, 32, None),
}
# Arrays whose current scene's entry is live in a manager: (live address, words per scene entry).
SCENE_MIRRORS = {
    "scene": (0x805A78D0, 8),     # SceneflagManager::sSceneFlags
    "dungeon": (0x805A9C58, 8),   # DungeonflagManager::sDungeonFlags
    "tbox": (0x805753F4, 2),      # TBoxflagManager::sTBoxFlags
}
SCENE_LIVE, SCENE_WORDS = SCENE_MIRRORS["scene"]
PROFILE_NAME = 0x08              # fBase_c::mProfileName
PLAYER_PROFILE = 0xC8            # fProfile::PLAYER (f_profile_name.h; its profile table entry was set live)


class GameSide:
    def __init__(self, pid):
        self.d = m.Dolphin(writable=True, pid=pid)
        self.block = self.find_block()
        self.seq = 0

    def find_block(self):
        w = self.d.u32(HOOK_SITE)
        if w is None or w >> 26 != 18:
            raise SystemExit(f"pid {self.d.pid}: no hook branch at {HOOK_SITE:#x} (got {w}); is the mod running?")
        off = w & 0x03FFFFFC
        if off & 0x02000000:
            off -= 0x04000000
        target = HOOK_SITE + off
        if self.d.u32(target) != HOOK_FIRST_WORD:
            raise SystemExit(f"pid {self.d.pid}: branch target {target:#x} is not our hook")
        return target + BLOCK_OFFSET

    def link_state(self):
        """(stage, room, (x, y, z), rotY, tunic, (anim0, anim1, frame0, frame1), equip) or None when there is no
        Link. anim0/anim1: lower-/upper-body animation (mAnimations[0..1]), frames from mAnmChrs[0..1]. equip:
        EQUIP bytes (equip_state). Then held: HELD bytes (held_state)."""
        link = self.d.u32(m.LINK_PTR)
        if not m.valid_ptr(link):
            return None
        pos = struct.unpack(">3f", self.d.read(link + m.ACTOR_POS, 12))
        rot_y = struct.unpack(">h", self.d.read(link + m.ACTOR_ROT + 2, 2))[0]
        tunic = self.d.read(link + TUNIC_TYPE, 1)[0]
        a0, a1 = struct.unpack(">2H", self.d.read(link + ANIMATIONS, 4))
        f0 = struct.unpack(">f", self.d.read(link + ANM_CHRS + ANM_FRAME, 4))[0]
        f1 = struct.unpack(">f", self.d.read(link + ANM_CHRS + ANM_CHR_SIZE + ANM_FRAME, 4))[0]
        spawn = m.spawn_info(self.d, m.CURRENT_SPAWN)
        return (spawn["stage"], spawn["room"], pos, rot_y, tunic, (a0, a1, f0, f1), self.equip_state() or NO_EQUIP,
                self.held_state() or NO_HELD)

    def equip_state(self):
        """EQUIP bytes for the own Link's sword, sheath and shield, or None without a Link."""
        link = self.d.u32(m.LINK_PTR)
        if not m.valid_ptr(link):
            return None
        body = self.d.u32(link + BODY_MODEL + SCN_LEAF)
        if not m.valid_ptr(body):
            return None
        inv = rigid_inverse(struct.unpack(">12f", self.d.read(body + MTX_LOCAL, 0x30)))
        drawn, vals = 0, []
        for bit, off in enumerate(EQUIP_MODELS):
            leaf = self.d.u32(link + off + SCN_LEAF)
            rel = [0.0] * 12
            if m.valid_ptr(leaf) and self.d.u32(leaf + G3D_PARENT):
                drawn |= 1 << bit
                rel = mtx_mul(inv, struct.unpack(">12f", self.d.read(leaf + MTX_WORLD, 0x30)))
            vals += [_s16(v * (POS_SCALE if i % 4 == 3 else ROT_SCALE)) for i, v in enumerate(rel)]
        return self._finish_equip(drawn, vals)

    def _rel_models(self, link, offsets):
        """For each model at Link+offset: (drawn, body-relative matrix as s16 x 12, ScnObj address)."""
        body = self.d.u32(link + BODY_MODEL + SCN_LEAF)
        inv = rigid_inverse(struct.unpack(">12f", self.d.read(body + MTX_LOCAL, 0x30)))
        out = []
        for off in offsets:
            leaf = self.d.u32(link + off + SCN_LEAF)
            rel, drawn = [0.0] * 12, False
            if m.valid_ptr(leaf) and self.d.u32(leaf + G3D_PARENT):
                drawn = True
                rel = mtx_mul(inv, struct.unpack(">12f", self.d.read(leaf + MTX_WORLD, 0x30)))
            out.append((drawn, [_s16(v * (POS_SCALE if i % 4 == 3 else ROT_SCALE)) for i, v in enumerate(rel)], leaf))
        return out

    def held_state(self):
        """HELD bytes for the own Link's held item models, or None without a Link."""
        link = self.d.u32(m.LINK_PTR)
        if not m.valid_ptr(link) or not m.valid_ptr(self.d.u32(link + BODY_MODEL + SCN_LEAF)):
            return None
        drawn, ids, vals = 0, [], []
        for slot, (on, rel, leaf) in enumerate(self._rel_models(link, HELD_MODEL_SLOTS)):
            idx = 0xFF
            if on:
                mdl = self.d.u32(leaf + 0xE8)    # ScnMdlSimple::mResMdl
                name = self._cstr(mdl + self.d.u32(mdl + RESMDL_NAME)) if m.valid_ptr(mdl) else ""
                idx = HELD_INDEX.get(name, 0xFF)
                if idx != 0xFF:
                    drawn |= 1 << slot
            ids.append(idx)
            vals += rel
        return HELD.pack(drawn, *ids, *vals)

    def _cstr(self, addr):
        raw = self.d.read(addr, 32) or b""
        return raw.split(b"\0")[0].decode("ascii", "replace")

    def _finish_equip(self, drawn, vals):
        sword = self.d.read(CURRENT_SWORD, 1)[0]
        shield = NO_SHIELD
        fa = self._file_a()
        if fa is not None:
            slot = self.d.read(fa + SHIELD_SLOT, 1)[0]
            if slot < 8:
                item = self.d.u32(fa + POUCH_ITEMS + 4 * slot) & 0xFFFF
                if ITEM_WOODEN_SHIELD <= item < ITEM_WOODEN_SHIELD + SHIELD_IDS:
                    shield = item - ITEM_WOODEN_SHIELD
        return EQUIP.pack(drawn, sword, shield, *vals)

    def enemy_stage(self):
        """Stage name while in play, else None."""
        if not self.in_play():
            return None
        return m.spawn_info(self.d, m.CURRENT_SPAWN)["stage"]

    def enemy_words(self):
        return self.d.u16s(ENEMY_LIVE, ENEMY_WORDS)

    def enemy_write(self, index, value):
        fa = self._file_a()
        word = struct.pack(">H", value)
        self.d.write(ENEMY_LIVE + 2 * index, word)
        if fa is not None:
            self.d.write(fa + ENEMY_SAVE + 2 * index, word)

    def enemy_table(self):
        """This game's shared enemies (puppet's listing), or None while there is no table."""
        t = self.d.u32(self.block + SHARED_ENEMY_TABLE)
        if not m.valid_ptr(t):
            return None
        head = self.d.read(t, 8)
        if head is None:
            return None
        _seq, n = struct.unpack(">2I", head)
        n = min(n, TABLE_MAX)
        raw = self.d.read(t + TABLE_ENTRIES, n * ENEMY_ENTRY.size) if n else b""
        out = []
        for k in range(n):
            oid, prof, x, y, z, rot, health, anim, mode, frame = ENEMY_ENTRY.unpack_from(raw, k * ENEMY_ENTRY.size)
            out.append(dict(objId=oid, profile=prof, pos=(x, y, z), rot=rot, health=health, anim=anim, mode=mode,
                            frame=frame))
        return out

    def write_enemy_orders(self, orders):
        """orders: [(objId, rotY, pos, healthCap, flags, (anim, mode, frameSeq, frame))]. Entries first, count
        last."""
        t = self.d.u32(self.block + SHARED_ENEMY_TABLE)
        if not m.valid_ptr(t):
            return
        orders = list(orders)[:TABLE_MAX]
        if orders:
            self.d.write(t + TABLE_ORDERS, b"".join(ENEMY_ORDER.pack(o, r, *p, h, f, *a) for o, r, p, h, f, a in orders))
        self.d.write(t + TABLE_ORDER_COUNT, struct.pack(">I", len(orders)))

    def set_test_profile(self, profile):
        self.d.write(self.block + SHARED_TEST_PROFILE, struct.pack(">H", profile))

    def kills_taken(self):
        """The puppet has handled the last kill batch (or there is none)."""
        seq, ack = struct.unpack(">2I", self.d.read(self.block + MAIL_KILL_SEQ, 8))
        return seq == ack

    def write_kills(self, ids):
        ids = list(ids)[:KILL_SLOTS] + [0xFFFF] * (KILL_SLOTS - len(ids))
        self.d.write(self.block + MAIL_KILLS, struct.pack(f">{KILL_SLOTS}H", *ids))
        seq = struct.unpack(">I", self.d.read(self.block + MAIL_KILL_SEQ, 4))[0]
        self.d.write(self.block + MAIL_KILL_SEQ, struct.pack(">I", (seq + 1) & 0xFFFFFFFF))

    def _file_a(self):
        fm = self.d.u32(m.FILE_MGR_PTR)
        return fm + FILE_A if m.valid_ptr(fm) else None

    def in_play(self):
        """Link is a live player actor and a stage is loaded. A non-null LINK alone is not enough: during a
        Dolphin reset the flag arrays were seen zeroed while LINK still held its old value (2026-10-25)."""
        link = self.d.u32(m.LINK_PTR)
        if not m.valid_ptr(link):
            return False
        prof = self.d.read(link + PROFILE_NAME, 2)
        if prof is None or struct.unpack(">H", prof)[0] != PLAYER_PROFILE:
            return False
        spawn = m.spawn_info(self.d, m.CURRENT_SPAWN)
        return bool(spawn and spawn["stage"])

    def flag_view(self):
        """Logical flag arrays, or None unless in play (loading, title, reset): flags are only touched in play."""
        if not self.in_play():
            return None
        fa = self._file_a()
        if fa is None:
            return None
        view = {}
        for name, (off, n, live) in FLAG_ARRAYS.items():
            view[name] = self.d.u16s(live if live else fa + off, n)
            if view[name] is None:
                return None
        idx = struct.unpack(">h", self.d.read(fa + SCENE_INDEX, 2))[0]
        for name, (live, per) in SCENE_MIRRORS.items():
            if 0 <= idx and (idx + 1) * per <= len(view[name]):
                view[name][idx * per:(idx + 1) * per] = self.d.u16s(live, per)
        self._scene_idx = idx
        self.scene_index = idx  # read by FlagSync (clears for the current scene entry only)
        return view

    def flag_write(self, name, index, value):
        fa = self._file_a()
        off, n, live = FLAG_ARRAYS[name]
        if fa is None or not 0 <= index < n:
            return
        word = struct.pack(">H", value)
        self.d.write(fa + off + 2 * index, word)
        if live:
            self.d.write(live + 2 * index, word)
        elif name in SCENE_MIRRORS:
            mirror, per = SCENE_MIRRORS[name]
            if index // per == getattr(self, "_scene_idx", -1):
                self.d.write(mirror + 2 * (index % per), word)

    def write_mail(self, flags, pos=(0.0, 0.0, 0.0), rot_y=0, tunic=None, anim=None, equip=None, held=None):
        # Data first, sequence last, so the puppet never sees a new seq with old data.
        self.d.write(self.block + MAIL_POS, struct.pack(">3fh", *pos, rot_y))
        if equip is not None:
            self.d.write(self.block + MAIL_EQUIP_DATA, equip)
            flags |= MAIL_EQUIP
        if held is not None:
            self.d.write(self.block + MAIL_HELD_DATA, held)
            flags |= MAIL_HELD
        if tunic is not None:
            self.d.write(self.block + MAIL_TUNIC, bytes([tunic & 0xFF]))
        if anim is not None:
            self.d.write(self.block + MAIL_ANIM_DATA, struct.pack(">2H2f", *anim))
            flags |= MAIL_ANIM
        self.d.write(self.block + MAIL_FLAGS, struct.pack(">I", flags))
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        self.d.write(self.block + MAIL_SEQ, struct.pack(">I", self.seq))


def rigid_inverse(a):
    """Inverse of a 3x4 matrix whose 3x3 part is a rotation."""
    r = (a[0], a[1], a[2], a[4], a[5], a[6], a[8], a[9], a[10])
    t = (a[3], a[7], a[11])
    out = []
    for i in range(3):
        row = (r[i], r[3 + i], r[6 + i])  # column i of r = row i of r transposed
        out += [*row, -sum(row[k] * t[k] for k in range(3))]
    return out


def mtx_mul(a, b):
    """a * b for 3x4 matrices (implied bottom row 0 0 0 1)."""
    out = []
    for i in range(3):
        for j in range(4):
            v = sum(a[i * 4 + k] * b[k * 4 + j] for k in range(3))
            out.append(v + a[i * 4 + 3] if j == 3 else v)
    return out


def _s16(v):
    return max(-32768, min(32767, int(round(v))))
