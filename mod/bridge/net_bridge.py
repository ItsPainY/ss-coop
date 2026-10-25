"""Networked bridge: one per player, next to that player's Dolphin. Host listens, the other player joins.

  python -I net_bridge.py --pid PID host [--bind 0.0.0.0] [--port 27645]
  python -I net_bridge.py --pid PID join HOST_IP[:PORT]

Each side sends its own Link 30 times a second over UDP and writes the peer's Link into its own puppet
mailbox. The puppet shows only while both are in the same stage and the peer was heard from within
PEER_TIMEOUT seconds. Stdlib only. Positions are unreliable by design: a lost packet is replaced by the next.

Wire format (big endian), every packet: magic "SSC1", type u8, flags u8, seq u32.
  HELLO (1)  join -> host, repeated until answered
  WELCOME (2) host -> join
  STATE (3)  stage char[8], room s8, has_link u8, pos 3 x f32, rotY s16, tunic u8, pad u8,
             anim0 u16, anim1 u16, frame0 f32, frame1 f32   (lower/upper body animation, sAnimations index)
             equipment: 76 bytes, game_side.EQUIP (sword, sheath, shield: drawn bits, ids, body-relative matrices)
             held items: 76 bytes, game_side.HELD (drawn bits, held_models index x 3, body-relative matrices)
             (older magics SSC1..SSC4 are ignored as garbage)
  BYE (4)    sender is leaving
  FLAGS (5)  msg_id u32, count u16, count x (array u8, index u16, set u16, clear u16); reliable
  FLAGS_ACK (6) msg_id u32
  ENEMY (7)  stage char[8], count u16, count x (index u16, bits u16): sender's enemy defeat flags; repeated,
             not acked (enemy_sync.py)
  OWN (8)    host -> join: stage char[8], count u16, count x (objId u16, owner u8, pad, epoch u32)
  MOTION (9) stage char[8], count u16, count x (objId u16, rotY s16, epoch u32, pos 3 x f32, health u16,
             anim u16, mode u8, pad, frame f32)
             (enemy_motion.py)

Flags are reliable and ordered: one FLAGS message in flight at a time, resent until acknowledged, applied
once (msg_id == last applied + 1). A new connection starts with a full snapshot from both sides (OR merge).
Flags are only read and applied while the player's own Link exists (not during loads).
"""

import argparse
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from flag_sync import MUTANT, ARRAY_IDS, ARRAY_NAMES, RESYNC, ChangeQueue, FlagSync  # noqa: E402
from enemy_sync import EnemySync  # noqa: E402
from enemy_motion import EnemyMotion  # noqa: E402

# SSC2: tunic byte in STATE. SSC3: dungeon/tbox flag arrays; an SSC2 peer would reject those FLAGS messages,
# never ack them and stall the ordered flag channel, so the versions must not connect.
# SSC4: the remote Link's animation slots in STATE. SSC5: equipment in STATE. SSC6: ENEMY messages.
# SSC7: held items in STATE. SSC8: OWN and MOTION (enemy motion). SSC9: enemy animation in MOTION.
MAGIC = b"SSC9"
HEADER = struct.Struct(">4sBBI")
STATE = struct.Struct(">8sbB3fhBx2H2f76s76s")
NO_EQUIP = bytes(76)
NO_HELD = bytes([0, 0xFF, 0xFF, 0xFF]) + bytes(72)
NO_ANIM = (443, 443, 0.0, 0.0)  # PLAYER_ANIM_MAX: no animation
FLAGS_HEAD = struct.Struct(">IH")
FLAG_ENTRY = struct.Struct(">BHHH")
ACK = struct.Struct(">I")
HELLO, WELCOME, STATE_T, BYE, FLAGS_T, FLAGS_ACK, ENEMY_T, OWN_T, MOTION_T = 1, 2, 3, 4, 5, 6, 7, 8, 9
OWN_ENTRY = struct.Struct(">HBxI")
MOTION_ENTRY = struct.Struct(">HhI3fHHBxf")
ENEMY_HEAD = struct.Struct(">8sH")
ENEMY_ENTRY = struct.Struct(">HH")
FLAGS_PER_MSG = 150      # 7 bytes each: ~1 KB packets
FLAG_POLL_EVERY = 0.1
FLAG_RESEND_EVERY = 0.25

DEFAULT_PORT = 27645
SEND_HZ = 30.0
TICK_HZ = 60.0
HELLO_EVERY = 0.5
PEER_TIMEOUT = 2.0

MAIL_BRIDGE, MAIL_VISIBLE = 1, 2  # same values as game_side.py / struct Shared


