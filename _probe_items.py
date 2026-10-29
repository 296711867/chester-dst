# -*- coding: utf-8 -*-
"""_probe_items.py — 只读探针:GetAllItems 全字段 dump + 礼物来源可分性分析"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import klei_heartbeat as HB

ACC = os.path.join(HB.SECRETS_DIR, "accounts", "KU_OdKe1JuO")
ITEMS_BASE = "https://items.kleientertainment.com"
PATH_ALL = "/clientitems/DST/GetAllItems"

sess = json.load(io.open(os.path.join(ACC, "session.json"), encoding="utf-8"))
token = sess.get("Token")
print("token len:", len(token or ""))

st, raw = HB.http_post_json(ITEMS_BASE, PATH_ALL, {"ClientToken": token})
print("HTTP", st, "| raw len", len(raw))
j = json.loads(raw)
if j.get("Error"):
    print("API error:", raw[:200]); sys.exit(1)

items = j.get("Items", [])
print("\n== 顶层字段 ==", sorted(j.keys()))
print("Items 数量:", len(items))

# 穷举所有 per-item 字段名(找文档漏掉的)
fields = {}
for it in items:
    for k, v in it.items():
        fields.setdefault(k, set()).add(type(v).__name__)
print("\n== per-item 字段 ==")
for k in sorted(fields):
    print("  %-14s %s" % (k, fields[k]))

# 样例:前 3 件完整 JSON
print("\n== 样例前3件 ==")
for it in items[:3]:
    print(json.dumps(it, ensure_ascii=False))

# ItemID 结构分析(是否含时间戳编码)
ids = [it.get("ItemID", 0) for it in items if it.get("ItemID")]
if ids:
    print("\n== ItemID 结构 ==")
    print("min:", min(ids), "max:", max(ids), "位数分布:",
          sorted(set(len(str(i)) for i in ids)))
    import time
    print("now ms:", int(time.time() * 1000), "| now 1e10:", int(time.time() * 1e7))
    # 高位/低位切分观察
    for i in sorted(ids)[:3] + sorted(ids)[-3:]:
        print("  id=%d hex=%x" % (i, i))

# Modified 分布(Context=1)
mods = sorted((it.get("Modified") or 0) for it in items if it.get("Context") == 1)
if mods:
    import time
    print("\n== Modified(已开物品最后修改时间)==")
    print("min:", time.strftime("%Y-%m-%d %H:%M", time.localtime(mods[0])), mods[0])
    print("max:", time.strftime("%Y-%m-%d %H:%M", time.localtime(mods[-1])), mods[-1])
    print("distinct:", len(set(mods)), "/", len(mods))

# 与本地礼物流水对账:面板领过的 item_type 集合
import sqlite3
db = sqlite3.connect(os.path.join(ACC, "account.db"))
ledger = [r[0] for r in db.execute(
    "SELECT item_type FROM gifts ORDER BY ts").fetchall()]
print("\n== 本地礼物流水 ==", len(ledger), "笔")
print("最早:", db.execute("SELECT MIN(ts) FROM gifts").fetchone()[0])
print("流水 item_type:", ledger)

inv_types = sorted(set(it.get("ItemType") for it in items if it.get("Context") == 1))
in_both = [t for t in inv_types if t in set(ledger)]
print("\n衣柜类型数:", len(inv_types), "| 与流水交集:", len(in_both))

# 完整保存供后续分析
io.open(r"D:\6Dpose\_items_dump.json", "w", encoding="utf-8").write(
    json.dumps(j, ensure_ascii=False, indent=1))
print("\ndump -> D:\\6Dpose\\_items_dump.json")
