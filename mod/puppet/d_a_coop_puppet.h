#ifndef D_A_COOP_PUPPET_H
#define D_A_COOP_PUPPET_H

#include "d/a/obj/d_a_obj_base.h"
#include "m/m3d/m_anmchr.h"
#include "m/m3d/m_anmchrblend.h"
#include "m/m3d/m_anmtexpat.h"
#include "m/m3d/m_smdl.h"
#include "m/m_allocator.h"
#include "nw4r/g3d/g3d_calcworld.h"
#include "nw4r/g3d/res/g3d_resanmchr.h"
#include "nw4r/g3d/res/g3d_resfile.h"

class dAcPy_c;

// Co-op puppet: shows the remote player's Link. Body, head, face and hands from "Alink", put together the way
// daPlayerModelBase_c does it (head and face follow the body's head joint, hands copy the body's hand joints).
// Animation: the remote Link's own lower-body and upper-body animations (its slots 0 and 1, by sAnimations index,
// with their frames), blended like the player does (lower from WAIST/CENTER, upper from BACKBONE_1).
class dAcCoopPuppet_c : public dAcObjBase_c {
public:
    dAcCoopPuppet_c();
    virtual ~dAcCoopPuppet_c();

    virtual bool createHeap() override;
    virtual int create() override;
    virtual int doDelete() override;
    virtual int actorExecute() override;
    virtual int draw() override;

    // Remote Link equipment the puppet can show (daPlayerModelBase_c mSwordMdl, mSheathMdl, mShieldMdl).
    enum { EQUIP_SWORD, EQUIP_SHEATH, EQUIP_SHIELD, EQUIP_COUNT };
    enum { KILL_SLOTS = 8 };
    enum { HELD_SLOTS = 3 }; // the player's three held item models (dAcPy_c +0x169C, +0x16B8, +0x16D4)

    // Shared block owned by the spawn hook (mod/hook/spawn_puppet.s, 0x140 bytes); its address arrives as
    // params. The bridge (mod/bridge) writes the mailbox part from outside the game.
    struct Shared {
        /* 0x00 */ u32 puppet;    // hook
        /* 0x04 */ u32 cooldown;  // hook
        /* 0x08 */ u32 creates;   // hook
        /* 0x0C */ u32 lastEvent; // puppet
        /* 0x10 */ u32 execs;     // puppet
        /* 0x14 */ u32 events;    // puppet
        /* 0x18 */ u32 mailSeq;   // bridge: bumped after every complete write
        /* 0x1C */ u32 mailFlags; // bridge: MAIL_*
        /* 0x20 */ f32 posX;      // bridge: remote Link position
        /* 0x24 */ f32 posY;
        /* 0x28 */ f32 posZ;
        /* 0x2C */ s16 rotY;      // bridge: remote Link facing
        /* 0x2E */ u8 tunic;      // bridge: remote Link's mTunicType (0 tunic, 1 Skyloft clothes, 2 Silent Realm)
        /* 0x2F */ u8 pad;        // puppet: PUPPET_BUILD of the running code
        /* 0x30 */ u8 history[8]; // puppet: last 8 events, oldest overwritten
        /* 0x38 */ u32 historyIdx;
        /* 0x3C */ u32 hookOff;   // dev tools: HOOK_OFF_MAGIC stops the hook from linking/spawning
        /* 0x40 */ u16 anim[2];   // bridge: remote daPlayerModelBase_c::mAnimations[0..1] (sAnimations index)
        /* 0x44 */ f32 frame[2];  // bridge: remote mAnmChrs[0..1] current frame
        /* 0x4C */ u32 reserved[5];
        /* 0x60 */ u8 equipDrawn; // bridge: EQUIP_* bits the remote Link draws this frame
        /* 0x61 */ u8 sword;      // bridge: remote daPlayerActBase_c::sCurrentSword (getSwordName index, 6 none)
        /* 0x62 */ u8 shield;     // bridge: remote shield id (item - ITEM_WOODEN_SHIELD, 0..9), 0xFF none
        /* 0x63 */ u8 equipPad;
        /* 0x64 */ s16 equipMtx[EQUIP_COUNT][12]; // bridge: model matrix relative to the body's, rotation x 8192,
                                                  // translation x 16 (mod/bridge/game_side.py EQUIP)
        /* 0xAC */ u32 killSeq;   // bridge: bumped after writing kills[]
        /* 0xB0 */ u32 killAck;   // puppet: killSeq it has handled; the bridge waits for it before the next batch
        /* 0xB4 */ u16 kills[KILL_SLOTS]; // bridge: enemy defeat flags (dAcBase_c::mObjID) the remote player set,
                                          // 0xFFFF = empty
        /* 0xC4 */ u32 reserved3[3];
        /* 0xD0 */ u8 heldDrawn;            // bridge: bits of the remote Link's held item slots drawn this frame
        /* 0xD1 */ u8 heldId[HELD_SLOTS];   // bridge: sHeldModels index per slot, 0xFF none
        /* 0xD4 */ s16 heldMtx[HELD_SLOTS][12]; // bridge: like equipMtx
        /* 0x11C */ u32 reserved4;
        /* 0x120 */ u32 enemyTable;  // puppet: address of its EnemyTable (0 = none yet); moves when we respawn
        /* 0x124 */ u16 testProfile; // dev/QA: also treat actors of this profile as enemies (0 = off)
        /* 0x126 */ u16 pad5;
        /* 0x128 */ u16 testAnimObj; // dev/QA: play animation testAnimIdx on this game's enemy with this mObjID once
        /* 0x12A */ u16 testAnimIdx; //   (EnemyEntry::anim numbering); the puppet clears testAnimObj when done
        /* 0x12C */ u32 reserved5[5];
    };

