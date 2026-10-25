# Gecko C2 hook at dScGame_c::execute (0x801BA700, SOUE01 rev 0), runs once per frame in the game scene.
# Keeps one co-op puppet actor alive while Link exists: links its REL, creates it, and recreates it if it
# was deleted (stage/room change, failed create). Retries at most every RETRY_FRAMES frames.
# Assemble: powerpc-eabi-as -mgekko --defsym PUPPET_PROFILE=0x... spawn_puppet.s
# Addresses: zeldaret/ss config/SOUE01/symbols.txt

.set LINK_PTR,     0x8057578C      # dAcPy_c::LINK
.set OBJ_CREATE,   0x8002E630      # dAcObjBase_c::create(u16 profile, u32 room, u32 params,
                                   #   const mVec3_c *pos, const mAng3_c *rot, const mVec3_c *scale, u32 params2)
.set DYL_ISLINKED, 0x80052FA0      # dDyl::isLinked(u16 relId); relId == profile id (DYNAMIC_NAME_TABLE)
.set DYL_TRYLINK,  0x80052FF0      # dDyl::tryLink(u16 relId): 1 = linked, 0 = still loading, 2 = failed
.set PROFILE_NAME, 0x08            # fBase_c::mProfileName (u16)
.set ACTOR_ROT,    0xB8            # dAcBase_c::mRotation
.set ACTOR_POS,    0xC0            # dAcBase_c::mPosition
.set ACTOR_ROOM,   0xEC            # dAcBase_c::mRoomID (s8)
.set RETRY_FRAMES, 30

# Shared block (see `block` below). The puppet gets its address as params and writes the debug fields.
.set B_PUPPET,     0x00            # puppet actor pointer, 0 = none
.set B_COOLDOWN,   0x04            # frames until the next create attempt
.set B_CREATES,    0x08            # number of create() calls made by this hook
.set B_EVENT,      0x0C            # puppet: last lifecycle event (1 create ok, 2 heap fail, 3 delete)
.set B_EXECS,      0x10            # puppet: actorExecute counter
.set B_EVENTS,     0x14            # puppet: count of events written
# 0x18..0x3B: mailbox written by the bridge, event history written by the puppet
# (layout in mod/puppet/d_a_coop_puppet.h, struct Shared)
.set B_HOOKOFF,    0x3C            # dev tools: HOOK_OFF_MAGIC here = do nothing (see mod/tools/devloop.py)
.set HOOK_OFF_MAGIC_HI, 0x0FF0
.set HOOK_OFF_MAGIC_LO, 0x0FF0

    stwu    r1, -0x40(r1)
    mflr    r0
    stw     r0, 0x44(r1)
    stmw    r28, 0x30(r1)
    stw     r3, 0x08(r1)               # dScGame_c *this
    stw     r4, 0x0C(r1)

    bl      block_end                  # r31 = address of the shared block
block:
    .long   0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0   # 0x00-0x3F
    .long   0, 0, 0, 0, 0, 0, 0, 0                           # 0x40-0x5F: remote animation (struct Shared)
    .long   0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0   # 0x60-0x9F: remote equipment (struct Shared)
    .long   0, 0, 0, 0                                       # 0xA0-0xAF
    .long   0, 0, 0, 0, 0, 0, 0, 0                           # 0xB0-0xCF: remote enemy kills (struct Shared)
    .long   0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0   # 0xD0-0x10F: remote held items (struct Shared)
    .long   0, 0, 0, 0                                       # 0x110-0x11F
    .long   0, 0, 0, 0, 0, 0, 0, 0                           # 0x120-0x13F: enemy motion table pointer (struct Shared)
block_end:
    mflr    r31

    lwz     r28, B_HOOKOFF(r31)        # switched off by the dev tools?
    lis     r29, HOOK_OFF_MAGIC_HI
    ori     r29, r29, HOOK_OFF_MAGIC_LO
    cmplw   r28, r29
    beq     done

    lis     r30, LINK_PTR@ha
    lwz     r30, LINK_PTR@l(r30)       # r30 = Link
    cmpwi   r30, 0
    bne     have_link
    li      r0, 0
    stw     r0, B_PUPPET(r31)          # Link gone: the puppet goes with the stage
    stw     r0, B_COOLDOWN(r31)
    b       done

have_link:
    lwz     r29, B_PUPPET(r31)
    cmpwi   r29, 0
    beq     want_spawn
    lhz     r0, PROFILE_NAME(r29)      # still our actor? (deleted memory gets reused or cleared)
    cmplwi  r0, PUPPET_PROFILE
    beq     done
    li      r0, 0
    stw     r0, B_PUPPET(r31)

want_spawn:
    lwz     r28, B_COOLDOWN(r31)       # (not r0: addi/subi read r0 as the constant 0)
    cmpwi   r28, 0
    beq     try_spawn
    subi    r28, r28, 1
    stw     r28, B_COOLDOWN(r31)
    b       done

try_spawn:
    li      r0, RETRY_FRAMES
    stw     r0, B_COOLDOWN(r31)
    # Our REL is used by no stage, so the game never links it: link it ourselves first.
    # (fBase_make returns null while the profile table entry is empty.)
    li      r3, PUPPET_PROFILE
    lis     r12, DYL_ISLINKED@ha
    addi    r12, r12, DYL_ISLINKED@l
    mtctr   r12
    bctrl
    cmpwi   r3, 0
    bne     do_create
    li      r3, PUPPET_PROFILE
    lis     r12, DYL_TRYLINK@ha
    addi    r12, r12, DYL_TRYLINK@l
    mtctr   r12
    bctrl
    cmpwi   r3, 1
    bne     done                       # still loading (or failed): try again later

do_create:
    lwz     r28, B_CREATES(r31)
    addi    r28, r28, 1
    stw     r28, B_CREATES(r31)
    li      r3, PUPPET_PROFILE
    lbz     r4, ACTOR_ROOM(r30)
    extsb   r4, r4
    mr      r5, r31                    # params = shared block address
    addi    r6, r30, ACTOR_POS
    addi    r7, r30, ACTOR_ROT
    li      r8, 0                      # scale: default
    li      r9, -1                     # params2
    lis     r12, OBJ_CREATE@ha
    addi    r12, r12, OBJ_CREATE@l
    mtctr   r12
    bctrl
    stw     r3, B_PUPPET(r31)

done:
    lwz     r3, 0x08(r1)
    lwz     r4, 0x0C(r1)
    lmw     r28, 0x30(r1)
    lwz     r0, 0x44(r1)
    mtlr    r0
    addi    r1, r1, 0x40
    stwu    r1, -0x10(r1)              # the instruction we replaced
