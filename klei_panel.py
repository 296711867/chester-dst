#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
klei_panel.py — Chester·饥荒礼物箱 本地控制面板(v5):多账号勾选挂机 + 一整套设置 + 统计
+ 礼物监控与历史(跨账号聚合、中文标签)+ 每账号独立 SQLite + 周礼"领满即停"
+ 游戏图标/展示字体(/assets/ 本地静态)+ 中英文切换。

架构(全部标准库,零第三方依赖):
  * http.server(ThreadingHTTPServer)只绑 127.0.0.1,路由白名单,无 CORS;
  * 心跳 worker 是唯一会碰 ctypes/SteamAPI 的线程;HTTP 侧的登录
    (添加账号)经 LOGIN_GATE 与之串行(get_ticket 内 chdir/appid 是进程级操作);
  * 多账号:panel_accounts.json 注册表(启动时自动收编 credentials/accounts/ 旧目录),
    worker 每轮顺序处理所有勾选账号(账号间错峰 3s,interval=每轮间隔);
    账号不符(Steam 停在别的号)只暂停该账号——面板里关→开勾选即重试;
  * 每账号一个数据库:credentials/accounts/<KU_id>/account.db
      heartbeats(ts, ok, http, note) / gifts(ts, item_type, item_id, source)
      events(ts, kind, detail) / meta(key, value)
  * 周礼策略:滑动 7 天窗口内 tick 来源礼物 >= 8 → 跳过该账号本轮心跳,
    窗口滑出自动恢复;DailyGift(每日登录礼)独立于周礼,每账号每天一次。

用法:
  python klei_panel.py                # 默认 http://127.0.0.1:8377 并自动开浏览器
  python klei_panel.py --port 8400 --no-browser
  双击「启动控制面板.bat」效果同上。

安全边界:
  - 只监听 127.0.0.1;Host 头校验(防 DNS rebinding);只实现 GET/POST 白名单路径;
  - 任何响应不包含 token;日志/事件/统计中 UserID 脱敏;仅 /api/accounts
    (账号勾选/移除操作需要)返回完整 userid,本地面板专属;
  - /assets/ 静态资源:扩展名白名单 + 穿越拒绝 + realpath 包含性校验。
