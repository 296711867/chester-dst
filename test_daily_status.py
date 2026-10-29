#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_daily_status.py — 每日礼/周礼状态计算 + AI 自动化接口测试。
覆盖:daily_state 状态机(claimed/probing/due/误章免疫/空库)、account_rows 嵌块、
GET /api/automation(pending_actions 规则)、/api/gifts 过滤参数(下推 SQL/回显/分页)、
前端 i18n 完整性与卡片接线。全部离线(临时 SQLite + loopback HTTP)。运行:
    python -m unittest test_daily_status -v
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import klei_panel as P


def gift(store, ts, source="tick", item_type="BODY_X"):
    with P.DB_LOCK:
        store.db.execute(
            "INSERT INTO gifts(ts,item_type,item_id,source) VALUES(?,?,?,?)",
            (ts, item_type, 0, source))
        store.db.commit()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chester_daily_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_accounts = P.ACCOUNTS_DIR
        P.ACCOUNTS_DIR = os.path.join(self.tmp, "accounts")
        os.makedirs(P.ACCOUNTS_DIR, exist_ok=True)
        self.store = P.AccountStore("KU_TEST")

    def tearDown(self):
        with P.DB_LOCK:
            self.store.db.close()
        P.ACCOUNTS_DIR = self._old_accounts


class FakePanelBase(Base):
    def setUp2(self):
        self._old = (P.ACCOUNTS_DIR, P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL)
        P.SETTINGS_PATH = os.path.join(self.tmp, "settings.json")
        P.ACCOUNTS_PATH = os.path.join(self.tmp, "accounts.json")
        return P.Panel()

    def tearDown2(self):
        (P.ACCOUNTS_DIR, P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL) = self._old


# ---------------------------------------------------------------- daily_state
class TDailyState(Base):
    def test_claimed_within_24h(self):
        now = time.time()
        gift(self.store, now - 2 * 3600, source="daily")
        d = P.daily_state(self.store, now=now)
        self.assertEqual(d["state"], "claimed")
        self.assertTrue(d["has_record"])
        self.assertAlmostEqual(d["next_eligible_ts"],
                               now - 2 * 3600 + P.DAILY_ROLL_S, delta=1)
        self.assertIsNone(d["retry_ts"])

    def test_due_after_roll(self):
        now = time.time()
        gift(self.store, now - 25 * 3600, source="daily")
        d = P.daily_state(self.store, now=now)
        self.assertEqual(d["state"], "due")
        self.assertIsNone(d["next_eligible_ts"])

    def test_boundary_exactly_24h_is_due(self):
        now = time.time()
        gift(self.store, now - P.DAILY_ROLL_S, source="daily")
        d = P.daily_state(self.store, now=now)
        self.assertEqual(d["state"], "due")   # now < next 为假 → 不算 claimed

    def test_probing_when_retry_active(self):
        now = time.time()
        gift(self.store, now - 25 * 3600, source="daily")
        self.store.set_meta("daily_retry_ts", repr(now + 3600))
        d = P.daily_state(self.store, now=now)
        self.assertEqual(d["state"], "probing")
        self.assertAlmostEqual(d["retry_ts"], now + 3600, delta=1)

    def test_probing_no_record_new_account(self):
        now = time.time()
        self.store.set_meta("daily_retry_ts", repr(now + 1800))
        d = P.daily_state(self.store, now=now)
        self.assertEqual(d["state"], "probing")
        self.assertFalse(d["has_record"])

    def test_due_fresh_account(self):
        d = P.daily_state(self.store, now=time.time())
        self.assertEqual(d["state"], "due")
        self.assertFalse(d["has_record"])
        self.assertIsNone(d["last_claim_ts"])
        self.assertIsNone(d["next_eligible_ts"])

    def test_stale_stamp_immune(self):
        # 老版本误章:meta 有 last_daily_ts 但 gifts 表无 daily 记录 → 绝不显示 claimed
        now = time.time()
        self.store.set_meta("last_daily_ts", repr(now - 3600))
        d = P.daily_state(self.store, now=now)
        self.assertNotEqual(d["state"], "claimed")
        self.assertFalse(d["has_record"])

    def test_store_none(self):
        d = P.daily_state(None, now=time.time())
        self.assertEqual(d["state"], "due")
        self.assertFalse(d["has_record"])

    def test_tick_gifts_do_not_count(self):
        # 只有 source='daily' 才算每日礼;tick 礼再多也不影响
        now = time.time()
        gift(self.store, now - 100, source="tick")
        d = P.daily_state(self.store, now=now)
        self.assertFalse(d["has_record"])
        self.assertEqual(d["state"], "due")


