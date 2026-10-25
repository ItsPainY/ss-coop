#include "coop/d_a_coop_puppet.h"

#include "common.h"
#include "d/a/d_a_player.h"
#include "d/a/e/d_a_en_base.h"
#include "d/a/obj/d_a_obj_base.h"
#include "d/d_heap.h"
#include "d/d_player_act.h"
#include "d/d_player_mdl.h"
#include "d/d_rawarchive.h"
#include "egg/core/eggFrmHeap.h"
#include "egg/core/eggHeap.h"
#include "m/m_heap.h"
#include "f/f_base.h"
#include "f/f_manager.h"
#include "m/m3d/m_fanm.h"
#include "toBeSorted/d_d3d.h"
#include "nw4r/g3d/res/g3d_resanmchr.h"
#include "m/m_mtx.h"
#include "m/m_vec.h"
#include "nw4r/g3d/g3d_anmchr.h"
#include "nw4r/g3d/g3d_scnmdlsmpl.h"
#include "nw4r/g3d/g3d_scnobj.h"
#include "nw4r/g3d/res/g3d_resfile.h"
#include "nw4r/g3d/res/g3d_resmdl.h"
#include "rvl/CX/cx.h"
#include "rvl/OS/OSCache.h"
#include "sized_string.h"
#include "toBeSorted/cx_util.h"

#include <string.h>

// PUPPET_PROFILE is set by the build to the profile whose REL this file replaces. The extra macro level makes
// it expand before SPECIAL_ACTOR_PROFILE pastes it into g_profile_<name>.
#define PUPPET_PROFILE_DEF(p) SPECIAL_ACTOR_PROFILE(p, dAcCoopPuppet_c, fProfile::p, 0x130, 0, 2)
PUPPET_PROFILE_DEF(PUPPET_PROFILE);

// Bump on every change: written to Shared::pad at create, shown by devloop.py status.
static const u8 PUPPET_BUILD = 19;
// Frames the spawn hook waits after we are deleted before it creates the next puppet (see releaseSlot).
static const u32 RESPAWN_DELAY_FRAMES = 60;
static void progress(u32 params, u32 step);

// Without a bridge: stand this far in front of where we were spawned (Link's position and facing).
// Forward is (sin(y), 0, cos(y)). In front, not to the side: after a doorway the side can be inside a wall.
static const f32 FRONT_OFFSET = 100.0f;
// Above this distance the puppet jumps instead of gliding (load zones, first update).
static const f32 SNAP_DISTANCE = 600.0f;
// Fraction of the remaining distance covered per frame: smooths the bridge's update rate.
static const f32 FOLLOW_RATE = 0.5f;

// Body, head, face, hands per tunic type, as in d_player_mdl.cpp (sBodyMdls...). The Silent Realm set ("Sl")
// is not in Alink, so it falls back to the tunic.
static const char *const sBodyMdls[] = {"al", "pl_body"};
static const char *const sHeadMdls[] = {"al_head", "pl_head"};
static const char *const sFaceMdls[] = {"al_face", "al_face"};
static const char *const sHandMdls[] = {"al_hands", "pl_hands"};

// Animations: daPlayerModelBase_c::sAnimations indices, unpacked from PLCommonAnime's "External" folder (part
// of ObjectPack, resident from boot like Alink: d_s_boot.cpp) into our own buffers. The player gives one animation up to 4 x 0x2400 bytes (loadTwoAnmChrs).
static const u16 ANIM_WAIT_S = 26;       // "waitS" (d_player_mdl.cpp: loadBody uses sAnimations[26])
static const u16 ANIM_COUNT = 443;       // PLAYER_ANIM_MAX: "no animation" in mAnimations
static const u32 SLOT_BUFFER = 0x2400 * 4;

// Equipment as in daPlayerModelBase_c::initSwordModel / initShieldModel (d_player_mdl.cpp). Sword index 6 is
// "no sword" (getCurrentSwordType). Shield type = shield id / 3 (getCurrentlyEquippedShieldType); the texture
// pattern frame id % 3 picks the variant within a type. Hylian (type 3) has no pattern.
static const u8 COOP_SWORD_NONE = 6;
static const u8 COOP_SHIELD_NONE = 0xFF;
static const u8 COOP_SHIELD_IDS = 10;
static const u8 COOP_SHIELD_HYLIA = 3;
static const char *const sSwordPods[] = {"EquipPodA", "EquipPodB", "EquipPodC", "EquipPodD", "EquipPodE", "EquipPodMaster"};
static const char *const sShieldMdls[] = {"EquipShieldWood", "EquipShieldIron", "EquipShieldHoly", "EquipShieldHylia"};
// Held items (bow, slingshot, beetle, ...): loaded into their own frame heap and swapped without respawning,
// the way the player keeps its sword and shield in "LinkSword" / "LinkShield" heaps (initModelHeaps). The largest
// file (EquipBottle) unpacks to 115 KB.
#include "coop/held_models.inc"
static const u32 HELD_HEAP_SIZE = 0x30000;
static const u8 HELD_NONE = 0xFF;
static const u32 HELD_MODEL_COUNT = sizeof(sHeldModels) / sizeof(sHeldModels[0]);

static const f32 EQUIP_ROT_SCALE = 1.0f / 8192.0f;
static const f32 EQUIP_POS_SCALE = 1.0f / 16.0f;

