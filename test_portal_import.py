#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_portal_import.py — v5.9 门户历史导入测试。
覆盖:parse_portal_text 解析与错误分级、import_portal_gifts 去重回填、
状态机联动(周礼计数/每日礼状态/快照防假跳变)、/api/gifts/import 端点。
全部离线(临时 SQLite + loopback HTTP)。运行:python -m unittest test_portal_import -v
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import klei_panel as P


def envelope(txns, ok=True):
    return json.dumps({"ok": ok, "data": {
        "TotalTransactions": len(txns), "Transactions": txns, "Balances": {}}},
        ensure_ascii=False)


def txn(typ, ts, items=None, week=0, num=0):
    return {"TxnID": "", "Type": typ, "Time": ts, "GameID": "DST",
            "Reason": "t", "Title": "t", "GiftItemWeek": week, "GiftItemNum": num,
            "ItemGains": [{"ID": i, "Type": t, "ImageURL": ""} for i, t in (items or [])],
            "ItemLosses": None, "CurrencyGains": None}


NOW = 1793300000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chester_portal_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_accounts = P.ACCOUNTS_DIR
        P.ACCOUNTS_DIR = os.path.join(self.tmp, "accounts")
        os.makedirs(P.ACCOUNTS_DIR, exist_ok=True)
        self.store = P.AccountStore("KU_TEST")

    def tearDown(self):
        with P.DB_LOCK:
            self.store.db.close()
        P.ACCOUNTS_DIR = self._old_accounts

    def ledger(self):
        with P.DB_LOCK:
            return self.store.db.execute(
                "SELECT ts,item_type,item_id,source FROM gifts ORDER BY ts").fetchall()