# ------------------------------------------------------------- account_rows
class TAccountRowsDaily(FakePanelBase):
    def setUp(self):
        super().setUp()
        self.panel = self.setUp2()
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True}]}
        self.store.set_meta("daily_retry_ts", repr(time.time() + 1800))

    def tearDown(self):
        self.tearDown2()
        super().tearDown()

    def test_daily_block_present(self):
        rows = P.account_rows(self.panel)
        self.assertEqual(len(rows), 1)
        d = rows[0]["daily"]
        for k in ("state", "has_record", "last_claim_ts",
                  "next_eligible_ts", "retry_ts"):
            self.assertIn(k, d)
        self.assertEqual(d["state"], "probing")

    def test_legacy_fields_intact(self):
        row = P.account_rows(self.panel)[0]
        for k in ("userid", "alias", "enabled", "weekly", "today",
                  "status", "daily"):
            self.assertIn(k, row)


# ------------------------------------------------------- /api/automation
class TApiAutomation(FakePanelBase):
    def setUp(self):
        super().setUp()
        self.panel = self.setUp2()
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True}]}
        self._old_route = (P.PORT, P.PANEL)
        P.PANEL = self.panel
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        P.PORT = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        (P.PORT, P.PANEL) = self._old_route
        self.tearDown2()
        super().tearDown()

    def _get(self, path):
        with urllib.request.urlopen(
                "http://127.0.0.1:%d%s" % (P.PORT, path), timeout=10) as r:
            return json.loads(r.read())

    def test_shape(self):
        s = self._get("/api/automation")
        self.assertEqual(s["schema"], 1)
        self.assertIn("server_time", s)
        self.assertIn("pending_actions", s)
        self.assertIn("actions", s)
        acc = s["accounts"][0]
        self.assertEqual(acc["userid"], "KU_TEST")
        self.assertIn("daily", acc)
        self.assertIn("weekly_capped", acc)
        self.assertEqual(s["daily"]["accounts_n"], 1)

    def test_pending_start_when_idle(self):
        # 面板未运行 + 有启用账号 → AI 该去 POST /api/start
        s = self._get("/api/automation")
        self.assertFalse(s["running"])
        self.assertIn("start", s["pending_actions"])

    def test_pending_login_when_no_session(self):
        # 启用账号无 session.json → needs_login → pending 含 login
        s = self._get("/api/automation")
        self.assertEqual(s["accounts"][0]["status"], "needs_login")
        self.assertIn("login", s["pending_actions"])

    def test_recent_gifts_and_filters_echo(self):
        now = time.time()
        gift(self.store, now - 10, source="daily")
        s = self._get("/api/automation")
        self.assertEqual(len(s["recent_gifts"]), 1)
        self.assertEqual(s["recent_gifts"][0]["source"], "daily")


