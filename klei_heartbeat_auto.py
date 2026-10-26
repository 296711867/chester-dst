#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
klei_heartbeat_auto.py — 双击运行：黑窗口实时日志 + 每 360 秒心跳 + 有礼物自动领取。

工作流（C 层逆向 + 实测，2026-08-30）:
  每 360s（C 层 const float 360.0 @0x140554eb4）
    ├─ POST items.kleientertainment.com/clientitems/DST/Tick  {"Token": <token>}
    │     → 服务端按心跳推进每周礼物掉落
    │     → 有礼: {"GiftReceived":{"Key":"<gift key>"}}
    └─ 有礼 → 领取（3 步，对应 C 层 / Lua）:
          1) POST clientitems/DST/VerifyGiftingReceipt
               {"Receipts":[{"UserID":"<KU_..>","Key":"<Key>"}]}   (0x140277da0)
          2) POST clientitems/DST/GetAllItems  → 找 Context==3 的未开礼物
          3) 循环 POST clientitems/DST/SetItemOpened
               {"ItemID":..,"ClientToken":..}                      (Lua TheInventory:SetItemOpened, C 层 0x1402587F0)
             Context 3→1，逐个揭示直至队列清空（Tick 门控 E_UNOPENED_ITEMS 解除）
  领取后立即再 Tick 一次，榨干可能堆叠的礼物（最多连续 5 次）。

Token 管理: LoginViaSteam 一次给 ~60min；到期前 5 分钟 TokenPurpose 续期，
           续期失败整轮重登（Steam 票据）。首次运行会弹 Steam 客户端登录。

用法:
  直接双击「心跳自动领取.bat」　（或）
  python klei_heartbeat_auto.py [--interval 360] [--once] [--no-logfile]
