#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_portal_session.py — v6.0 门户一键对账测试。
覆盖:PortalSession 状态机(probe/open_login/cancel/缓存持久化/快照安全)、
portal_reconcile 三态、drops 解析、onekey 路由(loopback)、内核检测注入。
全部离线(临时目录+loopback HTTP+fake fetcher)。运行:
    python -m unittest test_portal_session -v
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import klei_panel as P
import klei_portal_browser as PB

NOW = 1793300000.0
DROPS_OK = {"ok": True, "data": {
    "DropsInfo": {"DST": {"ClaimedDrops": 8, "MaxDrops": 8,
                          "GameName": "Don't Starve Together"}},
    "CurrentWeek": 2965, "WeekEnd": 1793309999999, "Locale": "zh-CN"}}
SHELL_HTML = "<!DOCTYPE html><html><head><title>Klei Account</title></head></html>"


def envelope(txns):
    return {"ok": True, "data": {"TotalTransactions": len(txns),
                                 "Transactions": txns, "Balances": {}}}


def txn(typ, ts, items):
    return {"Type": typ, "Time": ts, "GiftItemWeek": 0, "GiftItemNum": 0,
            "ItemGains": [{"ID": i, "Type": t, "ImageURL": ""} for i, t in items],
            "ItemLosses": None}


class FakeBrowser:
    """PortalBrowser 替身:fetch_dump 按脚本返回;open_login 记录调用。"""

    def __init__(self, script=None, available=True):
        self.script = list(script or [])      # 每次 fetch 弹出一个
        self.available = available
        self.login_calls = 0
        self.name = "fake"

    def fetch_dump(self, url, timeout_s=45.0):
        item = self.script.pop(0) if self.script else SHELL_HTML
        v = item() if callable(item) else item
        return v

    def open_login_window(self, url=None):
        self.login_calls += 1
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chester_psess_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.profile = os.path.join(self.tmp, "profile")
        self.cache = os.path.join(self.tmp, "drops_cache.json")

    def mk(self, script=None):
        b = FakeBrowser(script)
        s = P.PortalSession(self.profile, self.cache, browser=b)
        return s, b


class TPortalSession(Base):
    def test_initial_state_none(self):
        s, _ = self.mk()
        self.assertEqual(s.state, "none")
        self.assertIsNone(s.drops)

    def test_probe_success_ready_and_cache(self):
        s, _ = self.mk([json.dumps(DROPS_OK)])
        ok, code = s.probe()
        self.assertTrue(ok)
        self.assertEqual(s.state, "ready")
        self.assertEqual(s.drops["current_week"], 2965)
        self.assertEqual(s.drops["week_end_ms"], 1793309999999)
        # 缓存持久化:新实例读到 ready
        s2 = P.PortalSession(self.profile, self.cache, browser=FakeBrowser())
        self.assertEqual(s2.state, "ready")
        self.assertEqual(s2.drops["current_week"], 2965)

    def test_probe_shell_page_not_logged_in(self):
        s, _ = self.mk([SHELL_HTML])
        ok, code = s.probe()
        self.assertFalse(ok)
        self.assertEqual(code, "portal_not_logged_in")
        self.assertIn(s.state, ("none", "expired"))

    def test_probe_pre_wrapped_json(self):
        # 浏览器把 application/json 包进 <pre>
        s, _ = self.mk(["<html><body><pre>%s</pre></body></html>"
                        % json.dumps(DROPS_OK)])
        ok, _ = s.probe()
        self.assertTrue(ok)
        self.assertEqual(s.state, "ready")

    def test_probe_keeps_expired_not_none_when_had_cache(self):
        s, _ = self.mk([json.dumps(DROPS_OK)])
        s.probe()
        s.browser.script.append(SHELL_HTML)   # 会话失效
        ok, code = s.probe()
        self.assertFalse(ok)
        self.assertEqual(s.state, "expired")

    def test_open_login_awaiting_then_cancel(self):
        s, b = self.mk()
        s.open_login()
        self.assertEqual(s.state, "awaiting_login")
        self.assertEqual(b.login_calls, 1)
        s.cancel_login()
        self.assertEqual(s.state, "none")

    def test_snapshot_shape_and_safety(self):
        s, _ = self.mk([json.dumps(DROPS_OK)])
        s.userid = "KU_TEST"
        s.probe()
        snap = s.snapshot(registry=[{"userid": "KU_TEST", "alias": "测试"}])
        for k in ("state", "userid", "matched", "account_alias", "available",
                  "week", "week_end_ms", "claimed", "max", "synced_at",
                  "error_code"):
            self.assertIn(k, snap)
        self.assertTrue(snap["matched"])
        self.assertEqual(snap["account_alias"], "测试")
        self.assertEqual(snap["claimed"], 8)
        self.assertEqual(snap["week"], 2965)
        # 安全:快照绝不含凭证类键
        for bad in ("cookie", "token", "session", "profile"):
            self.assertNotIn(bad, snap)

    def test_fetch_data_txns_via_browser(self):
        data = envelope([txn("TXN_DAILY_GIFT_ITEM", NOW - 5, [(1, "A_B")])])
        s, _ = self.mk(["<pre>%s</pre>" % json.dumps(data)])
        txns, extra = s.fetch_data_txns()
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["source"], "daily")

    def test_fetch_data_shell_raises(self):
        s, _ = self.mk([SHELL_HTML])
        with self.assertRaises(P.PortalParseError) as cm:
            s.fetch_data_txns()
        self.assertEqual(cm.exception.code, "portal_not_logged_in")