// Node ids from daPlayerModelBase_c (d_player_mdl.h).
static const u32 BODY_NODE_CENTER = 0;
static const u32 BODY_NODE_BACKBONE_1 = 1;
static const u32 BODY_NODE_HEAD = 4;
static const u32 BODY_NODE_HAND_L = 9;
static const u32 BODY_NODE_HAND_R = 14;
static const u32 BODY_NODE_WAIST = 16;
static const u32 HANDS_NODE_HAND_L = 1;
static const u32 HANDS_NODE_HAND_R = 2;

dAcCoopPuppet_c::dAcCoopPuppet_c()
    : mpShared(nullptr),
      mpAnimBuf(nullptr),
      mLastSeq(0),
      mTunic(0),
      mHasTarget(false),
      mHidden(false),
      mAnimOk(false),
      mSword(COOP_SWORD_NONE),
      mShield(COOP_SHIELD_NONE),
      mHeldHeapReady(false),
      mpEnemies(nullptr) {
    for (int i = 0; i < ENEMY_MAX; i++) {
        mFrameSeenId[i] = 0xFFFF;
        mFrameSeenSeq[i] = 0;
    }
    mSlotAnim[0] = mSlotAnim[1] = ANIM_COUNT;
    for (int i = 0; i < HELD_SLOTS; i++) {
        mHeldLoaded[i] = HELD_NONE;
        mHeldOk[i] = false;
    }
    for (int i = 0; i < EQUIP_COUNT; i++) {
        mEquipOk[i] = false;
    }
    progress(mParams, 1);
}

dAcCoopPuppet_c::~dAcCoopPuppet_c() {
    progress(mParams, 50); // members are destroyed after this
}

void dAcCoopPuppet_c::note(u32 event) {
    if (mpShared != nullptr) {
        mpShared->lastEvent = event;
        mpShared->events++;
        mpShared->history[mpShared->historyIdx & 7] = event;
        mpShared->historyIdx++;
    }
}

// Progress marker in Shared::reserved[0], for finding where the game hangs (see NOTES.md, build 10).
// Uses mParams, which fBase_c sets before our constructor runs, so it works before create() too.
static void progress(u32 params, u32 step) {
    if (params >= 0x80000000 && params < 0x81800000) {
        reinterpret_cast<dAcCoopPuppet_c::Shared *>(params)->reserved[0] = step;
    }
}

void dAcCoopPuppet_c::HandsCallback::ExecCallbackC(
    nw4r::math::MTX34 *pMtxArray, nw4r::g3d::ResMdl mdl, nw4r::g3d::FuncObjCalcWorld *pFuncObj
) {
    // Same as daPlayerModelBase_c::handsCallbackC: the hands model's two hand joints take the body's hand joints.
    mpOwner->mBodyMdl.getNodeWorldMtx(BODY_NODE_HAND_L, &pMtxArray[mdl.GetResNode(HANDS_NODE_HAND_L).GetMtxID()]);
    mpOwner->mBodyMdl.getNodeWorldMtx(BODY_NODE_HAND_R, &pMtxArray[mdl.GetResNode(HANDS_NODE_HAND_R).GetMtxID()]);
}

// The local Link sets envelope_mtx_mode = APPROX on the shared ResMdl of the body it wears
// (daPlayerModelBase_c::loadBodyModels), and our model then stays in the bind pose (seen 2026-10-25: RAM showed
// mode 1 on pl_body, 0 on al). So for that body we draw from a private copy of its MDL0 block (~87 KB) with the
// mode back to NORMAL, in our own actor heap.
// History: a bare block copy failed AnmObjChr::Bind, because names are offsets into the BRRES string pool outside
// the block; a whole-file copy (~820 KB) in workExHeap left too little room there, and the next allocation hung
// the game (seen 2026-10-25, progress marker 13; workExHeap had ~850 KB free). So: block copy, and every name
// offset that points outside the block is rebased to the resident original.
static void rebaseOutward(u8 *copy, const u8 *orig, u32 size, u32 fieldOff, u32 baseOff) {
    s32 *field = reinterpret_cast<s32 *>(copy + fieldOff);
    const u8 *target = orig + baseOff + *field;
    if (*field != 0 && (target < orig || target >= orig + size)) {
        *field += static_cast<s32>(orig - copy);
    }
}

nw4r::g3d::ResMdl dAcCoopPuppet_c::ownBodyResource(const char *name) {
    nw4r::g3d::ResMdl body = mRes.GetResMdl(name);
    nw4r::g3d::ResMdlData &src = body.ref();
    if (src.info.envelope_mtx_mode == nw4r::g3d::ResMdlInfoDataTypedef::EVPMATRIXMODE_NORMAL) {
        return body;
    }
    u32 size = src.header.size;
    u8 *copy = static_cast<u8 *>(mAllocator.alloc(size));
    if (copy == nullptr) {
        note(EVENT_COPY_FAIL);
        return body; // no room: T-posed, but visible
    }
    const u8 *orig = reinterpret_cast<const u8 *>(&src);
    memcpy(copy, orig, size);
    nw4r::g3d::ResMdlData *dst = reinterpret_cast<nw4r::g3d::ResMdlData *>(copy);
    rebaseOutward(copy, orig, size, 0x0C, 0); // toResFileData
    rebaseOutward(copy, orig, size, 0x48, 0); // name
    // The 13 dictionaries (toResByteCodeDic 0x10 .. toResPlttNameToTexPlttInfoDic 0x40): entry name offsets are
    // relative to the dictionary. Node and material dictionaries also lead to data whose name (+0x8) is relative
    // to that data.
    for (u32 dicField = 0x10; dicField <= 0x40; dicField += 4) {
        s32 dicOff = *reinterpret_cast<const s32 *>(orig + dicField);
        if (dicOff <= 0) {
            continue;
        }
        const nw4r::g3d::ResDicData *dic = reinterpret_cast<const nw4r::g3d::ResDicData *>(orig + dicOff);
        for (u32 e = 0; e <= dic->numData; e++) {
            u32 entryOff = dicOff + 8 + e * sizeof(nw4r::g3d::ResDicNodeData);
            rebaseOutward(copy, orig, size, entryOff + 0x8, dicOff); // ofsString
            if (e != 0 && (dicField == 0x14 || dicField == 0x30)) {   // toResNodeDic, toResMatDic
                s32 dataOff = dicOff + dic->data[e].ofsData;
                rebaseOutward(copy, orig, size, dataOff + 0x8, dataOff); // ResNodeData/ResMatData::name
            }
        }
    }
    dst->info.envelope_mtx_mode = nw4r::g3d::ResMdlInfoDataTypedef::EVPMATRIXMODE_NORMAL;
    DCFlushRange(copy, size);
    return nw4r::g3d::ResMdl(copy);
}