def encode(ptype, seq, state=None, msg_id=0, changes=()):
    """ENEMY: state = (stage, [(index, bits), ...])."""
    out = HEADER.pack(MAGIC, ptype, 0, seq & 0xFFFFFFFF)
    if ptype in (ENEMY_T, OWN_T, MOTION_T):
        stage, entries = state
        out += ENEMY_HEAD.pack(stage.encode("ascii", "replace")[:8], len(entries))
        if ptype == ENEMY_T:
            out += b"".join(ENEMY_ENTRY.pack(i, w) for i, w in entries)
        elif ptype == OWN_T:
            out += b"".join(OWN_ENTRY.pack(o, owner, ep) for o, owner, ep in entries)
        else:
            out += b"".join(MOTION_ENTRY.pack(o, rot, ep, *pos, h, an, md, fr) for o, ep, pos, rot, h, an, md, fr in entries)
    elif ptype == FLAGS_T:
        out += FLAGS_HEAD.pack(msg_id, len(changes))
        out += b"".join(FLAG_ENTRY.pack(ARRAY_IDS[n], i, s, c) for n, i, s, c in changes)
    elif ptype == FLAGS_ACK:
        out += ACK.pack(msg_id)
    elif ptype == STATE_T:
        if state is None:
            out += STATE.pack(b"", 0, 0, 0.0, 0.0, 0.0, 0, 0, *NO_ANIM, NO_EQUIP, NO_HELD)
        else:
            stage, room, pos, rot_y = state[:4]
            tunic = state[4] if len(state) > 4 else 0
            anim = state[5] if len(state) > 5 else NO_ANIM
            equip = state[6] if len(state) > 6 else NO_EQUIP
            held = state[7] if len(state) > 7 else NO_HELD
            out += STATE.pack(stage.encode("ascii", "replace")[:8], room if room < 128 else room - 256, 1, *pos, rot_y,
                              tunic & 0xFF, *anim, equip, held)
    return out


def decode(data):
    """(type, seq, payload) or None for anything that is not ours. payload: STATE -> state or None (no Link),
    FLAGS -> (msg_id, changes), FLAGS_ACK -> msg_id, else None."""
    if len(data) < HEADER.size:
        return None
    magic, ptype, _flags, seq = HEADER.unpack_from(data)
    if magic != MAGIC or ptype not in (HELLO, WELCOME, STATE_T, BYE, FLAGS_T, FLAGS_ACK, ENEMY_T, OWN_T, MOTION_T):
        return None
    state = None
    if ptype in (ENEMY_T, OWN_T, MOTION_T):
        entry = {ENEMY_T: ENEMY_ENTRY, OWN_T: OWN_ENTRY, MOTION_T: MOTION_ENTRY}[ptype]
        if len(data) < HEADER.size + ENEMY_HEAD.size:
            return None
        stage, count = ENEMY_HEAD.unpack_from(data, HEADER.size)
        body = HEADER.size + ENEMY_HEAD.size
        if len(data) != body + count * entry.size:
            return None
        entries = [entry.unpack_from(data, body + k * entry.size) for k in range(count)]
        if ptype == MOTION_T:
            entries = [(o, ep, (x, y, z), rot, h, an, md, fr) for o, rot, ep, x, y, z, h, an, md, fr in entries]
        return ptype, seq, (stage.split(b"\0")[0].decode("ascii", "replace"), entries)
    if ptype == FLAGS_T:
        if len(data) < HEADER.size + FLAGS_HEAD.size:
            return None
        msg_id, count = FLAGS_HEAD.unpack_from(data, HEADER.size)
        body = HEADER.size + FLAGS_HEAD.size
        if len(data) != body + count * FLAG_ENTRY.size:
            return None
        changes = []
        for k in range(count):
            aid, idx, s, c = FLAG_ENTRY.unpack_from(data, body + k * FLAG_ENTRY.size)
            if aid not in ARRAY_NAMES:
                return None
            changes.append((ARRAY_NAMES[aid], idx, s, c))
        return ptype, seq, (msg_id, changes)
    if ptype == FLAGS_ACK:
        if len(data) != HEADER.size + ACK.size:
            return None
        return ptype, seq, ACK.unpack_from(data, HEADER.size)[0]
    if ptype == STATE_T:
        if len(data) < HEADER.size + STATE.size:
            return None
        stage, room, has_link, x, y, z, rot_y, tunic, a0, a1, f0, f1, equip, held = STATE.unpack_from(data, HEADER.size)
        if has_link:
            state = (stage.split(b"\0")[0].decode("ascii", "replace"), room, (x, y, z), rot_y, tunic, (a0, a1, f0, f1),
                     equip, held)
    return ptype, seq, state


def seq_newer(a, b):
    """True if seq a is newer than b, with 32-bit wraparound."""
    return a != b and ((a - b) & 0xFFFFFFFF) < 0x80000000


