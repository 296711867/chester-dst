#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
klei_open_gifts.py — 不开游戏直接"开礼物"（领取已收到的服装/周礼）。

原理（C 层逆向 + 实测，2026-08-30）:
  * 收到礼物后，服务端把「未开礼物」逐个排队；
    在 GetAllItems 响应里，未开礼物 = Item 且 "Context":3（已开/正常 = 1）。
  * 打开一个 = POST clientitems/DST/SetItemOpened {"ItemID":..,"ClientToken":..}
    （Lua 侧对应 TheInventory:SetItemOpened(item_id)，
     C 层 ItemServerLuaProxy @ 0x1402587F0，请求体 ClientToken+ItemID）。
    服务端返回 {"Error":false,"ItemID":..,"Modified":<新时间戳>}。
  * 打开后服务端「揭示」下一个未开礼物（Context 3 集合逐个冒头）——
    所以要循环：查到 Context==3 就 SetItemOpened，直到队列清空。
  * 队列清空后，Tick 不再返回 E_UNOPENED_ITEMS（门控解除），心跳可继续攒新礼。
  * ⚠ 神秘箱(MYSTERYBOX_*)不属于这套门控（Context=1 不挡 Tick）：
    想开它们要用 OpenMysteryBox（见 --open-boxes），默认不开。

实测:
  - FEET_WAXWELL_WIZARD / BEEFALO_FEET_SHADOW 两个未开礼物，逐个 SetItemOpened 后
    Context 3->1，Tick 从 E_UNOPENED_ITEMS 变为 {"Error":false,"GiftReceived":false}。
  - SetItemOpened 对神秘箱无效（服务端接受但不消耗），与本脚本默认行为一致。

用法:
  python klei_open_gifts.py --dry-run    # 只列 Context=3 未开礼物，不执行
  python klei_open_gifts.py              # 循环打开全部未开礼物
  python klei_open_gifts.py --verify     # 开完发一次 Tick 验证门控
  python klei_open_gifts.py --open-boxes # 额外把 MYSTERYBOX_* 神秘箱用 OpenMysteryBox 开掉