// Like daPlayerModelBase_c::getExternalCompressedFile, but only from PLCommonAnime's External folder and only
// into our own buffer. getExternalAnmChr would first look in the local Link's mCurrentRes (situational
// animations) and hand back a pointer into it, which the game may unload while we still use it.
nw4r::g3d::ResAnmChr dAcCoopPuppet_c::loadAnim(u16 idx, void *dest) {
    nw4r::g3d::ResAnmChr none;
    if (idx >= ANIM_COUNT || !mAnimRes.IsValid()) {
        return none;
    }
    const char *name = daPlayerModelBase_c::sAnimations[idx].animName;
    SizedString<64> file;
    file.sprintf("%s.rchaC", name);
    const void *src = mAnimRes.GetExternalData(file);
    if (src == nullptr || CXGetUncompressedSize(src) > SLOT_BUFFER) {
        return none; // not an archive animation (comes from an item/situation resource) or too big: keep the old
    }
    lzSafeDecompress(src, SLOT_BUFFER, dest);
    nw4r::g3d::ResFile res(dest);
    res.Init();
    return res.GetResAnmChr(name);
}

// Put animation idx into slot 0 (lower body) or 1 (upper body), the way daPlayerModelBase_c::loadAnmChr does.
bool dAcCoopPuppet_c::setSlotAnim(int slot, u16 idx) {
    nw4r::g3d::ResAnmChr anm = loadAnim(idx, mpAnimBuf + slot * SLOT_BUFFER);
    if (!anm.IsValid()) {
        return false;
    }
    mSlots[slot].setAnm(mBodyMdl, anm, m3d::PLAY_MODE_0);
    nw4r::g3d::AnmObjChr *obj = static_cast<nw4r::g3d::AnmObjChr *>(mSlots[slot].getAnimObj());
    obj->Release();
    nw4r::g3d::ResMdl body = mBodyMdl.getResMdl();
    if (slot == 0) {
        obj->Bind(body, BODY_NODE_WAIST, nw4r::g3d::AnmObjChr::BIND_PARTIAL);
        obj->Bind(body, BODY_NODE_CENTER, nw4r::g3d::AnmObjChr::BIND_ONE);
    } else {
        obj->Bind(body, BODY_NODE_BACKBONE_1, nw4r::g3d::AnmObjChr::BIND_PARTIAL);
    }
    mBlend.attach(slot, &mSlots[slot], 1.0f);
    // anmChr_c::setAnm took the blend off the model (removeAnm by type): put it back.
    mBodyMdl.setAnm(mBlend);
    mSlotAnim[slot] = idx;
    return true;
}

// Like daPlayerModelBase_c::getItemResFile, but an allocation failure skips the model instead of decompressing
// into a null pointer (a failed allocation hangs the game, see NOTES.md build 10).
bool dAcCoopPuppet_c::loadItem(const char *name, nw4r::g3d::ResFile &out, mAllocator_c &allocator) {
    SizedString<64> file;
    file.sprintf("dat/%s.brresC", name);
    const void *src = getOarcFile("PLCompItem", file);
    if (src == nullptr) {
        return false;
    }
    u32 size = CXGetUncompressedSize(src);
    void *buf = allocator.alloc(size);
    if (buf == nullptr) {
        return false;
    }
    lzSafeDecompress(src, size, buf);
    nw4r::g3d::ResFile res(buf);
    res.Init();
    res.Bind();
    BindSystemModelsAndLighting(res);
    out = res;
    return true;
}

bool dAcCoopPuppet_c::createEquipMdl(int which, nw4r::g3d::ResFile res, const char *name) {
    nw4r::g3d::ResMdl mdl = res.GetResMdl(name);
    mEquipOk[which] = mdl.IsValid() && mEquipMdl[which].create(mdl, &mAllocator, 0x120);
    return mEquipOk[which];
}