"""

import argparse
import json
import sys
import os
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import klei_steam_login as K
import klei_heartbeat as HB
import klei_open_gifts as OG

# 打包(PyInstaller)模式:可写数据放 exe 旁,只读资源(web/)在解包目录
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
    WEB_DIR = os.path.join(getattr(sys, "_MEIPASS", HERE), "web")
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    WEB_DIR = os.path.join(HERE, "web")
ACCOUNTS_DIR = os.path.join(HB.SECRETS_DIR, "accounts")
LEGACY_SESSION = os.path.join(HB.SECRETS_DIR, ".hb_auto_session.json")

PATH_DAILY = "/clientitems/DST/DailyGift"
PATH_RECEIPT = "/clientitems/DST/VerifyGiftingReceipt"
PATH_OPENED = "/clientitems/DST/SetItemOpened"

WEEKLY_CAP = 8                 # PC 周礼上限(社区情报,见 docs/情报-社区与SDK调研)
CAP_WINDOW = 7 * 24 * 3600     # 滑动窗口
MAX_OPEN_CHAIN = 200           # 与 klei_open_gifts.MAX_CHAIN 对齐
MAX_BOX_PER_ROUND = 50         # 单轮自动开神秘箱上限

DB_LOCK = threading.RLock()    # 所有 SQLite 访问共用一把锁(跨线程读写)

# ---------------------------------------------------------------- 设置
SETTINGS_PATH = os.path.join(HERE, "panel_settings.json")   # 用户本地配置,gitignore 排除
SETTINGS_DEFAULTS = {"interval": 360, "auto_box": False, "daily_gift": True,
                     "cap_stop": True, "desc": "klei_panel"}
SETTINGS_LOCK = threading.Lock()   # 序列化 tmp 写+replace,防并发保存互相写坏


def validate_settings(d):
    """任意输入 → 合法设置(越界收敛/类型不符回默认)。"""
    out = dict(SETTINGS_DEFAULTS)
    if not isinstance(d, dict):
        return out
    try:
        out["interval"] = max(30, min(3600, int(d.get("interval", 360))))
    except (TypeError, ValueError, OverflowError):   # 1e999→inf→OverflowError
        pass
    for k in ("auto_box", "daily_gift", "cap_stop"):
        if isinstance(d.get(k), bool):
            out[k] = d[k]
    if isinstance(d.get("desc"), str) and d["desc"].strip():
        out["desc"] = d["desc"].strip()[:100]
    return out


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return validate_settings(json.load(f))
    except (OSError, ValueError):          # 缺文件/损坏 JSON → 全默认
        return dict(SETTINGS_DEFAULTS)


def save_settings(d):
    d = validate_settings(d)
    with SETTINGS_LOCK:
        tmp = SETTINGS_PATH + ".tmp"       # 同目录 tmp + os.replace 原子替换
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    return d


def _today0():
    """本地时区当日 0 点(与每日礼判定共用)。"""
    return time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))

# ---------------------------------------------------------------- 账号注册表
ACCOUNTS_PATH = os.path.join(HERE, "panel_accounts.json")   # 面板配置,gitignore 排除
ACCOUNTS_LOCK = threading.RLock()   # 可重入:registry_update 持锁调用 save_accounts
LOGIN_GATE = threading.Lock()   # full_login 串行门:get_ticket 内 chdir+appid.txt 是进程级操作
RELOGIN_THROTTLE = 300          # needs_login 账号重登节流(秒)


def validate_accounts(raw):
    """任意输入 → 合法注册表(去重/强转/截断;坏条目丢弃)。"""
    out = []
    if isinstance(raw, dict) and isinstance(raw.get("accounts"), list):
        seen = set()
        for it in raw["accounts"]:
            if not isinstance(it, dict):
                continue
            uid = it.get("userid")
            if not isinstance(uid, str) or not uid.startswith("KU_") or uid in seen:
                continue
            seen.add(uid)
            alias = it.get("alias") if isinstance(it.get("alias"), str) else ""
            out.append({"userid": uid, "alias": alias.strip()[:30],
                        "enabled": bool(it.get("enabled"))})
    return {"version": 1, "accounts": out}


def load_accounts():
    try:
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            return validate_accounts(json.load(f))
    except (OSError, ValueError):
        return {"version": 1, "accounts": []}


def save_accounts(reg):
    reg = validate_accounts(reg)
    with ACCOUNTS_LOCK:
        tmp = ACCOUNTS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ACCOUNTS_PATH)
    return reg


def auto_alias(reg):
    """账号N = 注册表内已有最大 N + 1。"""
    n = 0
    for it in reg.get("accounts", []):
        try:
            n = max(n, int(str(it.get("alias", "")).replace("账号", "") or 0))
        except ValueError:
            pass
    return "账号%d" % (n + 1)


def migrate_accounts(panel):
    """首次升级收编:accounts/ 目录 → 注册表;最近活跃者 enabled,其余默认关。"""
    if os.path.exists(ACCOUNTS_PATH):
        return
    stores = list_account_stores()
    if not stores:
        return
    stores.sort(key=lambda ud: -(os.path.getmtime(ud[1]) if os.path.exists(ud[1]) else 0))
    accounts = []
    for i, (uid, _dbp) in enumerate(stores):
        accounts.append({"userid": uid, "alias": "账号%d" % (i + 1),
                         "enabled": i == 0})
    # LEGACY 单账号会话补收:UserID 与目录一致的复制为该目录 session.json
    legacy = AccountStore.load_session_file(LEGACY_SESSION)
    if legacy:
        _tok, luid, j = legacy
        for it in accounts:
            if it["userid"] == luid:
                p = os.path.join(ACCOUNTS_DIR, luid, "session.json")
                if not os.path.exists(p):
                    AccountStore(luid).save_session(j)
                    print("[panel] 已从旧版单账号会话收编 %s" % mask_userid(luid), flush=True)
    panel._registry = save_accounts({"version": 1, "accounts": accounts})
    print("[panel] 账号注册表已初始化:收编 %d 个账号(最近活跃者已启用)" % len(accounts),
          flush=True)

# ---------------------------------------------------------------- 物品中文标签
SLOT_TOKENS = {"HEAD": "头部", "BODY": "身体", "FEET": "脚部", "HANDS": "手部",
               "ITEM": "物件", "GHOST": "幽灵形态"}   # 槽位词可出现在任意段
ITEM_ZH = {
    "MYSTERYBOX": "神秘箱",
    # 角色
    "WILSON": "威尔逊", "WILLOW": "薇洛", "WOLFGANG": "沃尔夫冈", "WENDY": "温蒂",
    "WX78": "WX-78", "WICKERBOTTOM": "薇克巴顿", "WOODIE": "伍迪",
    "MAXWELL": "麦斯威尔", "WAXWELL": "麦斯威尔", "WIGFRID": "薇格弗德",
    "WEBBER": "韦伯", "WORMWOOD": "沃姆伍德", "WARLY": "沃利",
    "WORTOX": "沃拓克斯", "WURT": "薇尔娜", "WALTER": "沃尔特", "WANDA": "旺达",
    # 常见物件/生物
    "TOPHAT": "礼帽", "CHESTER": "切斯特", "EYEBONE": "眼骨", "GLOMMER": "格罗姆",
    "UMBRELLA": "雨伞", "BEEFALO": "皮弗娄牛", "BELL": "铃铛", "CATCOON": "猫狸",
    "GLASSCUTTER": "玻璃刀", "MARBLE": "大理石", "DECOR": "装饰",
    "VEST": "马甲", "DRESS": "长裙", "PICTUREFRAME": "画框",
}
SKIN_ZH = {
    "CHEF": "厨师", "FORMAL": "正装", "GUESTOFHONOR": "贵宾", "ROSE": "玫瑰",
    "SNOWFELL": "落雪", "SURVIVOR": "幸存者", "MERRYMAKER": "欢庆者",
    "TRIEDANDTRUE": "久经考验", "CLASSIC": "经典", "ICE": "寒冰",
    "WIZARD": "巫师", "BEACH": "海滩", "SHADOW": "暗影", "GORGE": "熔炉",
    "HALLOWEEN": "万圣", "WINTER": "冬宴", "LUCY": "露西",
    "VENTRILOQUIST": "腹语", "WESTERN": "西部", "MAGMATIC": "炽岩",
}
SLOT_OF = {"TOPHAT": "头部", "UMBRELLA": "手部"}   # 物件自带头部/手部语义的兜底


def label_item(item_type):
    """TOPHAT_CHEF → 厨师礼帽(头部);FEET_WAXWELL_WIZARD → 巫师麦斯威尔(脚部);
    BEEFALO_FEET_SHADOW → 暗影皮弗娄牛(脚部);任一段翻不出 → 原码。"""
    raw = str(item_type or "").strip()
    if not raw:
        return raw
    toks = raw.upper().split("_")
    slot, rest = None, []
    for t in toks:
        if slot is None and t in SLOT_TOKENS:
            slot = SLOT_TOKENS[t]                      # 槽位可在任意段(首/中)
        else:
            rest.append(t)
    if any(t not in ITEM_ZH and t not in SKIN_ZH for t in rest):
        return raw
    objs = [ITEM_ZH[t] for t in rest if t in ITEM_ZH]
    skins = [SKIN_ZH[t] for t in rest if t in SKIN_ZH]
    if not objs and not skins:
        return raw
    slot = slot or (SLOT_OF.get(rest[0]) if rest else None)
    return "".join(skins) + "".join(objs) + ("(%s)" % slot if slot else "")


SOURCE_ZH = {"tick": "周礼", "daily": "每日礼", "box": "神秘箱"}

# ---------------------------------------------------------------- 全量皮肤库
# 数据源:dst-skins-checklist.github.io(2953 件:原码→英文名/收藏组/稀有度+图标)
# 图标存 web/assets/items/<CODE>.png,名称索引存 web/assets/items/index.json
SKINS_PATH = os.path.join(WEB_DIR, "assets", "items", "index.json")
try:
    SKINS_DB = json.load(open(SKINS_PATH, encoding="utf-8"))
except (OSError, ValueError):
    SKINS_DB = {}
COLL_ZH = json.loads(json.dumps({"": "", "Abyssal": "深渊", "Adventurer's": "冒险家", "Aged": "苍古", "An Eye for an Eye": "以眼还眼", "Ancient": "远古", "Art Nouveau": "新艺术", "Barber's": "理发师", "Canadiana": "加拿大风情", "Cawnival": "乌鸦嘉年华", "Chitinous": "几丁质", "Costume": "戏服", "Cottage": "田园", "Crystalline": "晶体", "Dark Wizard": "黑魔法师", "Decennial": "十周年", "Decorator's": "装饰师", "Detective": "侦探", "Experimental": "实验", "Fantasmical": "幻梦", "Farmhand": "农夫", "Flock's": "禽群", "Fool's": "愚人", "Forge": "熔炉", "Forlorn Doll": "孤寂人偶", "Formal": "典雅", "Gothic": "哥特", "Hallowed Nights": "万圣夜", "Hand-me-down": "传家旧物", "Head Chef": "主厨", "Heart": "心形", "Hollow": "空心", "Jeweled": "珠宝", "Joker's": "小丑", "Lunar New Year": "农历新年", "Lush": "繁茂", "Masquerade": "化装舞会", "Merrymaker": "寻欢客", "Minotaur": "米诺陶", "Moonbound": "缚月", "Musclebound": "肌肉", "Mystic": "神秘", "Mythic": "神话", "Nautical": "航海", "Next of Kin": "至亲", "Nom de Plume": "笔名", "Oasis Bound": "绿洲之旅", "Orbital": "环轨", "Pantomime": "哑剧", "Parlor's": "会客厅", "Reliquary": "圣物", "Rose": "玫瑰", "Royal": "王室", "Seaside": "海滨", "Shadow": "暗影", "Shelled": "甲壳", "Shorts": "短裤", "Side Show": "助兴表演", "Snowfallen": "落雪", "Strongman": "大力士", "Survivor": "幸存者", "Swamp": "沼泽", "Swashbuckler": "侠盗", "Theater": "剧院", "Tinkersmith": "巧匠", "Trawler": "拖网渔船", "Uncorrupted": "未染", "Victorian": "维多利亚", "Wagstaff": "瓦格斯塔夫", "Winged Victory": "翼之胜利", "Winter": "冬宴", "Year of the Carrat": "卡拉特鼠之年", "Year of the Clockwork Knight": "发条骑士之年", "Year of the Pig King": "猪王之年", "Year of the Varg": "座狼之年"}))
RARITY_ZH = json.loads(json.dumps({"Common": "普通", "Classy": "精致", "Spiffy": "华丽", "Distinguished": "卓越", "Elegant": "典雅", "Loyal": "忠诚", "Timeless": "永恒", "Heirloom Elegant": "传家典雅", "Event": "活动", "Reward": "奖励", "Character": "角色", "Complimentary": "赠礼", "Resurrected": "复刻", "Proof Of Purchase": "凭证"}))


class AccountMismatch(RuntimeError):
    """重登后 UserID 与当前账号不符(Steam 当前登录的可能是别的账号)。不可自愈。"""


class NeedsLogin(RuntimeError):
    """账号缺会话且处于重登节流中,等下一轮再试。"""


def mask_userid(uid):
    uid = str(uid or "?")
    return uid[:6] + "***" if len(uid) > 6 else uid


class AccountStore:
    """一个账号一个 SQLite 库 + session 文件,目录 credentials/accounts/<userid>/。"""

    def __init__(self, userid):
        self.userid = userid
        self.dir = os.path.join(ACCOUNTS_DIR, userid)
        os.makedirs(self.dir, exist_ok=True)
        self.db_path = os.path.join(self.dir, "account.db")
        self.session_path = os.path.join(self.dir, "session.json")
        with DB_LOCK:
            self.db = sqlite3.connect(self.db_path, check_same_thread=False)
            self.db.execute("""CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY, value TEXT)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS heartbeats(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ok INTEGER,
                http INTEGER, note TEXT)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS gifts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
                item_type TEXT, item_id INTEGER, source TEXT)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
                kind TEXT, detail TEXT)""")
            self.db.commit()

    # ---- 基础记录 ----
    def add_heartbeat(self, ok, http, note):
        with DB_LOCK:
            self.db.execute("INSERT INTO heartbeats(ts,ok,http,note) VALUES(?,?,?,?)",
                            (time.time(), 1 if ok else 0, int(http or 0), str(note)[:200]))
            self.db.commit()

    def add_gift(self, item_type, item_id, source):
        with DB_LOCK:
            self.db.execute("INSERT INTO gifts(ts,item_type,item_id,source) VALUES(?,?,?,?)",
                            (time.time(), str(item_type), int(item_id or 0), source))
            self.db.commit()

    def add_event(self, kind, detail=""):
        with DB_LOCK:
            self.db.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                            (time.time(), str(kind), str(detail)[:300]))
            self.db.commit()

    def set_meta(self, key, value):
        with DB_LOCK:
            self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                            (key, str(value)))
            self.db.commit()

    def get_meta(self, key, default=None):
        with DB_LOCK:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    # ---- 查询 ----
    def gifts_since(self, since, source=None):
        q = "SELECT COUNT(*) FROM gifts WHERE ts>?"
        a = [since]
        if source:
            q += " AND source=?"
            a.append(source)
        with DB_LOCK:
            return self.db.execute(q, a).fetchone()[0]

    def recent(self, table, limit=50):
        with DB_LOCK:
            rows = self.db.execute(
                "SELECT * FROM %s ORDER BY id DESC LIMIT ?" % table, (int(limit),)).fetchall()
        return rows

    def recent_dicts(self, table, limit=50):
        """按列名输出对象数组(三张表统一形状)。"""
        with DB_LOCK:
            cur = self.db.execute(
                "SELECT * FROM %s ORDER BY id DESC LIMIT ?" % table, (int(limit),))
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows

    def oldest_window_gift_ts(self, since, source="tick"):
        """窗口内最早一礼的时间戳(算领满恢复 ETA 用)。"""
        with DB_LOCK:
            row = self.db.execute(
                "SELECT MIN(ts) FROM gifts WHERE ts>? AND source=?",
                (since, source)).fetchone()
        return row[0] if row and row[0] else None

    # ---- 会话文件 ----
    def save_session(self, resp_json):
        with open(self.session_path, "w", encoding="utf-8") as f:
            json.dump(resp_json, f)

    @staticmethod
    def load_session_file(path):
        try:
            with open(path, encoding="utf-8") as f:
                j = json.load(f)
            tok = j.get("Token")
            uid = j.get("UserID")
            return (tok, uid, j) if tok else None
        except Exception:
            return None


def list_account_stores():
    """枚举已有账号库,供 /api/accounts 与会话恢复用。"""
    out = []
    if not os.path.isdir(ACCOUNTS_DIR):
        return out
    for name in sorted(os.listdir(ACCOUNTS_DIR)):
        dbp = os.path.join(ACCOUNTS_DIR, name, "account.db")
        if os.path.isfile(dbp):
            out.append((name, dbp))
    return out


def _add_account_fn(accounts, uid, alias, resp):
    """registry_update 回调:重复添加也要落盘新 token(本次登录已废旧 token)。"""
    store = AccountStore(uid)
    store.save_session(resp)
    for it in accounts:
        if it["userid"] == uid:
            return "dup"
    accounts.append({"userid": uid,
                     "alias": (alias or "").strip()[:30]
                     or auto_alias({"accounts": accounts}),
                     "enabled": True})
    return None


def account_rows(panel):
    """GET /api/accounts:注册表+目录+运行态合成。userid 原文返回(仅 127.0.0.1 面板
    使用,供 toggle/remove 构造请求);日志/事件仍脱敏。"""
    reg = {it["userid"]: it for it in panel.registry_snapshot()}
    w = panel.worker
    runtimes = w.runtimes if w else {}
    rows = []
    seen = set()
    for uid, it in reg.items():
        seen.add(uid)
        rt = runtimes.get(uid)
        store = rt.store if rt and rt.store else None
        created = False
        if store is None and os.path.isfile(os.path.join(ACCOUNTS_DIR, uid, "account.db")):
            store = AccountStore(uid)
            created = True
        weekly = today = 0
        ticks = [0, 0]
        gifts_total = 0
        last_active = None
        has_session = os.path.isfile(os.path.join(ACCOUNTS_DIR, uid, "session.json"))
        if store:
            weekly = store.gifts_since(time.time() - CAP_WINDOW, source="tick")
            today = store.gifts_since(_today0())
            with DB_LOCK:
                db = store.db
                t = db.execute("SELECT COUNT(*),SUM(ok) FROM heartbeats").fetchone()
                gifts_total = db.execute("SELECT COUNT(*) FROM gifts").fetchone()[0]
                last_active = db.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
            ticks = [t[0] or 0, int(t[1] or 0)]
            if created:
                with DB_LOCK:
                    store.db.close()
        if rt and rt.paused_reason:
            status, reason = "paused", rt.paused_reason
        elif it["enabled"] and not has_session:
            status, reason = "needs_login", "无会话,请切 Steam 到该账号后重新添加"
        elif it["enabled"] and panel.get_settings().get("cap_stop") and weekly >= WEEKLY_CAP:
            status, reason = "capped", "本周已领满"
        elif it["enabled"]:
            status, reason = "ok", None
        else:
            status, reason = "disabled", None
        rows.append({"userid": uid, "alias": it.get("alias") or auto_alias({"accounts": [it]}),
                     "enabled": it["enabled"], "has_session": has_session,
                     "ticks_total": ticks[0], "ticks_ok": ticks[1],
                     "gifts_total": gifts_total, "weekly": weekly, "today": today,
                     "status": status, "paused_reason": reason, "last_active_ts": last_active})
    return rows


def gift_enrich(rows):
    """礼物行加中文标签与皮肤库信息(服务端集中算,前端免字典)。"""
    out = []
    for r in rows:
        r = dict(r)
        code = r.get("item_type")
        meta = SKINS_DB.get(code)
        r["item_label"] = label_item(code)
        r["source_label"] = SOURCE_ZH.get(r.get("source"), r.get("source") or "?")
        r["icon"] = "/assets/items/%s.png" % code if meta else None
        if meta:
            r["en_name"] = meta.get("en")
            r["rarity"] = meta.get("rarity")
            r["rarity_zh"] = meta.get("rarity_zh")
            r["collection_zh"] = meta.get("collection_zh")
        else:
            r["en_name"] = None
        # 原码翻译不出时,回退收藏组中文名(如"主厨系列"),再退原码
        if r["item_label"] == code and meta:
            czh = meta.get("collection_zh")
            if czh:
                r["item_label"] = czh + "系列"
        out.append(r)
    return out


class AccountRuntime:
    """单个账号的运行态(worker 线程私有)。"""

    def __init__(self, userid, alias="", enabled=True):
        self.userid = userid
        self.alias = alias
        self.enabled = enabled
        self.store = None            # AccountStore 懒建
        self.token = None
        self.capped = False
        self.paused_reason = None    # "mismatch" → 终态,关闭再开启重试
        self.last_error = None
        self.last_relogin_ts = 0.0


class Worker(threading.Thread):
    """唯一的心跳/登录工作线程:每轮顺序处理所有启用的账号。
    ctypes(SteamAPI)只在本线程(或持 LOGIN_GATE 的 HTTP 线程)内发生。"""

    def __init__(self, panel, interval):
        super().__init__(daemon=True, name="klei-worker")
        self.panel = panel
        self.interval = max(30, int(interval))   # 每轮间隔:一轮处理完所有启用账号再睡
        self.stop_event = threading.Event()
        self.runtimes = {}           # userid -> AccountRuntime
        self.current = None

    # ---- 工具 ----
    def _ev(self, rt, kind, detail=""):
        if rt is not None and rt.store is not None:
            rt.store.add_event(kind, detail)
        tag = ("[%s]" % rt.alias) if rt is not None and rt.alias else ""
        print("[panel:%s]%s %s %s" % (time.strftime("%H:%M:%S"), tag, kind, detail), flush=True)

    def _sleep(self, seconds):
        return self.stop_event.wait(seconds)

    # ---- 会话(L0 续期 / L1 重登门禁 / L2 暂停) ----
    def _adopt(self, rt, resp, src):
        if rt.store is None:
            rt.store = AccountStore(rt.userid)
        rt.store.save_session(resp)
        rt.token = resp.get("Token") or ""
        rt.paused_reason = None
        rt.needslogin_logged = False
        self._ev(rt, "login", "src=%s" % src)

    def _ensure_session(self, rt):
        """有 session → TokenPurpose 续期;无/失效 → LOGIN_GATE 内重登并做 UserID 门禁。"""
        loaded = None
        if rt.store is None:
            sp = os.path.join(ACCOUNTS_DIR, rt.userid, "session.json")
            loaded = AccountStore.load_session_file(sp)
            if loaded:
                rt.store = AccountStore(rt.userid)
        if loaded is None and rt.store is not None:
            loaded = AccountStore.load_session_file(rt.store.session_path)
        if loaded:
            tok, uid, _j = loaded
            st, raw = K.refresh_token(tok)
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                resp = None
            if resp and not resp.get("Error") and resp.get("Token"):
                self._adopt(rt, resp, "refresh")
                return
        # L1:重登一次(Steam 当前登录的可能不是这个账号 → 门禁)
        if time.time() - rt.last_relogin_ts < RELOGIN_THROTTLE:
            raise NeedsLogin("重登节流中")
        rt.last_relogin_ts = time.time()
        with LOGIN_GATE:
            tok, uid, resp, _sid = HB.full_login(
                None, self.panel.get_settings()["desc"])
        if uid != rt.userid:
            raise AccountMismatch(
                "期望 %s 实得 %s(Steam 当前登录的是别的账号)"
                % (mask_userid(rt.userid), mask_userid(uid)))
        self._adopt(rt, resp, "steam")

    def _refresh_or_relogin(self, rt):
        """token 失效:续期→重登;UserID 门禁防跨账号错配。
        refresh_or_relogin 内部续期失败会回落 full_login(进程级 SteamAPI 操作),须持门。"""
        with LOGIN_GATE:
            tok, uid, src, resp = HB.refresh_or_relogin(
                rt.token, None, self.panel.get_settings()["desc"])
        if uid not in ("?", None) and uid != rt.userid:
            raise AccountMismatch(
                "期望 %s 实得 %s" % (mask_userid(rt.userid), mask_userid(uid)))
        rt.token = tok
        rt.store.save_session(resp)
        self._ev(rt, src, "userid=%s" % mask_userid(uid))

    # ---- 礼物(逻辑与单账号版一致,全部经 rt 取 token/store) ----
    def _open_pending(self, rt):
        opened = 0
        for _ in range(MAX_OPEN_CHAIN):
            _st, items, raw = OG.get_items(rt.token)
            if items is None:
                self._ev(rt, "error", "GetAllItems 失败: %s" % raw[:120])
                break
            pending = [i for i in items
                       if isinstance(i, dict) and i.get("Context") == 3]
            if not pending:
                break
            item = pending[0]
            try:
                item_id = int(item["ItemID"])
                item_type = item["ItemType"]
            except (KeyError, TypeError, ValueError):
                self._ev(rt, "error", "未开礼物字段异常: %s" % str(item)[:120])
                break
            st2, raw2 = HB.http_post_json(
                HB.ITEMS_BASE, PATH_OPENED, {"ItemID": item_id, "ClientToken": rt.token})
            try:
                r = json.loads(raw2)
            except json.JSONDecodeError:
                r = None
            if not isinstance(r, dict) or r.get("Error"):
                self._ev(rt, "error", "SetItemOpened 失败 HTTP=%s %s" % (st2, raw2[:120]))
                break
            rt.store.add_gift(item_type, item_id, "tick")
            opened += 1
            self.panel.bump_gift()
            if self.stop_event.wait(0.35):
                break
        if opened:
            self._ev(rt, "gifts_opened", "本轮共领取 %d 个" % opened)
        return opened

    def _claim(self, rt, key):
        if key is not None:
            body = {"Receipts": [{"UserID": rt.store.userid, "Key": key}]}
            st, raw = HB.http_post_json(HB.ITEMS_BASE, PATH_RECEIPT, body)
            self._ev(rt, "receipt", "HTTP=%s %s" % (st, raw[:100]))
        self._open_pending(rt)

    def _open_boxes(self, rt):
        """自动开神秘箱(Context=1,不挡 Tick;产出一次性消耗,默认关)。"""
        opened = 0
        for _ in range(MAX_BOX_PER_ROUND):
            _st, items, raw = OG.get_items(rt.token)
            if items is None:
                self._ev(rt, "error", "开箱:GetAllItems 失败 %s" % raw[:120])
                break
            boxes = [i for i in items if isinstance(i, dict)
                     and str(i.get("ItemType") or "").startswith("MYSTERYBOX")]
            if not boxes:
                break
            try:
                item_id = int(boxes[0]["ItemID"])
                box_name = boxes[0]["ItemType"]
            except (KeyError, TypeError, ValueError):
                self._ev(rt, "error", "开箱:字段异常 %s" % str(boxes[0])[:120])
                break
            st2, raw2 = HB.http_post_json(
                HB.ITEMS_BASE, OG.PATH_OPENBOX,
                {"ItemID": item_id, "ClientToken": rt.token})
            try:
                r = json.loads(raw2)
            except json.JSONDecodeError:
                r = None
            if not isinstance(r, dict) or r.get("Error"):
                self._ev(rt, "error", "开箱 %s 失败 HTTP=%s %s" % (box_name, st2, raw2[:120]))
                break
            for it in r.get("Items") or []:
                if isinstance(it, dict):
                    try:
                        gid = int(it.get("ItemID") or 0)
                    except (TypeError, ValueError):
                        gid = 0
                    rt.store.add_gift(str(it.get("ItemType") or "?"), gid, "box")
                    self.panel.bump_gift()
            opened += 1
            if self.stop_event.wait(0.35):
                break
        if opened:
            self._ev(rt, "boxes_opened", "本轮开神秘箱 %d 个" % opened)
        return opened

    def _daily_gift(self, rt):
        """每日登录礼:每账号每天一次,独立于周礼(Tick)体系。"""
        today0 = _today0()
        if float(rt.store.get_meta("last_daily_ts", 0) or 0) >= today0:
            return
        st, raw = HB.http_post_json(
            HB.ITEMS_BASE, PATH_DAILY, {"ClientToken": rt.token})
        got, item_id = None, 0
        j = None
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 只有拿到确定性答复(HTTP 200 + 无业务错误)才盖章"当日已领";
        # 网络失败/token 过期不盖章,下轮重试,避免长暂停期每天丢每日礼
        if st != 200 or not isinstance(j, dict) or j.get("Error"):
            self._ev(rt, "daily_gift", "调用未成功(HTTP=%s),不盖章下轮重试: %s" % (st, raw[:80]))
            return
        g = j.get("GiftReceived")
        # 实测响应:{"GiftReceived":true,"ItemType":"TOPHAT_CHEF","ItemID":...}
        # 兼容两种形态:顶层字段(实测)或嵌在 GiftReceived 对象里(旧笔记形态)
        src = j if g is True else (g if isinstance(g, dict) else None)
        if isinstance(src, dict):
            got = src.get("ItemType")
            try:
                item_id = int(src.get("ItemID") or 0)
            except (TypeError, ValueError):
                item_id = 0
        rt.store.set_meta("last_daily_ts", time.time())
        if got:
            rt.store.add_gift(got, item_id, "daily")
            self.panel.bump_gift()
            self._ev(rt, "daily_gift", "%s (%d)" % (got, item_id))
        else:
            self._ev(rt, "daily_gift", "今日无每日礼/已领(%s)" % raw[:80])

    # ---- 单账号一轮(原 run 主体;吃掉单账号异常,返回是否完全成功) ----
    def _process_account(self, rt, cfg):
        p = self.panel
        try:
            if rt.token is None or rt.store is None:
                self._ensure_session(rt)
            if cfg["daily_gift"]:
                self._daily_gift(rt)
            if cfg["auto_box"]:
                self._open_boxes(rt)
            # 周礼领满即停:跳过该账号本轮 tick(窗口每轮重算,无需独占睡眠)
            w = rt.store.gifts_since(time.time() - CAP_WINDOW, source="tick")
            if cfg["cap_stop"] and w >= WEEKLY_CAP:
                if not rt.capped:
                    rt.capped = True
                    self._ev(rt, "cap_pause", "本周礼物已领满 %d/%d,暂停该账号心跳" % (w, WEEKLY_CAP))
                return True
            if rt.capped:
                rt.capped = False
                self._ev(rt, "cap_resume", "滑动窗口恢复,继续心跳")

            st, j, raw = HB.tick(rt.token)
            if st == 0:
                p.inc(tick_fail=1)
                rt.store.add_heartbeat(False, 0, raw[:80])
                p.set(note="[%s] 网络错误,下轮重试" % rt.alias)
                return False
            ec = j.get("ErrorCode") if isinstance(j, dict) else None
            if ec == "E_INVALID_TOKEN":
                self._refresh_or_relogin(rt)
                return True
            abnormal = j is None or st >= 400
            if abnormal:
                p.inc(tick_fail=1)
                rt.store.add_heartbeat(False, st, raw[:80])
                p.set(note="[%s] 响应异常(HTTP=%s),下轮重试" % (rt.alias, st))
                return False
            key = HB.get_gift_key(j)
            if ec == "E_UNOPENED_ITEMS":
                p.inc(tick_ok=1)
                rt.store.add_heartbeat(True, st, "E_UNOPENED_ITEMS")
                self._open_pending(rt)
            elif key is not None:
                p.inc(tick_ok=1)
                rt.store.add_heartbeat(True, st, "gift:%s" % key)
                self._claim(rt, key)
                # 榨干堆叠礼物
                for _ in range(5):
                    if self.stop_event.is_set():
                        break
                    _s2, j2, _r2 = HB.tick(rt.token)
                    k2 = HB.get_gift_key(j2)
                    if k2 is None:
                        break
                    self._claim(rt, k2)
                    if self.stop_event.wait(1):
                        break
            elif isinstance(j, dict) and j.get("GiftReceived") is True:
                p.inc(tick_ok=1)
                rt.store.add_heartbeat(True, st, "gift:no-key")
                self._open_pending(rt)
            elif isinstance(j, dict) and j.get("Error"):
                p.inc(tick_fail=1)
                rt.store.add_heartbeat(False, st, raw[:80])
                p.set(note="[%s] %s" % (rt.alias, raw[:80]))
                return False
            else:
                p.inc(tick_ok=1)
                rt.store.add_heartbeat(True, st, "ok")
            p.set(note="", last_tick_at=time.time())
            return True
        except AccountMismatch as e:
            # L2:单账号暂停,绝不自动反复重登;其余账号不受影响
            rt.paused_reason = "mismatch"
            self._ev(rt, "account_mismatch", "账号暂停:%s;切 Steam 到该账号后,关再开勾选重试" % e)
            p.set(note="[%s] 账号不符已暂停" % rt.alias)
            return False
        except NeedsLogin as e:
            if not getattr(rt, "needslogin_logged", False):
                rt.needslogin_logged = True
                self._ev(rt, "needs_login",
                         "%s(切 Steam 到该账号后,关再开勾选或等待下一轮)" % e)
            return False
        except Exception as e:
            rt.last_error = repr(e)[:160]
            self._ev(rt, "error", "%s: %s" % (rt.alias, rt.last_error))
            return False

    # ---- 主循环:每轮顺序处理所有启用账号 ----
    def run(self):
        p = self.panel
        p.set(status="running", started_at=time.time(),
              ticks=0, ok=0, fail=0, note="")
        try:
            while not self.stop_event.is_set():
                cfg = p.get_settings()            # 逐轮快照,轮内不混用两版设置
                p.set(interval=cfg["interval"])
                enabled = [it for it in p.registry_snapshot() if it["enabled"]]
                if not enabled:
                    p.set(note="没有启用的账号,挂机空转中;勾选账号后下一轮自动恢复")
                else:
                    p.set(note="")
                all_failed = bool(enabled)
                for it in enabled:
                    if self.stop_event.is_set():
                        break
                    rt = self.runtimes.setdefault(it["userid"], AccountRuntime(
                        it["userid"], it.get("alias") or "", it["enabled"]))
                    rt.alias = it.get("alias") or rt.alias
                    rt.enabled = True
                    if rt.paused_reason:          # L2 终态:等用户关/开勾选重试
                        continue
                    self.current = rt
                    p.set(current_userid=rt.userid, current_alias=rt.alias)
                    ok = self._process_account(rt, cfg)
                    all_failed = all_failed and not ok
                    self.current = None
                    p.set(current_userid=None, current_alias=None)
                    if self.stop_event.wait(3):   # 账号间错峰
                        break
                if enabled and all_failed:
                    # 整轮全失败(如断网):退避,防紧循环
                    if self._sleep(60):
                        break
                    continue
                if self._sleep(cfg["interval"]):
                    break
        finally:
            p.set(status="stopped", current_userid=None, current_alias=None)
            self._ev(None, "stop", "worker 退出")


class Panel:
    """共享状态(读写都持锁)+ worker 生命周期管理。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.worker = None
        self._idle_store = None
        self._settings = load_settings()
        self._registry = load_accounts()
        self.state = {
            "status": "idle", "note": "", "started_at": None, "interval": 360,
            "ticks": 0, "ok": 0, "fail": 0, "last_tick_at": None,
            "current_userid": None, "current_alias": None,
            "weekly": 0, "gifts_session": 0,
        }

    # ---- 账号注册表(锁内改+原子落盘;worker 每轮只读快照) ----
    def registry_snapshot(self):
        with ACCOUNTS_LOCK:
            return [dict(it) for it in self._registry.get("accounts", [])]

    def registry_update(self, fn):
        """fn(accounts_list) 原地修改;返回 (新列表, 错误msg或None)。"""
        with ACCOUNTS_LOCK:
            accounts = self._registry.get("accounts", [])
            err = fn(accounts)
            if err is None:
                self._registry = save_accounts(self._registry)
            else:
                self._registry = validate_accounts(self._registry)
        return err

    def add_account(self, alias, desc):
        """人肉环节:Steam 已切到目标账号 → 面板代为 LoginViaSteam 并收编。"""
        with LOGIN_GATE:
            try:
                tok, uid, resp, _sid = HB.full_login(None, desc)
            except Exception as e:
                return None, "登录失败: %s(Steam 客户端须已登录目标账号)" % e, 502
        if not uid or uid == "?":
            return None, "登录响应缺少 UserID", 502
        err = self.registry_update(lambda accounts: _add_account_fn(accounts, uid, alias, resp))
        w = self.worker
        rt = w.runtimes.get(uid) if w else None
        if rt:
            rt.paused_reason = None            # 移除→重新添加 = L2 重试把手
            rt.capped = False
        if err == "dup":
            return None, "该账号已在注册表中,会话已刷新", 409
        if err:
            return None, err, 500
        row = next((it for it in self.registry_snapshot() if it["userid"] == uid), None)
        return row, "已添加 %s(%s)" % (row["alias"] if row else alias, mask_userid(uid)), 200

    def remove_account(self, userid):
        def fn(accounts):
            for i, it in enumerate(accounts):
                if it["userid"] == userid:
                    accounts.pop(i)
                    return None
            return "账号不存在"
        err = self.registry_update(fn)
        return (False, err, 404) if err else (True, "已移除(数据目录保留,可重新添加)", 200)

    def toggle_account(self, userid, enabled):
        def fn(accounts):
            for it in accounts:
                if it["userid"] == userid:
                    it["enabled"] = bool(enabled)
                    return None
            return "账号不存在"
        err = self.registry_update(fn)
        if err:
            return False, err, 404
        w = self.worker
        if enabled and w:
            rt = w.runtimes.get(userid)
            if rt:
                rt.paused_reason = None      # L2 重试把手:关→开即重试
        return True, "已%s,下一轮生效" % ("启用" if enabled else "停用",), 200

    def get_settings(self):
        with self.lock:
            return dict(self._settings)

    def apply_settings(self, new):
        """只改内存;文件由调用方先行落盘。"""
        with self.lock:
            self._settings = dict(new)

    def set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def inc(self, tick_ok=0, tick_fail=0):
        with self.lock:
            self.state["ticks"] += 1
            self.state["ok"] += tick_ok
            self.state["fail"] += tick_fail

    def bump_gift(self):
        with self.lock:
            self.state["gifts_session"] += 1

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
            s["settings"] = dict(self._settings)   # Lock 不可重入,一次取齐
        s["weekly_cap"] = WEEKLY_CAP
        s["current_userid_masked"] = mask_userid(s.get("current_userid"))
        s["accounts_enabled"] = sum(1 for it in self.registry_snapshot() if it["enabled"])
        s["uptime_s"] = int(time.time() - s["started_at"]) if s["started_at"] else 0
        s["running"] = bool(self.worker and self.worker.is_alive())
        s["version"] = "1.1"
        return s

    def current_store(self):
        w = self.worker
        if w and w.current and w.current.store:
            return w.current.store
        return None

    def idle_store(self):
        """无 worker 时展示用的账号库:取最近活跃的一个,缓存复用(不每请求新建连接)。"""
        with self.lock:
            if self._idle_store is None:
                best = None
                for uid, dbp in list_account_stores():
                    try:
                        m = os.path.getmtime(dbp)
                    except OSError:
                        continue
                    if best is None or m > best[0]:
                        best = (m, uid)
                if best:
                    self._idle_store = AccountStore(best[1])
        return self._idle_store

    def start(self, interval):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return False, "已在运行中"
            if not any(it["enabled"] for it in self.registry_snapshot()):
                return False, "没有启用的账号,请先在账号列表勾选"
            w = Worker(self, interval)
            self.worker = w
            self.state["interval"] = w.interval
            w.start()   # 在锁内启动:连点"开始"不会产生无法停止的孤儿 worker
        return True, "started"

    def stop(self):
        w = self.worker
        if not w or not w.is_alive():
            return True, "未在运行"
        w.stop_event.set()
        w.join(timeout=8)
        if w.is_alive():
            return False, "仍在停止中(等待本轮请求完成,稍后自动就绪)"
        return True, "stopped"