class NetBridge:
    """Transport-agnostic core: give it a game side, a UDP socket and (for join) the host address."""

    def __init__(self, side, sock, role, peer=None, log=print):
        self.side, self.sock, self.role, self.peer, self.log = side, sock, role, peer, log
        self.sock.setblocking(False)
        self.connected = False
        self.send_seq = 0
        self.peer_seq = None
        self.peer_state = None
        self.peer_heard = -1e9
        self.next_send = 0.0
        self.next_hello = 0.0
        self.stats = dict(sent=0, received=0, stale=0, garbage=0, flags_out=0, flags_in=0, resends=0)
        self.flags = FlagSync(side) if hasattr(side, "flag_view") else None
        self.enemies = EnemySync(side) if hasattr(side, "enemy_words") else None
        self.motion = EnemyMotion(side, role, MUTANT) if hasattr(side, "enemy_table") else None
        self._reset_flag_channel()

    def _reset_flag_channel(self):
        """New peer: forget message ids, send everything we have once the game is ready."""
        self.out_queue = ChangeQueue()
        self.inflight = None              # (msg_id, changes, last_sent)
        self.next_msg_id = 1
        self.last_applied = 0
        self.need_snapshot = self.flags is not None
        self.next_flag_poll = 0.0

    def _flags_tick(self, now):
        if self.flags is None or not self.connected:
            return
        if self.need_snapshot:
            snap = self.flags.snapshot()
            if snap is not None:
                for ch in snap:
                    self.out_queue.add(*ch)
                self.need_snapshot = False
        elif now >= self.next_flag_poll:
            self.next_flag_poll = now + FLAG_POLL_EVERY
            for ch in self.flags.poll():
                self.out_queue.add(*ch)
            if self.flags.just_loaded:
                self.flags.just_loaded = False
                self.out_queue.add(*RESYNC)  # we may have loaded an older save: ask for the peer's flags again
                self.log("load finished: asking the peer for its flags again")
        if self.inflight is None and len(self.out_queue):
            changes = self.out_queue.take(FLAGS_PER_MSG)
            self.inflight = [self.next_msg_id, changes, now]
            self.next_msg_id += 1
            self._send(FLAGS_T, msg_id=self.inflight[0], changes=changes)
            self.stats["flags_out"] += len(changes)
        elif self.inflight is not None and now - self.inflight[2] >= FLAG_RESEND_EVERY:
            self.inflight[2] = now
            self._send(FLAGS_T, msg_id=self.inflight[0], changes=self.inflight[1])
            self.stats["resends"] += 1

    def _on_flags(self, msg_id, changes):
        if self.flags is None:
            return
        if msg_id <= self.last_applied:
            self._send(FLAGS_ACK, msg_id=msg_id)  # duplicate: our ack got lost
            return
        if msg_id != self.last_applied + 1:
            return  # cannot happen with one message in flight; ignore
        if not self.flags.apply(changes):
            return  # loading: no ack, the peer resends
        if any(c[0] == "resync" for c in changes):
            self.need_snapshot = True
        self.last_applied = msg_id
        self.stats["flags_in"] += len(changes)
        self._send(FLAGS_ACK, msg_id=msg_id)

    def _send(self, ptype, state=None, msg_id=0, changes=()):
        if self.peer is None:
            return
        self.send_seq = (self.send_seq + 1) & 0xFFFFFFFF
        try:
            self.sock.sendto(encode(ptype, self.send_seq, state, msg_id, changes), self.peer)
            self.stats["sent"] += 1
        except OSError:
            pass  # e.g. ICMP port unreachable surfacing on Windows; the peer may not be up yet

    def _receive(self, now):
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionResetError:
                continue  # Windows reports an earlier ICMP unreachable here; ignore
            msg = decode(data)
            if msg is None:
                self.stats["garbage"] += 1
                continue
            ptype, seq, state = msg
            if self.role == "host":
                if ptype == HELLO:
                    if self.peer != addr or not self.connected:
                        self.log(f"player joined from {addr[0]}:{addr[1]}")
                        self.peer, self.peer_seq = addr, None
                        self._reset_flag_channel()
                    self.connected = True
                    self.peer_heard = now
                    self._send(WELCOME)
                    continue
                if addr != self.peer:
                    continue  # only one peer at a time
            elif addr != self.peer:
                continue
            if ptype == WELCOME and not self.connected:
                self.connected = True
                self._reset_flag_channel()
                self.log("connected to host")
            if ptype == BYE:
                self.log("peer left")
                self.connected, self.peer_state, self.peer_seq = False, None, None
                self._reset_flag_channel()
                if self.role == "host":
                    self.peer = None
                continue
            self.peer_heard = now
            if ptype == FLAGS_T:
                self._on_flags(*state)
                continue
            if ptype in (OWN_T, MOTION_T):
                if self.motion is not None:
                    (self.motion.on_own if ptype == OWN_T else self.motion.on_motion)(*state, now)
                continue
            if ptype == ENEMY_T:
                if self.enemies is not None and MUTANT != "no_enemy_apply":
                    self.stats["enemy_in"] = self.stats.get("enemy_in", 0) + self.enemies.apply(*state, now)
                continue
            if ptype == FLAGS_ACK:
                if self.inflight is not None and state == self.inflight[0]:
                    self.inflight = None
                continue
            if ptype == STATE_T:
                if self.peer_seq is not None and not seq_newer(seq, self.peer_seq):
                    self.stats["stale"] += 1
                    continue
                self.peer_seq = seq
                self.peer_state = state
                self.stats["received"] += 1
                self.connected = True

    def tick(self, now):
        self._receive(now)
        own = self.side.link_state()
        if self.role == "join" and not self.connected and now >= self.next_hello:
            self.next_hello = now + HELLO_EVERY
            self._send(HELLO)
        if self.connected and now >= self.next_send:
            self.next_send = now + 1.0 / SEND_HZ
            self._send(STATE_T, own)
        self._flags_tick(now)
        if self.enemies is not None:
            out = self.enemies.poll(now)
            if out is not None and self.connected:
                self._send(ENEMY_T, out)
            self.enemies.pump_kills()
        fresh = now - self.peer_heard <= PEER_TIMEOUT
        if self.motion is not None:
            peer_now = self.peer_state if fresh else None
            msgs = self.motion.tick(now, own[2] if own else None, peer_now[2] if peer_now else None,
                                    peer_now[0] if peer_now else None)
            for kind, stage, entries in msgs:
                if self.connected:
                    self._send(OWN_T if kind == "own" else MOTION_T, (stage, entries))
        fresh = now - self.peer_heard <= PEER_TIMEOUT
        peer = self.peer_state if fresh else None
        if peer is not None and own is not None and peer[0] == own[0]:
            pos = peer[2]
            if MUTANT == "no_position":
                self._mut_pos = pos = getattr(self, "_mut_pos", None) or pos  # freeze at the first position
            self.side.write_mail(MAIL_BRIDGE | MAIL_VISIBLE, pos, peer[3], tunic=peer[4] if len(peer) > 4 else 0,
                                 anim=peer[5] if len(peer) > 5 else None, equip=peer[6] if len(peer) > 6 else None,
                                 held=peer[7] if len(peer) > 7 and MUTANT != "no_held" else None)
        else:
            self.side.write_mail(MAIL_BRIDGE)
        return own, peer

    def close(self):
        if self.connected:
            self._send(BYE)
        try:
            self.side.write_mail(0)
        except OSError:
            pass