void dAcCoopPuppet_c::loadEquip() {
    nw4r::g3d::ResFile res;
    bool ok = true;
    if (mSword < COOP_SWORD_NONE) {
        const char *name = daPlayerActBase_c::getSwordName(mSword);
        ok = loadItem(name, res, mAllocator) && createEquipMdl(EQUIP_SWORD, res, name) &&
             createEquipMdl(EQUIP_SHEATH, res, sSwordPods[mSword]);
    }
    if (ok && mShield < COOP_SHIELD_IDS) {
        u8 type = mShield / 3;
        ok = loadItem(sShieldMdls[type], res, mAllocator) && createEquipMdl(EQUIP_SHIELD, res, sShieldMdls[type]);
        if (ok && type != COOP_SHIELD_HYLIA) {
            nw4r::g3d::ResAnmTexPat pat = res.GetResAnmTexPat(sShieldMdls[type]);
            if (pat.IsValid() && mShieldTexPat.create(mEquipMdl[EQUIP_SHIELD].getResMdl(), pat, &mAllocator, nullptr, 1)) {
                mEquipMdl[EQUIP_SHIELD].setAnm(mShieldTexPat);
                mShieldTexPat.setFrame(mShield % 3, 0);
            }
        }
    }
    if (!ok) {
        note(EVENT_EQUIP_FAIL);
    }
}

// Load the remote Link's held item models when it shows a set we do not have. When it puts them away we keep
// them (just not drawn), so taking the same item out again costs nothing.
void dAcCoopPuppet_c::updateHeld() {
    if (mpShared == nullptr || !(mpShared->mailFlags & MAIL_HELD) || mpShared->heldDrawn == 0) {
        return;
    }
    bool same = true;
    for (int i = 0; i < HELD_SLOTS; i++) {
        u8 want = (mpShared->heldDrawn & (1 << i)) ? mpShared->heldId[i] : HELD_NONE;
        if (want != HELD_NONE && want != mHeldLoaded[i]) {
            same = false;
        }
    }
    if (same) {
        return;
    }
    // Not getHeap() == nullptr: a fresh mAllocator_c points at mHeap::g_assertHeap, and freeing that as a frame
    // heap crashed the game (build 14, 2026-10-25: invalid read in EGG::FrmHeap::free).
    if (!mHeldHeapReady) {
        mHeldHeapReady =
            mHeldAllocator.createFrmHeap(HELD_HEAP_SIZE, dHeap::work1Heap.heap, "CoopPuppetHeld", 0x20, mHeap::OPT_NONE);
    }
    EGG::FrmHeap *heap = mHeldHeapReady ? EGG::Heap::toFrmHeap(mHeldAllocator.getHeap()) : nullptr;
    if (heap == nullptr) {
        note(EVENT_HELD_FAIL);
        return;
    }
    for (int i = 0; i < HELD_SLOTS; i++) {
        if (mHeldOk[i]) {
            mHeldMdl[i].remove();
        }
        mHeldOk[i] = false;
        mHeldLoaded[i] = HELD_NONE;
    }
    heap->free(0x1 | 0x2);
    const char *loadedFile = nullptr;
    nw4r::g3d::ResFile res;
    for (int i = 0; i < HELD_SLOTS; i++) {
        u8 id = mpShared->heldId[i];
        if (!(mpShared->heldDrawn & (1 << i)) || id >= HELD_MODEL_COUNT) {
            continue;
        }
        const char *file = sHeldModels[id][0];
        if (loadedFile == nullptr || strcmp(loadedFile, file) != 0) {
            if (!loadItem(file, res, mHeldAllocator)) {
                note(EVENT_HELD_FAIL);
                return;
            }
            loadedFile = file;
        }
        nw4r::g3d::ResMdl mdl = res.GetResMdl(sHeldModels[id][1]);
        if (!mdl.IsValid() || !mHeldMdl[i].create(mdl, &mHeldAllocator, 0x120)) {
            note(EVENT_HELD_FAIL);
            return;
        }
        mHeldOk[i] = true;
        mHeldLoaded[i] = id;
    }
    note(EVENT_HELD_LOAD);
}

bool dAcCoopPuppet_c::createHeap() {
    // "Alink" is loaded at boot and stays resident (d_s_boot.cpp); arrows and bombs use it the same way.
    progress(mParams, 10);
    mRes = nw4r::g3d::ResFile(getOarcResFile("Alink"));
    mAnimRes = nw4r::g3d::ResFile(getOarcResFile("PLCommonAnime"));
    u32 t = mTunic < 2 ? mTunic : 0;
    nw4r::g3d::ResMdl body = ownBodyResource(sBodyMdls[t]);
    progress(mParams, 11);
    if (!mBodyMdl.create(body, &mAllocator, 0x120) || !mHeadMdl.create(mRes.GetResMdl(sHeadMdls[t]), &mAllocator, 0x120) ||
        !mFaceMdl.create(mRes.GetResMdl(sFaceMdls[t]), &mAllocator, 0x120) ||
        !mHandsMdl.create(mRes.GetResMdl(sHandMdls[t]), &mAllocator, 0x120)) {
        note(EVENT_HEAP_FAIL);
        releaseSlot();
        return false;
    }

    progress(mParams, 12);
    mHandsCallback.mpOwner = this;
    nw4r::g3d::ScnMdlSimple *hands = nw4r::g3d::ScnObj::DynamicCast<nw4r::g3d::ScnMdlSimple>(mHandsMdl.getG3dObject());
    if (hands != nullptr) {
        hands->SetScnMdlCallback(&mHandsCallback);
        hands->EnableScnMdlCallbackTiming(nw4r::g3d::ScnObj::CALLBACK_TIMING_C);
    }

    // Animation setup as in daPlayerModelBase_c::loadBody: a blend with one anmChr per slot, both on "waitS",
    // slot 0 bound to the lower body, slot 1 to the upper body.
    mAnimOk = false;
    progress(mParams, 13);
    mpAnimBuf = static_cast<u8 *>(mAllocator.alloc(SLOT_BUFFER * SLOT_COUNT)); // actor heap: work1Heap has room
    progress(mParams, 14);
    if (mpAnimBuf == nullptr) {
        note(EVENT_ANIM_BUFFER_FAIL);
    } else {
        nw4r::g3d::ResAnmChr wait = loadAnim(ANIM_WAIT_S, mpAnimBuf);
        if (!wait.IsValid()) {
            note(EVENT_ANIM_LOAD_FAIL);
        } else if (progress(mParams, 15), !mBlend.create(body, SLOT_COUNT, &mAllocator)) {
            note(EVENT_ANIM_CREATE_FAIL);
        } else {
            mAnimOk = true;
            for (int i = 0; i < SLOT_COUNT && mAnimOk; i++) {
                if (!mSlots[i].create2(body, wait, &mAllocator)) {
                    note(EVENT_ANIM_CREATE_FAIL);
                    mAnimOk = false;
                }
            }
            progress(mParams, 16);
            if (mAnimOk) {
                mBodyMdl.setAnm(mBlend);
                for (int i = 0; i < SLOT_COUNT; i++) {
                    setSlotAnim(i, ANIM_WAIT_S);
                    progress(mParams, 17 + i);
                }
            }
        }
    }
    if (!mAnimOk) {
        note(EVENT_ANIM_FAIL); // keep going: a T-posed puppet is better than none
    }

    loadEquip();
    progress(mParams, 19);
    mpEnemies = static_cast<EnemyTable *>(mAllocator.alloc(sizeof(EnemyTable)));
    if (mpEnemies == nullptr) {
        note(EVENT_TABLE_FAIL);
    } else {
        memset(mpEnemies, 0, sizeof(EnemyTable));
    }

    updateMatrix();
    placeParts();
    progress(mParams, 20);
    return true;
}