    // Enemy motion (mod/bridge/enemy_motion.py). The puppet lists the live placed enemies of this game every frame;
    // the bridge writes orders for the enemies the other game owns: pull to the owner's position and facing, and a
    // health cap (lower of the two games' health).
    enum { ENEMY_MAX = 32 };
    struct EnemyEntry {
        /* 0x00 */ u16 objId;   // dAcBase_c::mObjID: (room << 10) | placement index, the same in both games
        /* 0x02 */ u16 profile;
        /* 0x04 */ f32 pos[3];
        /* 0x10 */ s16 rotY;
        /* 0x12 */ u16 health;  // dAcEnBase_c::mHealth, 0xFFFF for test-profile actors
        /* 0x14 */ u16 anim;    // current animation: index in its mdlAnmChr's anim file (bit 15: model file);
                                // 0xFFFF = not a type we know
        /* 0x16 */ u8 mode;     // m3d::playMode_e of it
        /* 0x17 */ u8 pad;
        /* 0x18 */ f32 frame;
    };
    struct EnemyOrder {
        /* 0x00 */ u16 objId;
        /* 0x02 */ s16 rotY;
        /* 0x04 */ f32 pos[3];
        /* 0x10 */ u16 healthCap; // 0xFFFF = none
        /* 0x12 */ u16 flags;     // ORDER_FOLLOW, ORDER_ANIM
        /* 0x14 */ u16 anim;      // ORDER_ANIM: the owner's animation (EnemyEntry::anim)
        /* 0x16 */ u8 mode;
        /* 0x17 */ u8 frameSeq;   // changes when frame is a new value from the owner; only then we jump to it
        /* 0x18 */ f32 frame;
    };
    enum { ORDER_FOLLOW = 1, ORDER_ANIM = 2 };
    struct EnemyTable {
        /* 0x000 */ u32 seq;        // puppet: bumped after every complete listing
        /* 0x004 */ u32 count;
        /* 0x008 */ EnemyEntry entries[ENEMY_MAX];
        /* 0x388 */ u32 orderCount; // bridge: written after the orders
        /* 0x38C */ EnemyOrder orders[ENEMY_MAX];
    };
    enum MailFlag {
        MAIL_BRIDGE = 1 << 0,  // a bridge is attached; without it the puppet stays where it spawned
        MAIL_VISIBLE = 1 << 1, // remote player is in the same stage: show the puppet at posX/Y/Z
        MAIL_ANIM = 1 << 2,    // anim[] and frame[] are valid
        MAIL_EQUIP = 1 << 3,   // equip* fields are valid
        MAIL_HELD = 1 << 4,    // held* fields are valid
    };
    enum Event {
        EVENT_CREATE_OK = 1,
        EVENT_HEAP_FAIL = 2,
        EVENT_DELETE = 3,
        EVENT_ALLOC_FAIL = 4,
        EVENT_ANIM_FAIL = 5,
        EVENT_TUNIC_CHANGE = 6,
        EVENT_ANIM_BUFFER_FAIL = 7,
        EVENT_ANIM_LOAD_FAIL = 8,
        EVENT_ANIM_CREATE_FAIL = 9,
        EVENT_COPY_FAIL = 10,
        EVENT_EQUIP_CHANGE = 11, // remote sword or shield changed: respawn with the new models
        EVENT_EQUIP_FAIL = 12,   // an equipment model did not fit in the heap (not drawn)
        EVENT_REMOTE_KILL = 13,  // removed an actor the remote player defeated
        EVENT_HELD_LOAD = 14,    // loaded a new set of held item models
        EVENT_HELD_FAIL = 15,    // a held item model did not fit in its heap (not drawn)
        EVENT_TABLE_FAIL = 16,   // no room for the enemy table
    };
    enum { SLOT_COUNT = 2 }; // 0 lower body, 1 upper body

