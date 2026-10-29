#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_skins_db_refresh.py — 从 dst-skins-checklist.github.io 上游刷新本地皮肤库。

上游 = 单文件自包含 index.html(数据+图标 base64 内嵌,~27MB)。
产物:
  1. web/assets/items/index.json  重建(保留 v5.2 schema:
     code → {en, collection, rarity, character, collection_zh, rarity_zh})
  2. web/assets/items/<CODE>.png  仅补缺失的图标(base64 解码)
  3. 控制台报告:新增物品数/新增收藏组(待人工译中文)/礼物池散件的缺失分词

用法: python _skins_db_refresh.py [--download]
默认复用 %TEMP%/checklist_index.html 缓存,--download 强制重下上游。
"""
import base64
import collections
import io
import json
import os
import re
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ITEMS_DIR = os.path.join(HERE, "web", "assets", "items")
INDEX = os.path.join(ITEMS_DIR, "index.json")
UPSTREAM = ("https://raw.githubusercontent.com/dst-skins-checklist/"
            "dst-skins-checklist.github.io/master/index.html")
CACHE = os.path.join(tempfile.gettempdir(), "checklist_index.html")


def fetch_bundle():
    if os.path.exists(CACHE) and "--download" not in sys.argv:
        return io.open(CACHE, encoding="utf-8", errors="replace").read()
    req = urllib.request.Request(UPSTREAM, headers={"User-Agent": "chester-refresh"})
    data = urllib.request.urlopen(req, timeout=300).read()
    open(CACHE, "wb").write(data)
    return data.decode("utf-8", errors="replace")


def parse(html):
    """每行 <tr>:rarity | 名称(td2 title)+图标 b64 | drop(td3 title)/name | 颜色 | 收藏组 | 市场链接或注释原码。"""
    out = {}
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        m = (re.search(r"market/listings/322330/([A-Z0-9_]+)", row)
             or re.search(r"<!--\s*([A-Z0-9_]+)\s*-->", row))
        if not m:
            continue
        tds = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)
        titles = re.findall(r'<td\b[^>]*title="([^"]*)"', row)
        if len(tds) < 6 or len(titles) < 3:
            continue
        icon = re.search(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', tds[1])
        out[m.group(1)] = {
            "rarity": re.sub(r"<[^>]+>", "", tds[0]).strip(),
            "base": titles[1],                    # td2 title: 基础名(或变体名)
            "name": re.sub(r"<[^>]+>", "", tds[2]).strip(),   # Name 列 = 旧库 en 口径
            "drop": titles[2],                    # td3 title: 获取途径
            "charcolor": re.sub(r"<[^>]+>", "", tds[3]).strip(),
            "collection": re.sub(r"<[^>]+>", "", tds[4]).strip(),
            "icon_b64": icon.group(1) if icon else None,
        }
    return out


def main():
    html = fetch_bundle()
    recs = parse(html)
    old = json.load(io.open(INDEX, encoding="utf-8"))
    print("上游 %d 件 | 本地 %d 件 | 新增 %d 件"
          % (len(recs), len(old), len(set(recs) - set(old))))

    # 复用旧库的中文映射(收藏组/稀有度),新值留待人工补
    coll_zh = {"Line Cook": "副厨", "Promotional": "宣传"}   # v5.7.2 起新增项种子
    rar_zh = {"Heirloom Classy": "传家精致",
              "Heirloom Distinguished": "传家卓越"}
    for v in old.values():
        if v.get("collection") and v.get("collection_zh", "-") not in ("-", ""):
            coll_zh[v["collection"]] = v["collection_zh"]
        if v.get("rarity") and v.get("rarity_zh"):
            rar_zh[v["rarity"]] = v["rarity_zh"]

    newdb = {}
    for code, r in sorted(recs.items()):
        newdb[code] = {
            "en": r["name"],
            "collection": r["collection"],
            "rarity": r["rarity"],
            "character": r["charcolor"],
            "collection_zh": coll_zh.get(r["collection"], "-"),
            "rarity_zh": rar_zh.get(r["rarity"], r["rarity"]),
        }

    # 图标:只补缺失
    added_icons = 0
    for code, r in recs.items():
        p = os.path.join(ITEMS_DIR, code + ".png")
        if r["icon_b64"] and not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(base64.b64decode(r["icon_b64"]))
            added_icons += 1

    # 报告:新增收藏组 / 稀有度
    new_colls = sorted({r["collection"] for r in recs.values()
                        if r["collection"] != "-" and r["collection"] not in coll_zh})
    new_rars = sorted({r["rarity"] for r in recs.values() if r["rarity"] not in rar_zh})
    print("新增收藏组(需人工中文):", new_colls)
    print("新增稀有度(需人工中文):", new_rars)

    # 礼物池散件(收藏组=-且 Gift Box 掉落)的基名/颜色清单 → label_item 分词缺什么
    gift0 = {c: r for c, r in recs.items()
             if "Gift Box" in r["drop"] and r["collection"] == "-"}
    sys.path.insert(0, HERE)
    import klei_panel as P
    tokens = collections.Counter()
    for c in gift0:
        for t in c.split("_"):
            if t not in P.ITEM_ZH and t not in P.SKIN_ZH and t not in P.SLOT_TOKENS:
                tokens[t] += 1
    print("礼物池散件 %d 件,缺失分词 %d 个:" % (len(gift0), len(tokens)))
    for t, n in sorted(tokens.items(), key=lambda x: -x[1]):
        en = next((r["base"] for r in gift0.values() if t in r["base"].upper().split()), "")
        print("  %-16s x%-3d %s" % (t, n, en))

    json.dump(newdb, io.open(INDEX, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("index.json 重建(%d 件) | 图标新增 %d 个" % (len(newdb), added_icons))


if __name__ == "__main__":
    main()