int dAcCoopPuppet_c::create() {
    progress(mParams, 2);
    // The spawn hook passes the address of its shared block as params (MEM1 only).
    if (mParams >= 0x80000000 && mParams < 0x81800000) {
        mpShared = reinterpret_cast<Shared *>(mParams);
        mTunic = mpShared->tunic;
        if (mpShared->mailFlags & MAIL_EQUIP) {
            mSword = mpShared->sword;
            mShield = mpShared->shield;
        }
    }
    if (!initAllocatorWork1Heap(-1, "dAcCoopPuppet_c::m_allocator", 0x20)) {
        note(EVENT_ALLOC_FAIL);
        releaseSlot();
        return FAILED;
    }
    if (mpShared != nullptr) {
        mpShared->puppet = reinterpret_cast<u32>(this);
        mpShared->pad = PUPPET_BUILD; // lets the dev tools see which build is running
    }
    mPosition.x += FRONT_OFFSET * mRotation.y.sin();
    mPosition.z += FRONT_OFFSET * mRotation.y.cos();
    mBoundingBox.Set(mVec3_c(-60.0f, 0.0f, -60.0f), mVec3_c(60.0f, 180.0f, 60.0f));
    note(EVENT_CREATE_OK);
    progress(mParams, 30);
    return SUCCEEDED;
}

int dAcCoopPuppet_c::doDelete() {
    progress(mParams, 40);
    if (mpShared != nullptr && mpShared->enemyTable == reinterpret_cast<u32>(mpEnemies)) {
        mpShared->enemyTable = 0; // the table goes with our heap
    }
    note(EVENT_DELETE);
    releaseSlot();
    progress(mParams, 41);
    return SUCCEEDED;
}

// Tell the spawn hook we are gone. Its own liveness check (profile id at the stored pointer) is fooled when the
// freed memory still holds our old fields (seen 2026-10-25: deleted, never respawned).
void dAcCoopPuppet_c::releaseSlot() {
    if (mpShared != nullptr && mpShared->puppet == reinterpret_cast<u32>(this)) {
        mpShared->puppet = 0;
        // Give our heap time to be freed before the hook creates the replacement.
        mpShared->cooldown = RESPAWN_DELAY_FRAMES;
    }
}

void dAcCoopPuppet_c::followMail() {
    if (mpShared == nullptr || !(mpShared->mailFlags & MAIL_BRIDGE)) {
        mHidden = false; // no bridge: stay where we spawned
        return;
    }
    if (!(mpShared->mailFlags & MAIL_VISIBLE)) {
        mHidden = true; // remote player is in another stage
        mHasTarget = false;
        return;
    }
    mHidden = false;
    mVec3_c target(mpShared->posX, mpShared->posY, mpShared->posZ);
    mVec3_c diff = target - mPosition;
    if (!mHasTarget || diff.squaredLength() > SNAP_DISTANCE * SNAP_DISTANCE) {
        mPosition = target;
        mHasTarget = true;
    } else {
        mPosition += diff * FOLLOW_RATE;
    }
    mRotation.y = mpShared->rotY;
}

void dAcCoopPuppet_c::updateAnim() {
    if (!mAnimOk) {
        return;
    }
    bool remote = mpShared != nullptr && (mpShared->mailFlags & (MAIL_BRIDGE | MAIL_VISIBLE | MAIL_ANIM)) ==
                                             (MAIL_BRIDGE | MAIL_VISIBLE | MAIL_ANIM);
    bool fresh = remote && mpShared->mailSeq != mLastSeq;
    for (int i = 0; i < SLOT_COUNT; i++) {
        if (remote) {
            u16 want = mpShared->anim[i];
            if (want < ANIM_COUNT && want != mSlotAnim[i]) {
                setSlotAnim(i, want);
            }
        }
        if (fresh && mSlotAnim[i] == mpShared->anim[i]) {
            // Follow the remote frame; play() covers the frames between. (Not fanm_c::setFrame: it is not in the
            // game binary, the REL link failed on it. fanm_c::play itself calls AnmObj::SetFrame.)
            mSlots[i].getAnimObj()->SetFrame(mpShared->frame[i]);
        } else {
            mSlots[i].play();
        }
    }
    if (remote) {
        mLastSeq = mpShared->mailSeq;
    }
}