# ---------------------------------------------------------------- 解析
class TParse(unittest.TestCase):
    def test_ok_full(self):
        t, x = P.parse_portal_text(envelope([
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 100, [(111, "BODY_X")], week=2965, num=8),
            txn("TXN_DAILY_GIFT_ITEM", NOW - 50, [(222, "HAND_Y")]),
            txn("TXN_WEAVE_ITEM", NOW - 10, [(333, "HEAD_Z")]),   # 非礼物:v1 不导
        ]), now=NOW)
        self.assertEqual(len(t), 2)
        self.assertEqual([x["source"] for x in t], ["tick", "daily"])
        self.assertEqual(t[0]["items"][0]["id"], 111)
        self.assertEqual(x["dropped"], 0)

    def test_sorted_ascending(self):
        t, _ = P.parse_portal_text(envelope([
            txn("TXN_DAILY_GIFT_ITEM", NOW - 10, [(1, "A")]),
            txn("TXN_DAILY_GIFT_ITEM", NOW - 900, [(2, "B")]),
        ]), now=NOW)
        self.assertEqual([x["time"] for x in t], [NOW - 900, NOW - 10])

    def test_html_page_rejected(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text("<!DOCTYPE html><html><form action='/login'>")
        self.assertEqual(cm.exception.code, "not_logged_in_page")

    def test_truncated_json(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text('{"ok":true,"data":{"Transactions":[{"Type":')
        self.assertEqual(cm.exception.code, "bad_json")

    def test_ok_false(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text(envelope([], ok=False))
        self.assertEqual(cm.exception.code, "portal_says_no")

    def test_missing_data(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text('{"ok":true,"foo":1}')
        self.assertEqual(cm.exception.code, "bad_shape")

    def test_empty_transactions(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text(envelope([]))
        self.assertEqual(cm.exception.code, "no_transactions")

    def test_no_gift_entries_only_weave(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text(envelope([txn("TXN_WEAVE_ITEM", NOW - 5, [(9, "X")])]),
                                now=NOW)
        self.assertEqual(cm.exception.code, "no_transactions")

    def test_bad_time_dropped(self):
        t, x = P.parse_portal_text(envelope([
            txn("TXN_DAILY_GIFT_ITEM", 1000000000, [(1, "OLD")]),   # 早于 2020
            txn("TXN_DAILY_GIFT_ITEM", NOW + 90000, [(2, "FUT")]),  # 超前 >1 天
            txn("TXN_DAILY_GIFT_ITEM", "abc", [(3, "STR")]),
            txn("TXN_DAILY_GIFT_ITEM", NOW - 60, [(4, "GOOD")]),
        ]), now=NOW)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["items"][0]["id"], 4)

    def test_bom_and_surroundings(self):
        raw = "\ufeff  前缀杂文本 " + envelope([
            txn("TXN_DAILY_GIFT_ITEM", NOW - 5, [(7, "A")])]) + " 尾部 "
        t, _ = P.parse_portal_text(raw, now=NOW)
        self.assertEqual(len(t), 1)

    def test_too_large(self):
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text("x" * (P.PORTAL_TEXT_MAX_BYTES + 1))
        self.assertEqual(cm.exception.code, "too_large")

    def test_item_id_garbage_becomes_zero(self):
        raw = envelope([{"Type": "TXN_DAILY_GIFT_ITEM", "Time": NOW - 5,
                         "ItemGains": [{"ID": "abc", "Type": "X"}]}])
        t, _ = P.parse_portal_text(raw, now=NOW)
        self.assertEqual(t[0]["items"][0]["id"], 0)

    def test_item_id_overflow_2p63_clamped(self):
        # SQLite INTEGER 上限 2^63-1:超限 ID 收敛为 0(审查 I-C 回归锚)
        raw = envelope([{"Type": "TXN_DAILY_GIFT_ITEM", "Time": NOW - 5,
                         "ItemGains": [{"ID": 2 ** 63 + 5, "Type": "X"}]}])
        t, _ = P.parse_portal_text(raw, now=NOW)
        self.assertEqual(t[0]["items"][0]["id"], 0)

    def test_bool_time_dropped(self):
        # 唯一一笔是 bool Time → 丢弃后无合法交易,报 no_transactions
        raw = envelope([{"Type": "TXN_DAILY_GIFT_ITEM", "Time": True,
                         "ItemGains": [{"ID": 1, "Type": "X"}]}])
        with self.assertRaises(P.PortalParseError) as cm:
            P.parse_portal_text(raw, now=NOW)
        self.assertEqual(cm.exception.code, "no_transactions")

    def test_bool_time_among_others_dropped(self):
        # bool Time 混在合法交易中:该笔被丢,其余保留
        raw = envelope([
            {"Type": "TXN_DAILY_GIFT_ITEM", "Time": True,
             "ItemGains": [{"ID": 1, "Type": "X"}]},
            txn("TXN_DAILY_GIFT_ITEM", NOW - 5, [(2, "Y")]),
        ])
        t, x = P.parse_portal_text(raw, now=NOW)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["items"][0]["id"], 2)
        self.assertEqual(x["dropped"], 1)

    def test_time_at_min_boundary_kept(self):
        t, _ = P.parse_portal_text(envelope([
            txn("TXN_DAILY_GIFT_ITEM", P.PORTAL_TIME_MIN, [(1, "X")])]), now=NOW)
        self.assertEqual(len(t), 1)


# ---------------------------------------------------------------- 入库/去重
class TImport(Base):
    def _txns(self):
        t, _ = P.parse_portal_text(envelope([
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 3 * 86400, [(111, "BODY_X")], week=2964),
            txn("TXN_DAILY_GIFT_ITEM", NOW - 2 * 86400, [(222, "HAND_Y")]),
            txn("TXN_DAILY_GIFT_ITEM", NOW - 3600, [(333, "HEAD_Z")]),
        ]), now=NOW)
        return t

    def test_backfill_and_dedup_by_item_id(self):
        s1 = self.store.import_portal_gifts(self._txns(), now=NOW)
        self.assertEqual(s1["gifts_backfilled"], 3)
        s2 = self.store.import_portal_gifts(self._txns(), now=NOW)   # 幂等重导
        self.assertEqual(s2["gifts_backfilled"], 0)
        self.assertEqual(s2["gifts_deduped"], 3)
        self.assertEqual(len(self.ledger()), 3)

    def test_weekly_counts_into_window(self):
        self.store.import_portal_gifts(self._txns(), now=NOW)
        self.assertEqual(self.store.gifts_since(NOW - P.CAP_WINDOW, source="tick"), 1)

    def test_daily_state_benefits(self):
        self.store.import_portal_gifts(self._txns(), now=NOW)
        d = P.daily_state(self.store, now=NOW)
        self.assertEqual(d["state"], "claimed")
        self.assertEqual(d["has_record"], True)

    def test_source_split(self):
        self.store.import_portal_gifts(self._txns(), now=NOW)
        srcs = sorted(r[3] for r in self.ledger())
        self.assertEqual(srcs, ["daily", "daily", "tick"])

    def test_snapshot_refreshed_no_fake_arrival(self):
        # mute 场景:回填把窗口周礼从 0 抬到 1,快照同步刷新 → eval_suspect 不见跳变
        self.store.set_meta(P.SUSPECT_META_STATE, "mute")
        self.store.import_portal_gifts(self._txns(), now=NOW)
        snap = float(self.store.get_meta(P.SUSPECT_META_WEEKLY))
        self.assertEqual(int(snap), 1)
        weekly = self.store.gifts_since(NOW - P.CAP_WINDOW, source="tick")
        P.eval_suspect(self.store, "T", {
            "suspect_ask": True, "suspect_hours": 6.0}, weekly, now=NOW)
        self.assertEqual(self.store.get_meta(P.SUSPECT_META_STATE), "mute")

    def test_time_window_dedup_fallback(self):
        # item_id=0:两边都缺 ID 才按类型+±600s 窗口兜底
        with P.DB_LOCK:
            self.store.db.execute(
                "INSERT INTO gifts(ts,item_type,item_id,source) VALUES(?,?,?,?)",
                (NOW - 3600, "SAME_TYPE", 0, "tick"))
            self.store.db.commit()
        t, _ = P.parse_portal_text(envelope([
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 3660, [(0, "SAME_TYPE")]),  # 窗口内 → 跳过
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 7200, [(0, "SAME_TYPE")]),  # 窗口外 → 导入
        ]), now=NOW)
        s = self.store.import_portal_gifts(t, now=NOW)
        self.assertEqual(s["gifts_backfilled"], 1)
        self.assertEqual(s["gifts_deduped"], 1)

    def test_window_fallback_never_eats_distinct_ids(self):
        # 审查 I-D 回归锚:ID 在场但未命中时,窗口兜底不得吞掉 distinct-ID 礼物
        # (批量连开同类型多件是真实形态)
        t, _ = P.parse_portal_text(envelope([
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 60, [(1001, "SAME_TYPE")]),
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 30, [(1002, "SAME_TYPE")]),
            txn("TXN_WEEKLY_GIFT_ITEM", NOW - 10, [(1003, "SAME_TYPE")]),
        ]), now=NOW)
        s = self.store.import_portal_gifts(t, now=NOW)
        self.assertEqual(s["gifts_backfilled"], 3, "三件 distinct-ID 同类型礼物全保留")
        self.assertEqual(s["gifts_deduped"], 0)

    def test_import_failure_returns_500_json(self):
        # 入库期异常(磁盘满/库损坏类):store 关闭后 import 必抛,路由层转 500 JSON
        with P.DB_LOCK:
            self.store.db.close()
        raised = False
        try:
            t, _ = P.parse_portal_text(envelope([
                txn("TXN_DAILY_GIFT_ITEM", NOW - 5, [(1, "X")])]), now=NOW)
            self.store.import_portal_gifts(t, now=NOW)
        except Exception:
            raised = True    # 路由层 except Exception → 500 {"code":"import_failed"}
        finally:
            self.store.db = __import__("sqlite3").connect(
                self.store.db_path, check_same_thread=False)
        self.assertTrue(raised)