# ------------------------------------------------- /api/gifts 查询参数
class TApiGiftsParams(FakePanelBase):
    def setUp(self):
        super().setUp()
        self.panel = self.setUp2()
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True}]}
        self._old_route = (P.PORT, P.PANEL)
        P.PANEL = self.panel
        now = time.time()
        # 稀疏场景:60 条 tick 在前 + 1 条 daily 在最早(daily 早于最近 50 条 tick)
        for i in range(60):
            gift(self.store, now - 100 - i, source="tick")
        gift(self.store, now - 10000, source="daily")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        P.PORT = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        (P.PORT, P.PANEL) = self._old_route
        self.tearDown2()
        super().tearDown()

    def _get(self, qs):
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/gifts%s" % (P.PORT, qs), timeout=10) as r:
            return json.loads(r.read())

    def test_source_filter_pushed_down(self):
        # 无 SQL 下推时 daily(第 61 条)会被每库 limit 截掉;下推后必须能取到
        r = self._get("?source=daily")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["rows"][0]["source"], "daily")
        self.assertEqual(r["filters"]["source"], ["daily"])

    def test_since_filter(self):
        now = time.time()
        r = self._get("?since=%.0f" % (now - 200))
        self.assertTrue(all(row["ts"] > now - 200 for row in r["rows"]))
        self.assertGreaterEqual(r["count"], 50)

    def test_account_filter_and_unknown(self):
        r = self._get("?account=KU_TEST&source=daily")
        self.assertEqual(r["count"], 1)
        r2 = self._get("?account=KU_NOPE")
        self.assertEqual(r2["count"], 0)
        self.assertEqual(r2["filters"]["account"], "KU_NOPE")

    def test_offset_pagination(self):
        a = self._get("?limit=5")
        b = self._get("?limit=5&offset=5")
        self.assertEqual(len(a["rows"]), 5)
        self.assertEqual(len(b["rows"]), 5)
        self.assertNotEqual(a["rows"][0]["ts"], b["rows"][0]["ts"])
        self.assertTrue(b["rows"][0]["ts"] < a["rows"][-1]["ts"])

    def test_lenient_bad_params(self):
        # 坏值宽松收敛,不 400:source 只留白名单内的,since=abc 归 0
        r = self._get("?source=xxx,daily&since=abc&limit=99999")
        self.assertEqual(r["filters"]["source"], ["daily"])
        self.assertEqual(r["filters"]["since"], 0)
        self.assertEqual(r["limit"], 500)
        self.assertEqual(r["rows"][0]["source"], "daily")

    def test_default_no_filter_unchanged(self):
        r = self._get("?limit=10")
        self.assertEqual(len(r["rows"]), 10)
        self.assertIsNone(r["filters"]["source"])

    def test_multi_account_pagination_no_gap(self):
        # 多账号 ts 交错时,offset 翻页必须与全量排序逐条相等(全局窗口无漏行)
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True},
                {"userid": "KU_B", "alias": "B", "enabled": False}]}
        s2 = P.AccountStore("KU_B")
        now = time.time()
        for i in range(30):                      # 两库 ts 交错:A 偶数秒,B 奇数秒
            gift(self.store, now - 2000 - i * 2, source="tick")
            gift(s2, now - 2001 - i * 2, source="tick")
        try:
            full = self._get("?limit=200")       # 全量基准(两库共 121 条)
            truth = [r["ts"] for r in full["rows"]]
            paged = []
            off = 0
            while True:
                r = self._get("?limit=7&offset=%d" % off)
                if not r["rows"]:
                    break
                paged += [x["ts"] for x in r["rows"]]
                off += 7
            self.assertEqual(paged, truth)
        finally:
            with P.DB_LOCK:
                s2.db.close()