void dAcCoopPuppet_c::placeParts() {
    mBodyMdl.setLocalMtx(mWorldMtx);
    mBodyMdl.calc(false);
    // Head and face ride on the body's head joint; the hands model gets its hand joints in HandsCallback.
    mMtx_c head;
    mBodyMdl.getNodeWorldMtx(BODY_NODE_HEAD, head);
    mHeadMdl.setLocalMtx(head);
    mHeadMdl.calc(false);
    mFaceMdl.setLocalMtx(head);
    mFaceMdl.calc(false);
    mHandsMdl.setLocalMtx(mWorldMtx);
    mHandsMdl.calc(false);
    // Equipment: the remote model's matrix relative to the remote body, put on our body.
    if (mpShared == nullptr) {
        return;
    }
    for (int i = 0; i < EQUIP_COUNT && (mpShared->mailFlags & MAIL_EQUIP); i++) {
        if (mEquipOk[i]) {
            placeRel(mEquipMdl[i], mpShared->equipMtx[i]);
        }
    }
    if (mpShared->mailFlags & MAIL_HELD) {
        for (int i = 0; i < HELD_SLOTS; i++) {
            if (mHeldOk[i] && (mpShared->heldDrawn & (1 << i)) && mpShared->heldId[i] == mHeldLoaded[i]) {
                placeRel(mHeldMdl[i], mpShared->heldMtx[i]);
            }
        }
    }
}

// A remote model's matrix relative to the remote body, put on our body.
void dAcCoopPuppet_c::placeRel(m3d::smdl_c &mdl, const s16 *q) {
    mMtx_c rel;
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 4; c++) {
            rel.m[r][c] = q[r * 4 + c] * (c == 3 ? EQUIP_POS_SCALE : EQUIP_ROT_SCALE);
        }
    }
    mMtx_c mtx;
    MTXConcat(mWorldMtx, rel, mtx);
    mdl.setLocalMtx(mtx);
    mdl.calc(false);
}

// Remove live actors the remote player defeated. The bridge has already set their enemy defeat flags (live and
// committed copy), which only stops them from spawning again (d_stage_parse.cpp checks the flag at room load);
// ones that are alive right now go here, the way dAcBase_c::deleteWithFlagNoItemDrop does it: no drop, no kill
// counter. Placement ids are (room << 10) | index (getEnemyDefeatFlagFromId), the same in both games.
void dAcCoopPuppet_c::applyRemoteKills() {
    if (mpShared == nullptr || mpShared->killSeq == mpShared->killAck) {
        return;
    }
    static const u8 groups[] = {dAcBase_c::ACTOR, dAcBase_c::STAGE};
    for (int g = 0; g < 2; g++) {
        for (fBase_c *b = fManager_c::searchBaseByGroupType(groups[g], nullptr); b != nullptr;
             b = fManager_c::searchBaseByGroupType(groups[g], b)) {
            dAcBase_c *ac = static_cast<dAcBase_c *>(b);
            if (ac == this || ac->mObjID == 0xFFFF || ac->mLifecycleState == fBase_c::TO_BE_DELETED) {
                continue;
            }
            for (int k = 0; k < KILL_SLOTS; k++) {
                if (mpShared->kills[k] == ac->mObjID) {
                    ac->deleteWithFlagNoItemDrop();
                    note(EVENT_REMOTE_KILL);
                    break;
                }
            }
        }
    }
    mpShared->killAck = mpShared->killSeq;
}

// Enemies that can be shared: placed ones (a real mObjID) of these profiles. Not bosses (B_*), and not the parts and
// projectiles that enemies spawn themselves (arrows, cannon shots, tails, leaves, wires, shields, rocks), nor the
// Silent Realm guardians, which follow their own rules.
static bool isSharedEnemy(u16 profile) {
    static const u16 profiles[] = {
        0xAB,  0xC0,  0xC1,  0xC2,  0x14F, 0x155, 0x156, 0x158, 0x15A, 0x15C, 0x15F, 0x162, 0x163, 0x164,
        0x166, 0x167, 0x168, 0x169, 0x16D, 0x16E, 0x170, 0x171, 0x173, 0x174, 0x175, 0x176, 0x177, 0x178,
        0x179, 0x17A, 0x17B, 0x17C, 0x17E, 0x17F, 0x180, 0x184, 0x185, 0x186, 0x187, 0x188, 0x18A, 0x18B,
        0x18D, 0x18E, 0x190, 0x1A5,
    };
    for (u32 i = 0; i < sizeof(profiles) / sizeof(profiles[0]); i++) {
        if (profiles[i] == profile) {
            return true;
        }
    }
    return false;
}