def parse_addr(text, default_port):
    host, _, port = text.rpartition(":") if ":" in text else (text, "", "")
    return (host or text, int(port) if port else default_port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, required=True, help="Dolphin process id running the modded game")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    sub = ap.add_subparsers(dest="role", required=True)
    h = sub.add_parser("host")
    h.add_argument("--bind", default="0.0.0.0", help="127.0.0.1 for same-PC tests (no firewall prompt)")
    h.add_argument("--port", type=int, default=DEFAULT_PORT)
    j = sub.add_parser("join")
    j.add_argument("host", help="HOST_IP or HOST_IP:PORT")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from game_side import GameSide

    side = GameSide(args.pid)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.role == "host":
        sock.bind((args.bind, args.port))
        bridge = NetBridge(side, sock, "host")
        print(f"hosting on {args.bind}:{args.port} (game pid {args.pid}), waiting for a player", flush=True)
    else:
        sock.bind(("0.0.0.0", 0))
        peer = parse_addr(args.host, DEFAULT_PORT)
        bridge = NetBridge(side, sock, "join", peer=peer)
        print(f"joining {peer[0]}:{peer[1]} (game pid {args.pid})", flush=True)
    bridge.log = lambda s: print(s, flush=True)

    end = time.time() + args.seconds if args.seconds else None
    last_print = 0.0
    try:
        while end is None or time.time() < end:
            now = time.monotonic()
            own, peer = bridge.tick(now)
            if now - last_print > 2.0:
                last_print = now
                fmt = lambda s: "-" if s is None else f"{s[0]} r{s[1]} ({s[2][0]:.0f}, {s[2][1]:.0f}, {s[2][2]:.0f})"
                print(f"me: {fmt(own)} | peer: {fmt(peer)} | {bridge.stats}", flush=True)
            time.sleep(max(0.0, 1.0 / TICK_HZ - (time.monotonic() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
        print("bridge stopped, mailbox cleared", flush=True)


if __name__ == "__main__":
    main()