# ------------------------------------------------- 前端静态完整性
class TFrontend(unittest.TestCase):
    HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "web", "挂机面板.html")

    @classmethod
    def setUpClass(cls):
        with open(cls.HTML, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_daily_tiles_wired(self):
        for i in ("daily-sum", "daily-n", "dailyref", "weekref"):
            self.assertIn('id="%s"' % i, self.html, "缺少 %s 接线" % i)
        self.assertIn("dailyChip(a)", self.html)
        self.assertIn("fmtRel(", self.html)

    def test_i18n_keys_both_langs(self):
        # 本次新增的键必须 zh/en 双语言齐(显式清单,不扫既有键如 daily_gift)
        keys = ["daily_k", "daily_next", "daily_all", "daily_again",
                "daily_stopped", "daily_off_short", "daily_c", "daily_due",
                "daily_none", "daily_norec", "daily_closed",
                "daily_tip_claimed", "daily_tip_due", "daily_tip_none",
                "daily_tip_norec", "daily_tip_off", "week_capped_n"]
        self.assertIn("var I18N={", self.html)
        for key in keys:
            self.assertEqual(self.html.count(key + ":"), 2,
                             "%s 应在 zh/en 两套字典各出现一次" % key)
        # 双波浪号防回归:fmtRel 自带 "~" 前缀,i18n 模板不得再带(审查 A-I1)
        for key in ("daily_c", "daily_none"):
            for m in re.findall(key + r':"([^"]*)"', self.html):
                self.assertNotIn("~", m,
                                  "%s 模板含 '~' 会与 fmtRel 叠成 '~~'" % key)

    def test_i18n_portal_import_keys_both_langs(self):
        # v5.9 门户导入向导:i18n 键双语言齐
        keys = ["imp_open", "imp_title", "imp_close", "imp_step1", "imp_step2",
                "imp_step3", "imp_open_login", "imp_open_data", "imp_acct",
                "imp_placeholder", "imp_go", "imp_busy", "imp_done", "imp_scope",
                "imp_range", "imp_reimport", "imp_done_zero", "imp_err_empty",
                "imp_err_html", "imp_err_format", "imp_err_acct", "imp_err_server",
                "flt_all", "gift_more", "gifts_sub_filter"]
        for key in keys:
            self.assertEqual(self.html.count(key + ":"), 2,
                             "%s 应在 zh/en 两套字典各出现一次" % key)

    def test_import_wizard_wired(self):
        for i in ("impBtn", "impMask", "impText", "impGo", "impAcct", "impNote",
                  "giftFlt", "giftMore", "giftsSub"):
            self.assertIn('id="%s"' % i, self.html, "缺少 %s 接线" % i)
        self.assertIn("https://accounts.klei.com/account/transactions/data.json", self.html)
        self.assertIn('rel="noopener noreferrer"', self.html)
        self.assertIn('/api/gifts/import', self.html)
        self.assertIn("loadGiftPage", self.html)

    def test_i18n_portal_v6_keys_both_langs(self):
        # v6.0 门户一键对账:i18n 双语言齐
        keys = ["days_h", "min_h", "portal_acct_tag", "portal_week_n",
                "portal_week_ends", "portal_week_left", "portal_week_tip",
                "portal_stale", "portal_synced_at", "portal_state_none",
                "portal_state_wait", "portal_state_ready", "portal_state_expired",
                "portal_state_error", "portal_connected", "portal_unmatched",
                "portal_recon_more", "portal_login_probe_fail",
                "imp_open_login_win", "imp_probe", "imp_cancel", "imp_advanced",
                "imp_login_hint", "imp_onekey", "imp_onekey_busy",
                "imp_err_portal_login", "imp_err_portal_fetch",
                "imp_err_portal_kernel"]
        for key in keys:
            self.assertEqual(self.html.count(key + ":"), 2,
                             "%s 应在 zh/en 两套字典各出现一次" % key)

    def test_portal_ui_wired(self):
        for i in ("portalRef", "portalBox", "portalDot", "portalStateTxt",
                  "portalSynced", "portalWeekLine", "portalHint",
                  "impOpenLogin", "impOnekey", "impProbe", "impCancelLogin",
                  "impAdvanced"):
            self.assertIn('id="%s"' % i, self.html, "缺少 %s 接线" % i)
        for s in ("/api/portal/session_status", "/api/portal/probe",
                  "/api/portal/open_login", "/api/portal/onekey_import",
                  "portalReconcile", "renderPortalRef"):
            self.assertIn(s, self.html)
        self.assertIn("we>1e12", self.html)   # WeekEnd 毫秒防御回归锚

    def test_legacy_import_wizard_intact(self):
        # 旧粘贴向导降级为折叠但全部可达
        for i in ("impText", "impGo", "impAcct", "impNote", "impAdvanced"):
            self.assertIn('id="%s"' % i, self.html)
        self.assertIn("data-i18n=\"imp_advanced\"", self.html)

    def test_prog_grid_four_cols(self):
        self.assertIn("grid-template-columns:1.2fr 1fr 1fr 1fr", self.html)
        self.assertIn("grid-template-columns:1fr 1fr", self.html)   # 移动端 2 列


if __name__ == "__main__":
    unittest.main(verbosity=2)