// Enemy types whose animation we sync: where their d3d::AnmMdlWrapper (model + animation) sits, from the
// decomp headers. Checked against the wrapper's vtable before use, so a wrong offset in a not-yet-matching header
// only means "no animation sync" for that type.
struct EnemyAnimType {
    u16 profile;
    u16 offset;
};
static const EnemyAnimType sEnemyAnimTypes[] = {
    {0x16D, 0x388}, // E_KS Keese (dAcEKs_c::mMdl)
    {0x155, 0x378}, // E_SM ChuChu (dAcESm_c::mMdl)
    {0x170, 0x3BC}, // E_REMLY Remlit (dAcEremly_c::mMdl)
    {0x185, 0x378}, // E_SKYTAIL (body)
};
static const u32 VT_ANM_MDL_WRAPPER = 0x80500A20;  // __vt__Q23d3d13AnmMdlWrapper
static const u32 VT_ANM_MDL_WRAPPER2 = 0x805009D0; // __vt__Q23d3d14AnmMdlWrapper2
static const u16 ANIM_NONE = 0xFFFF;
static const u16 ANIM_IN_MDL_FILE = 0x8000;

static d3d::AnmMdlWrapper *enemyAnimModel(dAcBase_c *ac) {
    for (u32 i = 0; i < sizeof(sEnemyAnimTypes) / sizeof(sEnemyAnimTypes[0]); i++) {
        if (sEnemyAnimTypes[i].profile == ac->mProfileName) {
            u8 *p = reinterpret_cast<u8 *>(ac) + sEnemyAnimTypes[i].offset;
            u32 vt = *reinterpret_cast<u32 *>(p);
            if (vt == VT_ANM_MDL_WRAPPER || vt == VT_ANM_MDL_WRAPPER2) {
                return reinterpret_cast<d3d::AnmMdlWrapper *>(p);
            }
            return nullptr;
        }
    }
    return nullptr;
}

static nw4r::g3d::ResAnmChr currentAnim(d3d::AnmMdlWrapper *w) {
    nw4r::g3d::AnmObjChrRes *obj = nw4r::g3d::AnmObj::DynamicCast<nw4r::g3d::AnmObjChrRes>(w->getAnm().getAnimObj());
    return obj != nullptr ? obj->GetResAnm() : nw4r::g3d::ResAnmChr();
}

// The binary has only ResFile::GetResAnmChr(name), so we read the BRRES index ourselves (the layout
// mod/tools/brres_ls.py reads): root dictionary at header +0x0C (+8), folder "AnmChr(NW4R)", one entry per
// animation. Dictionary: numData at +4, entries of 16 bytes from +8 (entry 0 is the root), name and data
// offsets at +8 and +12 of an entry, relative to the dictionary.
static const u8 *animDic(nw4r::g3d::ResFile file) {
    if (!file.IsValid()) {
        return nullptr;
    }
    const u8 *base = reinterpret_cast<const u8 *>(file.ptr());
    const u8 *root = base + *reinterpret_cast<const u16 *>(base + 0x0C) + 8;
    u32 n = *reinterpret_cast<const u32 *>(root + 4);
    for (u32 e = 1; e <= n; e++) {
        const u8 *entry = root + 8 + e * 16;
        if (strcmp(reinterpret_cast<const char *>(root + *reinterpret_cast<const s32 *>(entry + 8)), "AnmChr(NW4R)") == 0) {
            return root + *reinterpret_cast<const s32 *>(entry + 12);
        }
    }
    return nullptr;
}

// Index of the current animation in the wrapper's animation file (or, bit 15, its model file).
static u16 animIndex(d3d::AnmMdlWrapper *w) {
    nw4r::g3d::ResAnmChr cur = currentAnim(w);
    if (!cur.IsValid()) {
        return ANIM_NONE;
    }
    nw4r::g3d::ResFile files[2] = {w->getAnmFile(), w->getMdlFile()};
    for (u16 f = 0; f < 2; f++) {
        const u8 *dic = animDic(files[f]);
        if (dic == nullptr) {
            continue;
        }
        u32 n = *reinterpret_cast<const u32 *>(dic + 4);
        for (u32 e = 1; e <= n && e < ANIM_IN_MDL_FILE; e++) {
            const u8 *entry = dic + 8 + e * 16;
            if (dic + *reinterpret_cast<const s32 *>(entry + 12) == reinterpret_cast<const u8 *>(cur.ptr())) {
                return static_cast<u16>((e - 1) | (f ? ANIM_IN_MDL_FILE : 0));
            }
        }
    }
    return ANIM_NONE;
}

static const char *animName(d3d::AnmMdlWrapper *w, u16 idx) {
    const u8 *dic = animDic((idx & ANIM_IN_MDL_FILE) ? w->getMdlFile() : w->getAnmFile());
    u32 e = (idx & ~ANIM_IN_MDL_FILE) + 1;
    if (dic == nullptr || e > *reinterpret_cast<const u32 *>(dic + 4)) {
        return nullptr;
    }
    return reinterpret_cast<const char *>(dic + *reinterpret_cast<const s32 *>(dic + 8 + e * 16 + 8));
}

// Whether frameSeq for this enemy is one we have not applied yet (and remember it).
bool dAcCoopPuppet_c::frameIsNew(u16 objId, u8 seq) {
    int slot = objId % ENEMY_MAX;
    if (mFrameSeenId[slot] == objId && mFrameSeenSeq[slot] == seq) {
        return false;
    }
    mFrameSeenId[slot] = objId;
    mFrameSeenSeq[slot] = seq;
    return true;
}