    class HandsCallback : public nw4r::g3d::ICalcWorldCallback {
    public:
        HandsCallback() : mpOwner(nullptr) {}
        virtual void ExecCallbackC(
            nw4r::math::MTX34 *pMtxArray, nw4r::g3d::ResMdl mdl, nw4r::g3d::FuncObjCalcWorld *pFuncObj
        ) override;
        dAcCoopPuppet_c *mpOwner;
    };
    friend class HandsCallback; // pre-C++11 compiler: nested classes need this to reach mBodyMdl

private:
    void note(u32 event);
    void releaseSlot();
    nw4r::g3d::ResMdl ownBodyResource(const char *name);
    nw4r::g3d::ResAnmChr loadAnim(u16 idx, void *dest);
    bool setSlotAnim(int slot, u16 idx);
    void followMail();
    void updateAnim();
    void placeParts();
    void loadEquip();
    void applyRemoteKills();
    void updateEnemies();
    bool frameIsNew(u16 objId, u8 seq);
    bool loadItem(const char *file, nw4r::g3d::ResFile &out, mAllocator_c &allocator);
    void updateHeld();
    void placeRel(m3d::smdl_c &mdl, const s16 *q);
    bool createEquipMdl(int which, nw4r::g3d::ResFile res, const char *name);

    nw4r::g3d::ResFile mRes;
    nw4r::g3d::ResFile mAnimRes; // PLCommonAnime
    m3d::smdl_c mBodyMdl;
    m3d::smdl_c mHeadMdl;
    m3d::smdl_c mFaceMdl;
    m3d::smdl_c mHandsMdl;
    HandsCallback mHandsCallback;
    m3d::anmChrBlend_c mBlend;
    m3d::anmChr_c mSlots[SLOT_COUNT];
    u16 mSlotAnim[SLOT_COUNT];
    Shared *mpShared;
    u8 *mpAnimBuf;  // SLOT_COUNT animation buffers in our actor heap, or null
    m3d::smdl_c mEquipMdl[EQUIP_COUNT];
    mHeapAllocator_c mHeldAllocator;    // before mHeldMdl: members are destroyed in reverse order
    m3d::smdl_c mHeldMdl[HELD_SLOTS];
    u8 mHeldLoaded[HELD_SLOTS];         // sHeldModels index loaded per slot, 0xFF none
    bool mHeldOk[HELD_SLOTS];
    bool mHeldHeapReady; // mHeldAllocator has its frame heap (a fresh allocator points at mHeap::g_assertHeap)
    EnemyTable *mpEnemies;
    u16 mFrameSeenId[ENEMY_MAX];  // last EnemyOrder::frameSeq applied per enemy (small ring by objId)
    u8 mFrameSeenSeq[ENEMY_MAX];
    m3d::anmTexPat_c mShieldTexPat;
    bool mEquipOk[EQUIP_COUNT];
    u8 mSword;  // what we were created for (Shared::sword/shield at create); a change respawns us
    u8 mShield;
    u32 mLastSeq;
    u8 mTunic;
    bool mHasTarget;
    bool mHidden;
    bool mAnimOk;
};

#endif