"""

import argparse
import json
import os
import sys
import time

import klei_heartbeat as HB
import klei_steam_login as K
import klei_open_gifts as G

PATH_RECEIPT = "/clientitems/DST/VerifyGiftingReceipt"
MAX_DRAIN = 5        # 一次领礼后最多连续再查/再领次数
SECRETS_DIR = HB.SECRETS_DIR   # 凭证目录(单一来源,见 klei_heartbeat.py;已被 .gitignore 排除)
SAVE_FILE = os.path.join(SECRETS_DIR, ".hb_auto_session.json")


def open_pending_gifts(token, logfile):
    """Open every currently queued Context=3 gift and return the opened count."""
    opened = 0
    for _ in range(G.MAX_CHAIN):
        _st, items, raw = G.get_items(token)
        if items is None:
            HB.log("GetAllItems 失败: %s" % raw[:120], logfile)
            break
        pending = [item for item in items
                   if isinstance(item, dict) and item.get("Context") == 3]
        if not pending:
            break
        item = pending[0]
        try:
            item_id = int(item["ItemID"])
            item_type = item["ItemType"]
        except (KeyError, TypeError, ValueError):
            HB.log("未开礼物字段异常，停止处理: %s" % str(item)[:120], logfile)
            break
        st, raw = HB.http_post_json(
            G.ITEMS_BASE, G.PATH_OPENED,
            {"ItemID": item_id, "ClientToken": token})
        try:
            response = json.loads(raw)
        except json.JSONDecodeError:
            response = None
        if not isinstance(response, dict) or response.get("Error"):
            HB.log("SetItemOpened 失败(HTTP=%s) %s" % (st, raw[:120]), logfile)
            break
        HB.log("★ 领取礼物 %s (%d)" % (item_type, item_id), logfile)
        opened += 1
        time.sleep(0.35)
    HB.log("本轮共领取 %d 个礼物" % opened, logfile)
    return opened


def claim_gift(token, userid, gift_key, logfile):
    """Verify one GiftReceived.Key, then open every queued gift."""
    body = {"Receipts": [{"UserID": userid, "Key": gift_key}]}
    st, raw = HB.http_post_json(HB.ITEMS_BASE, PATH_RECEIPT, body)
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        response = None
    ok = isinstance(response, dict) and not response.get("Error")
    HB.log("凭据回执 VerifyGiftingReceipt HTTP=%s %s -> %s" %
           (st, ("%s" % response)[:120] if response is not None else raw[:120],
            "OK" if ok else "失败"), logfile)
    return open_pending_gifts(token, logfile)


def main():
    ap = argparse.ArgumentParser(description="心跳+自动领取（360s 一轮）")
    ap.add_argument("--interval", type=int, default=HB.DEFAULT_INTERVAL,
                    help="心跳间隔秒（默认 360）")
    ap.add_argument("--once", action="store_true", help="只跑一轮后退出")
    ap.add_argument("--no-logfile", action="store_true", help="不写日志文件只打屏幕")
    ap.add_argument("--dll", default=None, help="steam_api64.dll 路径")
    ap.add_argument("--desc", default="standalone_hb_auto", help="FriendlyHostDescriptor")
    args = ap.parse_args()
    interval = max(30, args.interval)
    logfile = None if args.no_logfile else os.path.join(SECRETS_DIR, "hb_auto_console.log")
    dll = args.dll or K.find_steam_dll()

    HB.log("══════ Klei 心跳+自动领取 开始 ══════")
    HB.log("间隔=%ds  日志=%s" % (interval, logfile or "仅屏幕"), logfile)

    # ---- 登录（优先复用/续期）----
    token, userid = None, "?"
    if os.path.exists(SAVE_FILE):
        try:
            with open(SAVE_FILE, encoding="utf-8") as f:
                token = json.load(f).get("Token")
        except Exception:
            token = None
        if token:
            HB.log("复用会话 %s" % SAVE_FILE, logfile)
    if not token:
        token, userid, j, _steamid = HB.full_login(dll, args.desc)
        HB.log("登录完成 UserID=%s" % userid, logfile)
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(j, f)

    born = time.time()
    fam = 1                       # 第几轮心跳
    while True:
        since = int(time.time() - born)
        HB.log("── 第 %d 轮心跳（登录后 %s）──" % (fam, time.strftime("%H:%M:%S", time.gmtime(since))), logfile)

        # ---- 心跳 ----
        st, j, raw = HB.tick(token)
        if st == 0:                                   # 网络错误,别误报"心跳正常"
            HB.log("Tick 网络错误: %s（下轮自动重试）" % raw[:120], logfile)
            if args.once:
                HB.log("--once 完成，退出", logfile)
                return 1
            fam += 1
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                HB.log("用户中断", logfile)
                return 0
            continue
        err = j.get("ErrorCode") if isinstance(j, dict) else ""
        if st == 401 or err == "E_INVALID_TOKEN":
            HB.log("心跳鉴权失效 %s → 续期/重登" % raw[:140], logfile)
            token, userid, src, jj = HB.refresh_or_relogin(token, dll, args.desc)
            HB.log("已%s（%s）" % ("续期" if src == "refresh" else "重登", userid), logfile)
            with open(SAVE_FILE, "w", encoding="utf-8") as f:
                json.dump(jj, f)
            fam += 1
            continue
        if j is None or st >= 400:                    # 非 JSON/HTTP 异常,别计成功
            HB.log("Tick 响应异常(HTTP=%s) %s" % (st, raw[:160]), logfile)
            if args.once:
                HB.log("--once 完成，退出", logfile)
                return 1
            fam += 1
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                HB.log("用户中断", logfile)
                return 0
            continue
        if isinstance(j, dict) and j.get("Error"):
            if err == "E_UNOPENED_ITEMS":
                HB.log("检测到未开礼物，自动清理 Context=3 队列", logfile)
                open_pending_gifts(token, logfile)
            else:
                HB.log("心跳业务错误 HTTP=%s %s" % (st, raw[:200]), logfile)
        else:
            gift = j.get("GiftReceived") if isinstance(j, dict) else None
            key = HB.get_gift_key(j)
            if key is not None:
                HB.log("🎁 心跳带礼物 Key=%s" % key, logfile)
                claim_gift(token, userid, key, logfile)
                # 榨干堆叠礼物：立即再查
                for _ in range(MAX_DRAIN):
                    _st2, j2, raw2 = HB.tick(token)
                    key2 = HB.get_gift_key(j2)
                    if key2 is None:
                        gift2 = j2.get("GiftReceived") if isinstance(j2, dict) else None
                        if gift2 is True:
                            HB.log("后续 Tick 报告有礼物但未提供 Key；扫描未开队列", logfile)
                            open_pending_gifts(token, logfile)
                        elif gift2 not in (None, False):
                            HB.log("后续 Tick 的 GiftReceived 格式异常，已停止榨取：%s" %
                                   raw2[:200], logfile)
                        break
                    HB.log("🎁 还有礼物 Key=%s" % key2, logfile)
                    claim_gift(token, userid, key2, logfile)
                    time.sleep(1)
            elif gift is True:
                HB.log("心跳报告有礼物但未提供 Key；跳过回执并扫描未开队列", logfile)
                open_pending_gifts(token, logfile)
            elif gift not in (None, False):
                HB.log("心跳 GiftReceived 格式异常，已忽略：%s" % raw[:200], logfile)
            else:
                HB.log("心跳正常，本轮无礼物（GiftReceived=%s）" % bool(gift), logfile)

        # ---- token 快到期续期 ----
        age = time.time() - born
        if age >= HB.TOKEN_LIFETIME - HB.REFRESH_EARLY:
            HB.log("Token 将到期，续期中…", logfile)
            token, userid, src, jj = HB.refresh_or_relogin(token, dll, args.desc)
            HB.log("已%s（%s），重新计时" % ("续期" if src == "refresh" else "重登", userid), logfile)
            with open(SAVE_FILE, "w", encoding="utf-8") as f:
                json.dump(jj, f)
            born = time.time()

        if args.once:
            HB.log("--once 完成，退出", logfile)
            return 0
        fam += 1
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            HB.log("用户中断", logfile)
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] 已退出")