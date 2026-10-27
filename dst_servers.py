#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dst_servers.py — 饥荒联机版(DST)在线服务器查询工具

逆向结论(详见 docs/协议-服务器查询.md):
  * 游戏内服务器列表来自 Klei lobby-v2 服务(专用服注册,客户端带票据查询);
  * 本工具走其公开数据镜像 dstserverlist.top 的 API(字段与游戏浏览器一致):
      POST api/v2/server/list    列表(支持筛选/排序/分页)
      POST api/v2/server/details?id=<RowId>   单服详情(玩家/天数/MOD)

用法:
  python dst_servers.py                          # 默认:按在线人数列出前 20 台
  python dst_servers.py --search 永恒             # 按服务器名搜索
  python dst_servers.py --size 10 --sort -Connected
  python dst_servers.py --info <RowId>           # 单服详情(玩家/MOD/天数)
  python dst_servers.py --total                  # 只看总在线数
"""

import argparse
import json
import sys
import time
import urllib.request

API = "https://api.dstserverlist.top/api/v2/server"
DEFAULT_UA = "Mozilla/5.0"


def _raw_post(url, body=b"", attempts=3):
    """该接口偶发瞬时 SSL 中断,重试即可。"""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url, data=body or None,
                headers={"User-Agent": DEFAULT_UA,
                         "Content-Type": "application/json"},
                method="POST")
            return urllib.request.urlopen(req, timeout=40).read()
        except Exception as e:
            last = e
            time.sleep(1 + i)
    raise last


def _post(path, obj):
    return json.loads(_raw_post(API + path, json.dumps(obj).encode()))


def _post_qs(path, qs):
    from urllib.parse import urlencode
    return json.loads(_raw_post(API + path + "?" + urlencode(qs)))


def cut(v, w):
    """按显示宽度截断(中文记 2 格)。"""
    v = str(v)
    pad, out = 0, ""
    for ch in v:
        add = 2 if ord(ch) > 127 else 1
        if pad + add > w:
            break
        out += ch
        pad += add
    return out


def fmt_table(rows):
    widths = [36, 7, 11, 14, 10]
    head = ["服务器名", "人数", "模式", "大区", "其他"]
    out = "  ".join(cut(h, w).ljust(w) for h, w in zip(head, widths))
    out += "\n" + "-" * sum(widths) + "  " * (len(widths) - 1)
    for r in rows:
        cells = [cut(r["name"], widths[0]),
                 cut("%d/%d" % (r["conn"], r["max"]), widths[1]),
                 cut(r["mode"], widths[2]), cut(r["region"], widths[3]),
                 cut(r["extra"], widths[4])]
        out += "\n" + "  ".join(c.ljust(w) for c, w in zip(cells, widths))
    return out


def cmd_list(args):
    body = {"PageCount": args.size, "PageIndex": args.page - 1,
            "Sort": [args.sort] if args.sort else ["-Connected"]}
    if args.search:
        body["ServerName"] = {"Value": args.search, "IsExclude": False, "IsRegex": False}
    if args.mode:
        body["Mode"] = args.mode
    if args.region:
        body["LobbyRegion"] = args.region
    d = _post("/list", body)
    print("在线服务器:%d 台(第 %d/%d 页,数据时间 %s)" %
          (d["TotalCount"], d["PageIndex"] + 1, d["MaxPageIndex"] + 1,
           str(d["LastUpdate"])[:16]))
    rows = []
    for it in d["List"]:
        extra = "PVP" if it.get("IsPvp") else ""
        if it.get("IsPassword"):
            extra += ("," if extra else "") + "密码"
        if it.get("IsMods"):
            extra += ("," if extra else "") + "MOD"
        if it.get("IsClanOnly"):
            extra += ("," if extra else "") + "仅部落"
        rows.append({"name": it.get("Name", "?"),
                     "conn": it.get("Connected", 0), "max": it.get("MaxConnections", 0),
                     "mode": it.get("Mode", "?"), "region": it.get("LobbyRegion", "?"),
                     "extra": extra})
    print(fmt_table(rows))
    if args.show_rowid:
        print("\nRowId 列表(供 --info 使用):")
        for it in d["List"]:
            print(" ", it["RowId"], cut(it.get("Name", ""), 30))


def cmd_info(args):
    d = _post_qs("/details", {"id": args.info, "forceUpdate": "false"})["Server"]
    players = d.get("Players", [])
    print("玩家(%d):" % len(players))
    for p in players:
        print("  -", p.get("Name", "?"), "| SteamID:", p.get("NetId", "?"))
    di = d.get("DaysInfo") or {}
    if di:
        print("游戏天数:第 %d 天(本季第 %d/%d 天)" %
              (di.get("Day", 0), di.get("DaysElapsedInSeason", 0),
               di.get("TotalDaysSeason", 0)))
    mods = d.get("ModsInfo") or []
    print("MOD(%d):" % len(mods))
    for m in mods[:15]:
        print("  -", m.get("Name"), m.get("CurrentVersion"))
    if len(mods) > 15:
        print("  …共 %d 个" % len(mods))


def cmd_total():
    print(json.dumps(_post("/GetTotal", {}), ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(
        description="DST 在线服务器查询(接口逆向:见 docs/协议-服务器查询.md)")
    ap.add_argument("--search", help="按服务器名搜索")
    ap.add_argument("--mode", help="按模式过滤: survival/endless/wilderness")
    ap.add_argument("--region", help="按大区过滤: ap-east-1 等")
    ap.add_argument("--sort", default="-Connected",
                    help="排序,如 -Connected/-Days/+ServerName")
    ap.add_argument("--size", type=int, default=20, help="每页条数(默认 20)")
    ap.add_argument("--page", type=int, default=1, help="页码(默认 1)")
    ap.add_argument("--show-rowid", action="store_true", help="附加显示 RowId")
    ap.add_argument("--info", metavar="RowId", help="查看单服详情(玩家/MOD/天数)")
    ap.add_argument("--total", action="store_true", help="只看总在线数")
    args = ap.parse_args()
    try:
        if args.total:
            cmd_total()
        elif args.info:
            cmd_info(args)
        else:
            cmd_list(args)
    except Exception as e:
        print("查询失败:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