// List this game's live shared enemies for the bridge and apply its orders for the ones the other game owns.
// The enemy keeps running its own AI and animations; FOLLOW puts it back at the owner's position and facing every
// frame, so it shows where the owner's copy is (plus at most one frame of its own movement).
void dAcCoopPuppet_c::updateEnemies() {
    if (mpEnemies == nullptr || mpShared == nullptr) {
        return;
    }
    mpShared->enemyTable = reinterpret_cast<u32>(mpEnemies);
    EnemyTable *t = mpEnemies;
    u32 orders = t->orderCount < ENEMY_MAX ? t->orderCount : ENEMY_MAX;
    u32 n = 0;
    for (fBase_c *b = fManager_c::searchBaseByGroupType(dAcBase_c::ACTOR, nullptr); b != nullptr;
         b = fManager_c::searchBaseByGroupType(dAcBase_c::ACTOR, b)) {
        dAcBase_c *ac = static_cast<dAcBase_c *>(b);
        bool test = mpShared->testProfile != 0 && ac->mProfileName == mpShared->testProfile;
        if (ac->mObjID == 0xFFFF || ac->mLifecycleState != fBase_c::ACTIVE ||
            !(test || isSharedEnemy(ac->mProfileName))) {
            continue;
        }
        dAcEnBase_c *en = test ? nullptr : static_cast<dAcEnBase_c *>(ac);
        for (u32 k = 0; k < orders; k++) {
            const EnemyOrder &o = t->orders[k];
            if (o.objId != ac->mObjID) {
                continue;
            }
            if (o.flags & ORDER_FOLLOW) {
                ac->mPosition.set(o.pos[0], o.pos[1], o.pos[2]);
                ac->mRotation.y = o.rotY;
            }
            if (en != nullptr && o.healthCap < en->mHealth) {
                en->mHealth = o.healthCap;
            }
            d3d::AnmMdlWrapper *w = (o.flags & ORDER_ANIM) ? enemyAnimModel(ac) : nullptr;
            if (w != nullptr && o.anim != ANIM_NONE) {
                if (animIndex(w) != o.anim) {
                    const char *name = animName(w, o.anim);
                    if (name != nullptr) {
                        w->setAnm(name, static_cast<m3d::playMode_e>(o.mode), 0.0f); // virtual: also its sounds
                        w->setFrame(o.frame);
                    }
                } else if (frameIsNew(o.objId, o.frameSeq)) {
                    w->setFrame(o.frame);
                }
            }
            break;
        }
        if (mpShared->testAnimObj != 0 && mpShared->testAnimObj == ac->mObjID) {
            d3d::AnmMdlWrapper *tw = enemyAnimModel(ac);
            const char *name = tw != nullptr ? animName(tw, mpShared->testAnimIdx) : nullptr;
            if (name != nullptr) {
                tw->setAnm(name, m3d::PLAY_MODE_4, 0.0f);
            }
            mpShared->testAnimObj = 0;
        }
        if (n < ENEMY_MAX) {
            EnemyEntry &e = t->entries[n++];
            e.objId = ac->mObjID;
            e.profile = ac->mProfileName;
            e.pos[0] = ac->mPosition.x;
            e.pos[1] = ac->mPosition.y;
            e.pos[2] = ac->mPosition.z;
            e.rotY = ac->mRotation.y;
            e.health = en != nullptr ? en->mHealth : 0xFFFF;
            d3d::AnmMdlWrapper *w = enemyAnimModel(ac);
            e.anim = w != nullptr ? animIndex(w) : ANIM_NONE;
            e.mode = w != nullptr ? w->getAnm().getPlayMode() : 0;
            e.pad = 0;
            e.frame = (w != nullptr && w->getAnm().getAnimObj() != nullptr) ? w->getAnm().getAnimObj()->GetFrame() : 0.0f;
        }
    }
    t->count = n;
    t->seq++;
}

int dAcCoopPuppet_c::actorExecute() {
    if (mpShared != nullptr) {
        mpShared->execs++;
        if ((mpShared->mailFlags & MAIL_BRIDGE) && mpShared->tunic != mTunic) {
            note(EVENT_TUNIC_CHANGE);
            deleteRequest(); // the hook spawns a new puppet with the new outfit
            return SUCCEEDED;
        }
        if ((mpShared->mailFlags & MAIL_EQUIP) && (mpShared->sword != mSword || mpShared->shield != mShield)) {
            note(EVENT_EQUIP_CHANGE);
            deleteRequest(); // respawn with the new sword/shield models
            return SUCCEEDED;
        }
    }
    applyRemoteKills();
    updateEnemies();
    followMail();
    updateMatrix();
    updateAnim();
    updateHeld();
    placeParts();
    return SUCCEEDED;
}

int dAcCoopPuppet_c::draw() {
    if (mHidden) {
        return SUCCEEDED;
    }
    drawModel(&mBodyMdl);
    drawModel(&mHeadMdl);
    drawModel(&mFaceMdl);
    drawModel(&mHandsMdl);
    if (mpShared != nullptr && (mpShared->mailFlags & MAIL_EQUIP)) {
        for (int i = 0; i < EQUIP_COUNT; i++) {
            if (mEquipOk[i] && (mpShared->equipDrawn & (1 << i))) {
                drawModel(&mEquipMdl[i]);
            }
        }
    }
    if (mpShared != nullptr && (mpShared->mailFlags & MAIL_HELD)) {
        for (int i = 0; i < HELD_SLOTS; i++) {
            if (mHeldOk[i] && (mpShared->heldDrawn & (1 << i)) && mpShared->heldId[i] == mHeldLoaded[i]) {
                drawModel(&mHeldMdl[i]);
            }
        }
    }
    return SUCCEEDED;
}