PANEL = Panel()


class Handler(BaseHTTPRequestHandler):
    server_version = "KleiPanel/1.0"

    def log_message(self, fmt, *args):
        pass  # 安静模式;面板自身的事件都在 worker/DB 里

    # ---- 安全边界 ----
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ---- 路由 ----
    def do_GET(self):
        if not self._host_ok():
            return self._send_json({"error": "bad host"}, 400)
        path = self.path.split("?", 1)[0]
        if path.startswith("/assets/"):
            return self._serve_asset(path)
        if path in ("/", "/index.html"):
            return self._serve_panel()
        if path == "/api/status":
            s = PANEL.snapshot()
            store = PANEL.current_store() or PANEL.idle_store()
            if store:
                s["weekly"] = store.gifts_since(time.time() - CAP_WINDOW, source="tick")
                s["today_gifts"] = store.gifts_since(_today0())
                # 领满 ETA 只在真的处于"领满暂停"时给(停止态显示会误导)
                if (s.get("running") and s.get("settings", {}).get("cap_stop")
                        and s.get("weekly", 0) >= WEEKLY_CAP):
                    oldest = store.oldest_window_gift_ts(time.time() - CAP_WINDOW)
                    if oldest:
                        s["cap_resume_eta"] = oldest + CAP_WINDOW
            else:
                s["today_gifts"] = 0
            return self._send_json(s)
        if path == "/api/settings":
            return self._send_json({"settings": PANEL.get_settings()})
        if path == "/api/gifts":
            return self._send_rows("gifts")
        if path == "/api/heartbeats":
            return self._send_rows("heartbeats")
        if path == "/api/events":
            return self._send_rows("events")
        if path == "/api/accounts":
            return self._send_json({"accounts": account_rows(PANEL)})
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._host_ok():
            return self._send_json({"error": "bad host"}, 400)
        path = self.path.split("?", 1)[0]
        if path == "/api/settings":
            body = self._read_body_json()
            if not isinstance(body, dict):
                return self._send_json({"ok": False, "error": "bad json"}, 400)
            new = validate_settings(body)
            try:
                saved = save_settings(new)
            except OSError as e:
                return self._send_json({"ok": False, "error": "写入失败: %s" % e}, 500)
            PANEL.apply_settings(saved)
            return self._send_json({"ok": True, "settings": saved})
        if path == "/api/start":
            body = self._read_body_json()
            if not isinstance(body, dict):
                return self._send_json({"error": "bad json"}, 400)
            cur = PANEL.get_settings()
            if "interval" in body:                 # 显式传入才改;否则沿用已存设置
                try:
                    cur["interval"] = int(body["interval"])
                except (TypeError, ValueError, OverflowError):
                    pass
            try:
                merged = save_settings(cur)
            except OSError:
                merged = validate_settings(cur)    # 落盘失败不阻断启动,但仍走收敛
            PANEL.apply_settings(merged)
            ok, msg = PANEL.start(merged["interval"])
            return self._send_json({"ok": ok, "message": msg}, 200 if ok else 409)
        if path == "/api/accounts/add":
            body = self._read_body_json()
            if not isinstance(body, dict):
                return self._send_json({"ok": False, "error": "bad json"}, 400)
            alias = body.get("alias") if isinstance(body.get("alias"), str) else ""
            row, msg, code = PANEL.add_account(
                alias, PANEL.get_settings()["desc"])
            payload = {"ok": code == 200, "message": msg}
            if row:
                payload["account"] = row
            return self._send_json(payload, code)
        if path == "/api/accounts/remove":
            body = self._read_body_json()
            if not isinstance(body, dict) or not isinstance(body.get("userid"), str):
                return self._send_json({"ok": False, "error": "bad json"}, 400)
            ok, msg, code = PANEL.remove_account(body["userid"])
            return self._send_json({"ok": ok, "message": msg}, code)
        if path == "/api/accounts/toggle":
            body = self._read_body_json()
            if (not isinstance(body, dict) or not isinstance(body.get("userid"), str)
                    or not isinstance(body.get("enabled"), bool)):
                return self._send_json({"ok": False, "error": "bad json"}, 400)
            ok, msg, code = PANEL.toggle_account(body["userid"], body["enabled"])
            return self._send_json({"ok": ok, "message": msg}, code)
        if path == "/api/stop":
            ok, msg = PANEL.stop()
            return self._send_json({"ok": ok, "message": msg})
        return self._send_json({"error": "not found"}, 404)

    def do_PUT(self):
        self._send_json({"error": "method not allowed"}, 405)

    do_DELETE = do_PUT
    do_PATCH = do_PUT

    def _serve_asset(self, path):
        """静态资源(web/assets/ 下,白名单扩展名;拒绝任何路径穿越)。"""
        name = path[len("/assets/"):]
        safe = os.path.normpath(name).replace("\\", "/")
        if (".." in safe or not safe or safe.startswith("/")
                or (len(safe) > 1 and safe[1] == ":")):   # 盘符绝对/相对路径逃逸
            return self._send_json({"error": "bad path"}, 400)
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".ico": "image/x-icon",
                 }.get(os.path.splitext(safe)[1].lower())
        if not ctype:
            return self._send_json({"error": "bad type"}, 400)
        fp = os.path.realpath(os.path.join(WEB_DIR, "assets", safe))
        root = os.path.realpath(os.path.join(WEB_DIR, "assets"))
        if not fp.startswith(root + os.sep):     # 双保险:realpath 包含性校验
            return self._send_json({"error": "bad path"}, 400)
        try:
            with open(fp, "rb") as f:
                body = f.read()
        except OSError:
            return self._send_json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _serve_panel(self):
        html_path = os.path.join(WEB_DIR, "挂机面板.html")
        try:
            with open(html_path, "rb") as f:
                body = f.read()
        except OSError:
            return self._send_json({"error": "panel html missing"}, 500)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_rows(self, table):
        limit = 50
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if kv.startswith("limit="):
                    try:
                        limit = max(1, min(500, int(kv[6:])))
                    except ValueError:
                        pass
        if table == "gifts":
            rows = gift_enrich(self._gifts_all_accounts(limit))
        else:
            store = PANEL.current_store() or PANEL.idle_store()
            rows = store.recent_dicts(table, limit) if store else []
        return self._send_json({"rows": rows, "table": table, "limit": limit})

    def _gifts_all_accounts(self, limit):
        """跨账号聚合最近礼物(带归属账号别名),按时间倒序截断。"""
        aliases = {it["userid"]: (it.get("alias") or mask_userid(it["userid"]))
                   for it in PANEL.registry_snapshot()}
        runtimes = PANEL.worker.runtimes if PANEL.worker else {}
        merged = []
        for uid, alias in aliases.items():
            rt = runtimes.get(uid)
            store = rt.store if rt and rt.store else None
            created = False
            if store is None and os.path.isfile(
                    os.path.join(ACCOUNTS_DIR, uid, "account.db")):
                store = AccountStore(uid)
                created = True
            if store is None:
                continue
            for r in store.recent_dicts("gifts", limit):
                r = dict(r)
                r["account"] = alias
                merged.append(r)
            if created:
                with DB_LOCK:
                    store.db.close()
        merged.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        return merged[:limit]


PORT = 8377


def main():
    global PORT
    ap = argparse.ArgumentParser(description="Klei 挂机本地控制面板(仅 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8377)
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    PORT = args.port

    migrate_accounts(PANEL)   # 首次升级收编 accounts/ 目录 → 注册表

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d" % PORT
    print("[panel] 控制面板已启动: %s  (Ctrl+C 退出)" % url, flush=True)
    print("[panel] 教程见项目根目录 挂机指南.html;黑窗口 bat 与本面板不要同时运行", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[panel] 退出", flush=True)
        PANEL.stop()
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