class TReconcile(unittest.TestCase):
    def test_match(self):
        self.assertEqual(P.portal_reconcile(8, 8), "match")
        self.assertEqual(P.portal_reconcile(0, 0), "match")

    def test_local_behind(self):
        self.assertEqual(P.portal_reconcile(6, 8), "local_behind")

    def test_window_tail(self):
        self.assertEqual(P.portal_reconcile(8, 2), "window_tail")


class TKernelDetect(Base):
    def test_injected_candidates(self):
        name, exe = PB.detect_kernel(candidates=[("edge", r"C:\x\msedge.exe")])
        self.assertEqual((name, exe), ("edge", r"C:\x\msedge.exe"))
        name, exe = PB.detect_kernel(candidates=[("edge", None), ("chrome", "x")])
        self.assertEqual(name, "chrome")
        name, exe = PB.detect_kernel(candidates=[("edge", None)])
        self.assertEqual(name, None)

    def test_fetch_without_exe_raises(self):
        # 真 PortalBrowser(非 Fake):exe 为空字符串(检测失败落定)→ bad_kernel
        b = PB.PortalBrowser(os.path.join(self.tmp, "prof2"), exe="", name="none")
        self.assertFalse(b.available)
        with self.assertRaises(PB.PortalKernelError) as cm:
            b.fetch_dump("http://x")
        self.assertEqual(cm.exception.code, "bad_kernel")

    def test_empty_dump_is_kernel_error(self):
        # 实测坑:rc=0 空 stdout = 委托失败,必须报 kernel_error 而非当成功
        b = PB.PortalBrowser(os.path.join(self.tmp, "prof3"),
                             exe=r"C:\fake\browser.exe", name="fake")
        import subprocess as _sp
        from unittest import mock

        def fake_run(cmd, capture_output, timeout):
            class CP:
                stdout = b"   "
                stderr = b""
                returncode = 0
            return CP()
        with mock.patch.object(_sp, "run", fake_run):
            with self.assertRaises(PB.PortalKernelError) as cm:
                b.fetch_dump("http://x")
        self.assertEqual(cm.exception.code, "kernel_error")


class FakePanelBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chester_papi_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old = (P.ACCOUNTS_DIR, P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL,
                     P.PORTAL, P.PORTAL_CACHE_PATH, P.PORTAL_PROFILE_DIR)
        P.ACCOUNTS_DIR = os.path.join(self.tmp, "accounts")
        os.makedirs(P.ACCOUNTS_DIR, exist_ok=True)
        P.SETTINGS_PATH = os.path.join(self.tmp, "settings.json")
        P.ACCOUNTS_PATH = os.path.join(self.tmp, "accounts.json")
        P.PORTAL_CACHE_PATH = os.path.join(self.tmp, "drops_cache.json")
        P.PORTAL_PROFILE_DIR = os.path.join(self.tmp, "profile")
        self.panel = P.Panel()
        P.PANEL = self.panel
        fake = FakeBrowser(["<pre>%s</pre>" % json.dumps(envelope([
                                txn("TXN_DAILY_GIFT_ITEM", NOW - 5,
                                    [(9, "A_B")])])),
                            json.dumps(DROPS_OK),          # onekey 尾部 probe
                            json.dumps(DROPS_OK)])         # session_status 兜底
        P.PORTAL = P.PortalSession(P.PORTAL_PROFILE_DIR, P.PORTAL_CACHE_PATH,
                                   browser=fake)
        with P.ACCOUNTS_LOCK:
            self.panel._registry = {"accounts": [
                {"userid": "KU_TEST", "alias": "T", "enabled": True}]}
        self._old_route = (P.PORT,)
        P.PORT = None
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        P.PORT = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        (P.PORT,) = self._old_route
        (P.ACCOUNTS_DIR, P.SETTINGS_PATH, P.ACCOUNTS_PATH, P.PANEL,
         P.PORTAL, P.PORTAL_CACHE_PATH, P.PORTAL_PROFILE_DIR) = self._old

    def _post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (P.PORT, path),
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class TApiPortal(FakePanelBase):
    def test_session_status_shape(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/portal/session_status" % P.PORT, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read())
        self.assertTrue(j["ok"])
        for k in ("state", "available", "claimed", "max", "week"):
            self.assertIn(k, j)
        for bad in ("cookie", "token", "session"):
            self.assertNotIn(bad, j)

    def test_onekey_import_happy(self):
        code, r = self._post("/api/portal/onekey_import", {"userid": "KU_TEST"})
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(r["gifts_backfilled"], 1)
        self.assertIn("portal", r)
        self.assertEqual(r["portal"]["week"], 2965)

    def test_onekey_bad_account(self):
        for bad in ("KU_NOPE", "KU_../x", "nope"):
            code, r = self._post("/api/portal/onekey_import", {"userid": bad})
            self.assertIn(code, (400, 404), bad)
            self.assertEqual(r["code"], "bad_account")

    def test_status_has_portal_block(self):
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/status" % P.PORT, timeout=10) as r:
            j = json.loads(r.read())
        self.assertIn("portal", j)
        self.assertIn("state", j["portal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
