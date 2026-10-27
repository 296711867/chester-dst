#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_suspect.py — 疑似满仓监控(dry-watch)测试:判定 SQL / 状态机 / 降频 / 应答端点。
全部离线(临时 SQLite + monkeypatch HB.tick + loopback HTTP),运行:
    python -m unittest test_suspect -v
"""
import json
import sqlite3
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import klei_panel as P

CFG = {"interval": 360, "auto_box": False, "daily_gift": False, "cap_stop": False,
       "desc": "t", "suspect_ask": True, "suspect_hours": 6.0}


def hb(store, ts, ok=1, note="ok"):
    with P.DB_LOCK:
        store.db.execute(
            "INSERT INTO heartbeats(ts,ok,http,note) VALUES(?,?,?,?)",
            (ts, ok, 200, note))
        store.db.commit()


def gift(store, ts, source="tick"):
    with P.DB_LOCK:
        store.db.execute(
            "INSERT INTO gifts(ts,item_type,item_id,source) VALUES(?,?,?,?)",
            (ts, "BODY_X", 0, source))
        store.db.commit()


def events(store):
    with P.DB_LOCK:
        return [(r[0], r[1]) for r in store.db.execute(
            "SELECT kind,detail FROM events ORDER BY id").fetchall()]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chester_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old_accounts = P.ACCOUNTS_DIR
        P.ACCOUNTS_DIR = os.path.join(self.tmp, "accounts")
        os.makedirs(P.ACCOUNTS_DIR, exist_ok=True)
        self.store = P.AccountStore("KU_TEST")

    def tearDown(self):
        with P.DB_LOCK:
            self.store.db.close()
        P.ACCOUNTS_DIR = self._old_accounts


class TDryStats(Base):
    def test_empty(self):
        self.assertEqual(self.store.dry_stats(), (0, None, 0))

    def test_gift_anchor(self):
        now = time.time()
        gift(self.store, now - 1000)
        for i in range(60):
            hb(self.store, now - 900 + i * 10)
        n, anchor, stuck = self.store.dry_stats()
        self.assertEqual((n, stuck), (60, 0))
        self.assertGreaterEqual(anchor, now - 1000)

    def test_first_ok_tick_anchor_when_no_gift(self):
        now = time.time()
        for i in range(59):
            hb(self.store, now - 4000 + i * 10)
        n, anchor, stuck = self.store.dry_stats()
        self.assertEqual(n, 59)
        self.assertLess(anchor, now - 3900)

    def test_failed_ticks_excluded(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10, ok=0, note="net")
        self.assertEqual(self.store.dry_stats()[0], 0)

    def test_window_cutoff_ignores_last_month(self):
        now = time.time()
        for i in range(59):                      # 上月闲置尾巴:窗口外
            hb(self.store, now - 8 * 86400 + i * 10)
        self.assertEqual(self.store.dry_stats(), (0, None, 0))

    def test_stuck_counted(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10,
               note="E_UNOPENED_ITEMS" if i % 20 == 0 else "ok")
        n, _a, stuck = self.store.dry_stats()
        self.assertEqual((n, stuck), (60, 3))

    def test_daily_gift_not_anchor(self):
        now = time.time()
        gift(self.store, now - 100, source="daily")
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        n, anchor, _s = self.store.dry_stats()
        self.assertEqual(n, 60)                  # daily 不重置干燥期
        self.assertLess(anchor, now - 3900)


class TEvalSuspect(Base):
    def test_threshold_entry_and_event(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        state, ask_ts, dry_h, ok_n, stuck = P.eval_suspect(
            self.store, "T", CFG, 0, now=now)
        self.assertEqual(state, "ask")
        self.assertEqual(ok_n, 60)
        self.assertEqual(dry_h, 6.0)
        self.assertEqual(events(self.store)[-1][0], "suspect_ask")
        self.assertEqual(self.store.get_meta(P.SUSPECT_META_STATE), "ask")

    def test_below_threshold_stays_normal(self):
        now = time.time()
        for i in range(59):
            hb(self.store, now - 4000 + i * 10)
        state = P.eval_suspect(self.store, "T", CFG, 0, now=now)[0]
        self.assertIsNone(state)

    def test_timeout_auto_continue(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)
        state, _ts, _h, _n, _s = P.eval_suspect(
            self.store, "T", CFG, 0, now=now + P.SUSPECT_ASK_TIMEOUT_S + 1)
        self.assertEqual(state, "ack")
        self.assertIn("suspect_continue", [k for k, _ in events(self.store)])

    def test_cooldown_reask(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)
        ack_at = now + P.SUSPECT_ASK_TIMEOUT_S + 1
        P.eval_suspect(self.store, "T", CFG, 0, now=ack_at)
        # 冷却内(24h)不重问
        s1 = P.eval_suspect(self.store, "T", CFG, 0, now=ack_at + 3600)[0]
        self.assertEqual(s1, "ack")
        # 冷却结束重问
        s2 = P.eval_suspect(self.store, "T", CFG, 0,
                            now=ack_at + P.SUSPECT_REASK_COOLDOWN_S + 1)[0]
        self.assertEqual(s2, "ask")

    def test_gift_arrival_clears(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)
        gift(self.store, now + 1)                 # 探测 tick 带回礼物
        state = P.eval_suspect(self.store, "T", CFG, 1, now=now + 2)[0]  # weekly 0→1
        self.assertIsNone(state)
        evs = [k for k, _ in events(self.store)]
        self.assertIn("suspect_clear", evs)
        self.assertIn("gift", dict(events(self.store))["suspect_clear"])
        self.assertIsNone(self.store.get_meta(P.SUSPECT_META_STATE))

    def test_threshold_raise_keeps_mute(self):
        """审查 I-1:阈值上调不得误清 mute(用户明确手势),也不得谎报 gift 事件。"""
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)          # weekly 快照=0
        self.store.set_meta(P.SUSPECT_META_STATE, "mute")         # 用户选了暂停
        cfg12 = dict(CFG, suspect_hours=12.0)
        state = P.eval_suspect(self.store, "T", cfg12, 0, now=now + 1)[0]
        self.assertEqual(state, "mute")                            # 保留
        evs = dict(events(self.store))
        self.assertNotIn("suspect_clear", evs)                     # 无谎报事件
        # ask 挂起时阈值上调 → 降级 ack(记 recheck),冷却记忆保留
        P._suspect_wipe(self.store)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)           # 重新进入 ask
        self.store.set_meta(P.SUSPECT_META_ASK, repr(now))
        state, ask_ts, _h, _n, _s = P.eval_suspect(
            self.store, "T", cfg12, 0, now=now + 2)
        self.assertEqual(state, "ack")
        self.assertIn("recheck", dict(events(self.store)).get("suspect_clear", ""))
        ack_ts = float(self.store.get_meta(P.SUSPECT_META_ACK))
        self.assertGreater(ack_ts, 0)                              # 冷却记忆在

    def test_settings_off_retracts_pending_ask(self):
        """审查 N-2:关闭问询开关,挂起中的 ask 立即撤回(不等 30 分钟超时)。"""
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)
        self.assertEqual(self.store.get_meta(P.SUSPECT_META_STATE), "ask")
        cfg_off = dict(CFG, suspect_ask=False)
        state = P.eval_suspect(self.store, "T", cfg_off, 0, now=now + 1)[0]
        self.assertEqual(state, "ack")
        evs = dict(events(self.store))
        self.assertIn("问询已关闭", evs.get("suspect_continue", ""))

    def test_capped_guard_blocks_and_keeps_mute(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        # weekly>=8 时不进入问询
        s = P.eval_suspect(self.store, "T", CFG, 8, now=now)[0]
        self.assertIsNone(s)
        # mute 是用户明确选择:实证领满也保留
        self.store.set_meta(P.SUSPECT_META_STATE, "mute")
        s = P.eval_suspect(self.store, "T", CFG, 8, now=now)[0]
        self.assertEqual(s, "mute")

    def test_switch_off_never_asks(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        cfg = dict(CFG, suspect_ask=False)
        s = P.eval_suspect(self.store, "T", cfg, 0, now=now)[0]
        self.assertIsNone(s)


class TSettings(unittest.TestCase):
    def test_clamps(self):
        out = P.validate_settings({"suspect_hours": 20})
        self.assertEqual(out["suspect_hours"], 12.0)
        out = P.validate_settings({"suspect_hours": 1})
        self.assertEqual(out["suspect_hours"], 3.0)
        out = P.validate_settings({"suspect_hours": "abc"})
        self.assertEqual(out["suspect_hours"], 6.0)
        out = P.validate_settings({"suspect_ask": False})
        self.assertFalse(out["suspect_ask"])
        self.assertEqual(P.suspect_threshold_ticks({"suspect_hours": 6.0}), 60)


class FakePanelBase(Base):
    def setUp2(self):
        self._old = (P.ACCOUNTS_DIR, P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL)
        P.SETTINGS_PATH = os.path.join(self.tmp, "settings.json")
        P.ACCOUNTS_PATH = os.path.join(self.tmp, "accounts.json")
        return P.Panel()

    def tearDown2(self):
        (P.ACCOUNTS_DIR, P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL) = self._old


class TThrottle(FakePanelBase):
    def setUp(self):
        super().setUp()
        self.panel = self.setUp2()
        self.worker = P.Worker(self.panel, 360)

    def tearDown(self):
        self.tearDown2()
        super().tearDown()

    def _rt(self):
        rt = P.AccountRuntime("KU_TEST", "T")
        rt.store = self.store
        rt.token = "TOKEN"
        return rt

    def test_mute_spacing_skips_ticks(self):
        now = time.time()
        self.store.set_meta(P.SUSPECT_META_STATE, "mute")
        hb(self.store, now - P.SUSPECT_MUTE_PROBE_S - 5)   # 上次活动 >2h 前
        calls = []

        def fake_tick(token):
            calls.append(token)
            return 200, {}, "{}"
        rt = self._rt()
        orig = P.HB.tick
        P.HB.tick = fake_tick
        try:
            self.assertTrue(self.worker._process_account(rt, CFG))   # 间距外 → tick
            self.assertEqual(len(calls), 1)
            hbs = self.store.recent("heartbeats", 10)
            self.assertTrue(self.worker._process_account(rt, CFG))   # 间距内 → 让位
            self.assertEqual(len(calls), 1)                          # 不发 tick
            self.assertEqual(len(self.store.recent("heartbeats", 10)),
                             len(hbs))                                # 也不写心跳
        finally:
            P.HB.tick = orig

    def test_ask_spacing_and_load(self):
        now = time.time()
        self.store.set_meta(P.SUSPECT_META_STATE, "ask")
        self.store.set_meta(P.SUSPECT_META_ASK, repr(now - 10))
        hb(self.store, now - P.SUSPECT_ASK_PROBE_S - 5)
        calls = []
        rt = self._rt()
        orig = P.HB.tick
        P.HB.tick = lambda token: (calls.append(token), (200, {}, "{}"))[1]
        try:
            self.assertTrue(self.worker._process_account(rt, CFG))
            self.assertEqual(len(calls), 1)
            self.assertTrue(self.worker._process_account(rt, CFG))
            self.assertEqual(len(calls), 1)
            # _suspect_load 镜像正确
            self.assertEqual(rt.suspect_state, "ask")
            self.assertAlmostEqual(rt.suspect_ask_ts, now - 10, delta=1)
        finally:
            P.HB.tick = orig

    def test_normal_no_throttle(self):
        now = time.time()
        for i in range(3):
            hb(self.store, now - 60 + i)
        calls = []
        rt = self._rt()
        orig = P.HB.tick
        P.HB.tick = lambda token: (calls.append(token), (200, {}, "{}"))[1]
        try:
            for _ in range(3):
                self.assertTrue(self.worker._process_account(rt, CFG))
            self.assertEqual(len(calls), 3)
        finally:
            P.HB.tick = orig


class TAnswerEndpoint(FakePanelBase):
    def setUp(self):
        super().setUp()
        self.panel = self.setUp2()
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True}]}

    def tearDown(self):
        self.tearDown2()
        super().tearDown()

    def _post(self, port, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/suspect/answer" % port,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=10)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_answer_flow(self):
        self.store.set_meta(P.SUSPECT_META_STATE, "ask")
        old_port, old_panel = P.PORT, P.PANEL
        P.PANEL = self.panel
        srv = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        P.PORT = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            code, r = self._post(P.PORT, {"userid": "KU_TEST", "action": "continue"})
            self.assertEqual(code, 200)
            self.assertTrue(r["ok"])
            self.assertEqual(self.store.get_meta(P.SUSPECT_META_STATE), "ack")

            code, r = self._post(P.PORT, {"userid": "KU_TEST", "action": "mute"})
            self.assertEqual(code, 200)
            self.assertEqual(self.store.get_meta(P.SUSPECT_META_STATE), "mute")
            self.assertIn("suspect_mute", [k for k, _ in events(self.store)])

            code, _ = self._post(P.PORT, {"userid": "KU_NOPE", "action": "continue"})
            self.assertEqual(code, 404)
            code, _ = self._post(P.PORT, {"userid": "KU_TEST", "action": "bogus"})
            self.assertEqual(code, 400)
            code, _ = self._post(P.PORT, {"userid": 123, "action": "continue"})
            self.assertEqual(code, 400)
        finally:
            srv.shutdown()
            srv.server_close()
            P.PORT, P.PANEL = old_port, old_panel

    def test_toggle_clears_state(self):
        self.store.set_meta(P.SUSPECT_META_STATE, "mute")
        ok, _msg, code = self.panel.toggle_account("KU_TEST", True)
        self.assertTrue(ok)
        self.assertEqual(code, 200)
        self.assertIsNone(self.store.get_meta(P.SUSPECT_META_STATE))
        self.assertIn("suspect_clear", [k for k, _ in events(self.store)])


class TEvResilience(Base):
    """审查 B2-I1:错误兜底路径的事件写入绝不能反噬(持续锁下 worker 必须存活)。"""

    def test_ev_survives_db_failure(self):
        worker = P.Worker.__new__(P.Worker)      # 只用 _ev,不跑 __init__
        rt = P.AccountRuntime("KU_TEST", "T")
        rt.store = self.store

        def boom(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        self.store.add_event = boom
        try:
            worker._ev(rt, "error", "兜底路径零 DB 依赖")
            P.emit_event(self.store, "error", "同上")
        except Exception as e:
            self.fail("事件写入反噬调用方: %r" % e)


class TSuspectRows(FakePanelBase):
    def setUp(self):
        super().setUp()
        self.panel = self.setUp2()
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True}]}

    def tearDown(self):
        self.tearDown2()
        super().tearDown()

    def test_rows_and_lazy_timeout(self):
        now = time.time()
        for i in range(60):
            hb(self.store, now - 4000 + i * 10)
        P.eval_suspect(self.store, "T", CFG, 0, now=now)   # 进 ask
        rows = P.suspect_rows(self.panel)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["userid"], "KU_TEST")
        self.assertEqual(rows[0]["dry_h"], 6.0)
        self.assertEqual(rows[0]["stuck_unopened"], 0)
        # 懒推进:把 ask 时间拨到 31 分钟前 → status 组装时应自动转 ack
        self.store.set_meta(P.SUSPECT_META_ASK,
                            repr(now - P.SUSPECT_ASK_TIMEOUT_S - 1))
        rows = P.suspect_rows(self.panel)
        self.assertEqual(rows, [])
        self.assertEqual(self.store.get_meta(P.SUSPECT_META_STATE), "ack")


if __name__ == "__main__":
    unittest.main()