"""

import argparse
import datetime
import json
import os
import sys
import time

import klei_heartbeat as HB   # 复用登录/token/HTTP/日志

ITEMS_BASE = "https://items.kleientertainment.com"
PATH_ALL = "/clientitems/DST/GetAllItems"
PATH_OPENED = "/clientitems/DST/SetItemOpened"
PATH_OPENBOX = "/clientitems/DST/OpenMysteryBox"

MAX_CHAIN = 200   # 一次运行最多连续打开的个数（防死循环）

SECRETS_DIR = HB.SECRETS_DIR   # 凭证目录(单一来源,见 klei_heartbeat.py;已被 .gitignore 排除)


def get_items(token):
    st, raw = HB.http_post_json(ITEMS_BASE, PATH_ALL, {"ClientToken": token})
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        response = None
    if not isinstance(response, dict) or response.get("Error"):
        return None, None, raw
    items = response.get("Items", [])
    if not isinstance(items, list):
        return None, None, raw
    return st, items, raw


def main():
    ap = argparse.ArgumentParser(description="不开游戏开克雷已收礼物（SetItemOpened）")
    ap.add_argument("--dry-run", action="store_true", help="只列未开礼物不执行")
    ap.add_argument("--max", type=int, default=None, help="最多开 N 个（默认全清）")
    ap.add_argument("--verify", action="store_true", help="开完发一次 Tick 验证门控")
    ap.add_argument("--open-boxes", action="store_true",
                    help="顺便用 OpenMysteryBox 开掉 MYSTERYBOX_* 神秘箱（默认不开）")
    ap.add_argument("--token", metavar="FILE", default=os.path.join(SECRETS_DIR, ".gift_session.json"),
                    help="登录会话文件（默认 credentials/.gift_session.json）")
    ap.add_argument("--save", metavar="FILE", default=None, help="登录响应保存文件")
    ap.add_argument("--dll", default=None, help="steam_api64.dll 路径")
    ap.add_argument("--desc", default="standalone_open_gifts", help="FriendlyHostDescriptor")
    ap.add_argument("--log", default=os.path.join(SECRETS_DIR, "klei_opened.log"), help="开礼物日志")
    args = ap.parse_args()
    logfile = args.log or None

    # ---- 1. token ----
    token = None
    if os.path.exists(args.token):
        try:
            with open(args.token, encoding="utf-8") as f:
                token = json.load(f).get("Token")
        except Exception:
            token = None
    if not token:
        token, uid, j, _ = HB.full_login(args.dll, args.desc)
        HB.log("重新登录完成 UserID=%s" % uid, logfile)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(j, f)
    else:
        HB.log("复用会话 %s" % args.token, logfile)

    # ---- 2. dry-run / 列出未开 ----
    _, items, _raw = get_items(token)
    if items is None:
        HB.log("GetAllItems 失败: %s" % _raw[:200], logfile)
        return 1
    un = [item for item in items
          if isinstance(item, dict) and item.get("Context") == 3]
    HB.log("库存 %d 件，未开礼物(Context=3) %d 个：" % (len(items), len(un)), logfile)
    for item in un:
        d = datetime.datetime.fromtimestamp(item.get("Modified", 0)).strftime("%Y-%m-%d %H:%M")
        HB.log("    [%s] %s  (待开, %s)" %
               (item.get("ItemID", "?"), item.get("ItemType", "?"), d), logfile)
    if args.max:
        un = un[:args.max]
        HB.log("    (限开前 %d 个)" % args.max, logfile)
    if args.dry_run:
        HB.log("--dry-run：未执行", logfile)
        return 0
    if not un:
        HB.log("没有未开礼物。", logfile)
        if args.verify:
            st3, j3, _ = HB.tick(token)
            HB.log("Tick: %s" % (j3 or {}), logfile)
        return 0

    # ---- 3. 循环开礼物（逐个揭示）----
    opened = []
    for n in range(MAX_CHAIN):
        _st, current_items, _raw = get_items(token)
        cur = [item for item in (current_items or [])
               if isinstance(item, dict) and item.get("Context") == 3]
        if not cur:
            break
        item = cur[0]
        try:
            item_id = int(item["ItemID"])
            item_type = item["ItemType"]
        except (KeyError, TypeError, ValueError):
            HB.log("未开礼物字段异常，停止处理: %s" % str(item)[:120], logfile)
            break
        st2, raw2 = HB.http_post_json(
            ITEMS_BASE, PATH_OPENED,
            {"ItemID": item_id, "ClientToken": token})
        try:
            response = json.loads(raw2)
        except json.JSONDecodeError:
            response = None
        if not isinstance(response, dict) or response.get("Error"):
            HB.log("SetItemOpened 失败(HTTP=%s) %s" % (st2, raw2[:160]), logfile)
            break
        opened.append(item_type)
        HB.log("★ [%d] 打开 %s (%d)" % (n + 1, item_type, item_id), logfile)
        time.sleep(0.35)
    HB.log("共打开 %d 个未开礼物" % len(opened), logfile)

    # ---- 4. 可选：开神秘箱 ----
    if args.open_boxes:
        _, items2, raw_items2 = get_items(token)
        if items2 is None:
            HB.log("GetAllItems（开箱）失败: %s" % raw_items2[:160], logfile)
        else:
            boxes = [item for item in items2
                     if isinstance(item, dict) and
                     isinstance(item.get("ItemType"), str) and
                     item["ItemType"].startswith("MYSTERYBOX")]
            for box in boxes:
                try:
                    item_id = int(box["ItemID"])
                except (KeyError, TypeError, ValueError):
                    HB.log("神秘箱字段异常，跳过: %s" % str(box)[:120], logfile)
                    continue
                st3, raw3 = HB.http_post_json(
                    ITEMS_BASE, PATH_OPENBOX,
                    {"ItemID": item_id, "ClientToken": token})
                try:
                    response = json.loads(raw3)
                except json.JSONDecodeError:
                    response = None
                if isinstance(response, dict) and not response.get("Error"):
                    got = [item.get("ItemType", "?") for item in response.get("Items", [])
                           if isinstance(item, dict)]
                    HB.log("★ 开箱 %s -> %s" %
                           (box["ItemType"], ", ".join(got)), logfile)
                    time.sleep(0.35)
                else:
                    HB.log("开箱 %s 失败(HTTP=%s) %s" %
                           (box["ItemType"], st3, raw3[:120]), logfile)

    # ---- 5. 验证 ----
    if args.verify:
        st4, j4, r4 = HB.tick(token)
        ec = j4.get("ErrorCode") if isinstance(j4, dict) else None
        if ec:
            HB.log("验证 Tick: 仍返回 %s（可能还有未开或新礼）" % ec, logfile)
        else:
            HB.log("验证 Tick: 门控清除，心跳正常 → %s" % r4[:120], logfile)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] 退出")