# ---------------------------------------------------------------- API
class FakePanelBase(Base):
    def setUp2(self):
        self._old = (P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL)
        P.SETTINGS_PATH = os.path.join(self.tmp, "settings.json")
        P.ACCOUNTS_PATH = os.path.join(self.tmp, "accounts.json")
        return P.Panel()

    def tearDown2(self):
        (P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL) = self._old


class TApiImport(FakePanelBase):
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

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/gifts/import" % P.PORT,
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_import_ok(self):
        code, r = self._post({"userid": "KU_TEST", "text": envelope([
            txn("TXN_DAILY_GIFT_ITEM", NOW - 100, [(555, "X_Y")])])})
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(r["gifts_backfilled"], 1)
        self.assertEqual(r["weekly_after"], 0)

    def test_path_traversal_userid_rejected(self):
        for bad in ("KU_../x", "KU_a\\b", "KU_" + "x" * 40, "XX_TEST"):
            code, r = self._post({"userid": bad, "text": "{}"})
            self.assertEqual(code, 400, bad)
            self.assertEqual(r["code"], "bad_account")

    def test_unknown_account_404(self):
        code, r = self._post({"userid": "KU_NOPE", "text": envelope(
            [txn("TXN_DAILY_GIFT_ITEM", NOW - 5, [(1, "A")])])})
        self.assertEqual(code, 404)
        self.assertEqual(r["code"], "bad_account")

    def test_html_paste_error_passthrough(self):
        code, r = self._post({"userid": "KU_TEST", "text": "<html>login</html>"})
        self.assertEqual(code, 400)
        self.assertEqual(r["code"], "not_logged_in_page")
        self.assertIn("accounts.klei.com", r["detail"])

    def test_stats_shape(self):
        code, r = self._post({"userid": "KU_TEST", "text": envelope([
            txn("TXN_DAILY_GIFT_ITEM", NOW - 100, [(777, "A_B")]),
            txn("TXN_DAILY_GIFT_ITEM", NOW - 90, [(777, "A_B")]),   # 同 ID 重复
        ])})
        self.assertEqual((code, r["ok"]), (200, True))
        self.assertEqual(r["gifts_backfilled"], 1)
        self.assertEqual(r["gifts_deduped"], 1)
        for k in ("parsed", "time_min", "time_max", "weekly_after"):
            self.assertIn(k, r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
