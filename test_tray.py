# -*- coding: utf-8 -*-
"""test_tray.py — 托盘化改造测试。

覆盖:纯逻辑(tooltip/节流/命令分发)、真实 Win32 生命周期(消息注入,
不弹真菜单、不真开浏览器)、面板集成(退出时序/stdio 兜底/windowed 报错分支/
单实例/端口分流/api/quit)。
"""
import ctypes
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import klei_tray as T
import klei_panel as P


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def _mk_controller(**kw):
    rec = {"open": 0, "quit": 0, "datadir": 0, "autorun": 0}
    ctrl = T.TrayController(
        url="http://127.0.0.1:8377",
        on_open=lambda: rec.__setitem__("open", rec["open"] + 1),
        on_quit=lambda: rec.__setitem__("quit", rec["quit"] + 1),
        state_line=kw.get("state_line", lambda: "状态行"),
        tooltip=kw.get("tooltip", lambda: "tip"),
        on_datadir=lambda: rec.__setitem__("datadir", rec["datadir"] + 1),
        on_log=kw.get("on_log"),
        autorun_enabled=kw.get("autorun_enabled"),
        on_autorun_toggle=lambda: rec.__setitem__("autorun", rec["autorun"] + 1),
        frozen=kw.get("frozen", False),
        logger=kw.get("logger", lambda m: None),
    )
    return ctrl, rec


class TestPure(unittest.TestCase):
    def test_tooltip_running_stopped(self):
        self.assertIn("挂机中(3 账号)", T.tooltip_text("1.1", True, 3))
        self.assertIn("已停止", T.tooltip_text("1.1", False, 0))

    def test_tooltip_truncate_127(self):
        t = T.tooltip_text("v" * 300, True, 999)
        self.assertLessEqual(len(t), T.TIP_MAX)

    def test_icon_path_candidates_frozen(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "_MEIPASS", "M", create=True), \
             mock.patch.object(sys, "executable",
                               os.path.join("X", "dir", "Chester.exe")):
            cands = T.icon_path_candidates()
        self.assertEqual(cands[0], os.path.join("M", "icon.ico"))
        self.assertEqual(cands[1], os.path.join("X", "dir", "icon.ico"))

    def test_icon_path_candidates_source(self):
        cands = T.icon_path_candidates(frozen=False)
        self.assertEqual(cands, [os.path.join(
            os.path.dirname(os.path.abspath(T.__file__)), "icon.ico")])

    def test_left_click_throttle(self):
        ctrl, rec = _mk_controller()
        ctrl._open_panel()
        ctrl._open_panel()          # 1500ms 内连击 → 只开一次
        self.assertEqual(rec["open"], 1)
        ctrl._last_open = 0.0       # 节流窗口过后再点 → 再开
        ctrl._open_panel()
        self.assertEqual(rec["open"], 2)

    def test_dispatch_table(self):
        ctrl, rec = _mk_controller(frozen=True)
        ctrl._dispatch(T.IDM_OPEN)
        self.assertEqual(rec["open"], 1)
        ctrl._dispatch(T.IDM_DATADIR)
        self.assertEqual(rec["datadir"], 1)
        ctrl._dispatch(T.IDM_QUIT)
        self.assertEqual(rec["quit"], 1)
        ctrl._dispatch(T.IDM_AUTORUN)
        self.assertEqual(rec["autorun"], 1)
        ctrl._dispatch(9999)        # 未知 id:不抛不动作
        ctrl._dispatch(T.IDM_LOG)   # 无 on_log:静默忽略
        ctrl._dispatch(0)           # 点菜单外取消:忽略
        self.assertEqual((rec["open"], rec["quit"], rec["datadir"]), (1, 1, 1))

    def test_dispatch_autorun_hidden_when_not_frozen(self):
        ctrl, rec = _mk_controller(frozen=False)
        ctrl._dispatch(T.IDM_AUTORUN)
        self.assertEqual(rec["autorun"], 0)

    def test_open_panel_exception_never_raises(self):
        def boom():
            raise RuntimeError("x")
        ctrl = T.TrayController(
            url="u", on_open=boom, on_quit=lambda: None,
            state_line=lambda: "s", tooltip=lambda: "t",
            on_datadir=lambda: None, logger=lambda m: None)
        ctrl._open_panel()          # 异常被吞,只记日志


@unittest.skipUnless(T._OK, "需要 Windows")
class TestWin32Lifecycle(unittest.TestCase):
    def setUp(self):
        T.NI_LOG = []

    def tearDown(self):
        T.NI_LOG = None

    def _start(self, tip_box=None, popup_cmd=None):
        if tip_box is None:
            tip_box = ["Chester tip"]
        ctrl, rec = _mk_controller(tooltip=lambda: tip_box[0])
        ctrl._tip_box = tip_box
        if popup_cmd is not None:
            ctrl._popup = lambda hmenu, x, y: popup_cmd   # 测试缝:不弹真菜单
        self.assertTrue(ctrl.start(3.0))
        self.addCleanup(self._stop, ctrl)
        return ctrl, rec

    def _stop(self, ctrl):
        ctrl.request_quit()
        ctrl.join(2.0)

    def test_add_click_menu_quit_clean(self):
        ctrl, rec = self._start(popup_cmd=T.IDM_QUIT)
        self.assertTrue(_wait(lambda: ctrl.hwnd), "托盘窗口应存在")
        hwnd = ctrl.hwnd                          # 用自身 hwnd,避免按类名搜到生产实例
        self.assertTrue(T._user32.IsWindow(hwnd))
        self.assertIn((T.NIM_ADD, 1), T.NI_LOG)

        # 左键 ×2(节流)→ on_open 恰好 1 次
        T._user32.PostMessageW(hwnd, T.WM_APP_TRAY, 1, T.WM_LBUTTONUP)
        T._user32.PostMessageW(hwnd, T.WM_APP_TRAY, 1, T.WM_LBUTTONUP)
        self.assertTrue(_wait(lambda: rec["open"] >= 1), "左键应触发打开")
        time.sleep(0.25)
        self.assertEqual(rec["open"], 1)

        # tooltip 变化 + WM_TIMER → NIM_MODIFY
        ctrl._tip_box[0] = "状态变了"
        T._user32.PostMessageW(hwnd, T.WM_TIMER, 1, 0)
        self.assertTrue(_wait(lambda: (T.NIM_MODIFY, 1) in T.NI_LOG),
                        "tooltip 变化应触发 NIM_MODIFY")

        # explorer 重启重挂:TaskbarCreated → 再来一次 NIM_ADD
        n_add = sum(1 for op, _ in T.NI_LOG if op == T.NIM_ADD)
        T._user32.PostMessageW(hwnd, ctrl._msg_taskbar, 0, 0)
        self.assertTrue(_wait(lambda: sum(1 for op, _ in T.NI_LOG
                                          if op == T.NIM_ADD) >= n_add + 1),
                        "TaskbarCreated 应重挂图标")

        # 右键 → _popup(测试缝)返回 IDM_QUIT → on_quit
        T._user32.PostMessageW(hwnd, T.WM_APP_TRAY, 1, T.WM_RBUTTONUP)
        self.assertTrue(_wait(lambda: rec["quit"] >= 1), "右键菜单退出应触发 on_quit")

        # 干净退出:WM_CLOSE → 线程死 + NIM_DELETE + 窗口消失
        T._user32.PostMessageW(hwnd, T.WM_CLOSE, 0, 0)
        ctrl.join(3.0)
        self.assertFalse(ctrl.alive())
        self.assertEqual(T.NI_LOG[-1], (T.NIM_DELETE, 1))
        self.assertFalse(T._user32.IsWindow(hwnd), "退出后不应残留窗口")

    def test_hard_delete_icon(self):
        ctrl, _ = self._start()
        ctrl.hard_delete_icon()     # 跨线程兜底删图标
        self.assertTrue(_wait(lambda: (T.NIM_DELETE, 1) in T.NI_LOG))

    def test_menu_open_command(self):
        # 右键菜单选「打开控制面板」路径(_popup 缝返回 IDM_OPEN)
        ctrl, rec = self._start(popup_cmd=T.IDM_OPEN)
        self.assertTrue(_wait(lambda: ctrl.hwnd))
        T._user32.PostMessageW(ctrl.hwnd, T.WM_APP_TRAY, 1, T.WM_RBUTTONUP)
        self.assertTrue(_wait(lambda: rec["open"] >= 1))


class TestPanelHelpers(unittest.TestCase):
    def test_state_line_variants(self):
        with mock.patch.object(P.PANEL, "snapshot",
                               return_value={"running": True,
                                             "accounts_enabled": 2,
                                             "interval": 360}):
            self.assertIn("2 账号", P._tray_state_line())
            self.assertIn("360s", P._tray_state_line())
        with mock.patch.object(P.PANEL, "snapshot",
                               return_value={"running": True, "accounts_enabled": 0}):
            self.assertIn("空转", P._tray_state_line())
        with mock.patch.object(P.PANEL, "snapshot",
                               return_value={"running": False}):
            self.assertIn("已停止", P._tray_state_line())

    def test_tooltip_from_snapshot(self):
        with mock.patch.object(P.PANEL, "snapshot",
                               return_value={"running": True,
                                             "accounts_enabled": 2,
                                             "version": "1.1"}):
            self.assertIn("挂机中(2 账号)", P._tray_tooltip())

    def test_shutdown_sequence_order(self):
        order = []

        class FakeSrv:
            def shutdown(self):
                order.append("shutdown")

            def server_close(self):
                order.append("close")

        class FakeTray:
            def request_quit(self):
                order.append("tray_quit")

            def join(self, timeout):
                order.append("tray_join")

            def alive(self):
                return False

            def hard_delete_icon(self):
                order.append("hard_delete")

        with mock.patch.object(P.PANEL, "stop", new=lambda: (True, "")):
            P._shutdown_sequence(FakeSrv(), FakeTray())
        self.assertEqual(order, ["shutdown", "close", "tray_quit", "tray_join"])

    def test_shutdown_sequence_tray_stuck_fallback(self):
        # 托盘线程卡在模态菜单:join 超时 + alive → hard_delete_icon 兜底
        order = []

        class FakeSrv:
            def shutdown(self):
                order.append("shutdown")

            def server_close(self):
                order.append("close")

        class FakeTray:
            def request_quit(self):
                order.append("tray_quit")

            def join(self, timeout):
                order.append("tray_join")

            def alive(self):
                return True

            def hard_delete_icon(self):
                order.append("hard_delete")

        with mock.patch.object(P.PANEL, "stop", new=lambda: (True, "")):
            P._shutdown_sequence(FakeSrv(), FakeTray())
        self.assertEqual(order, ["shutdown", "close",
                                 "tray_quit", "tray_join", "hard_delete"])

    def test_shutdown_sequence_no_tray(self):
        order = []

        class FakeSrv:
            def shutdown(self):
                order.append("shutdown")

            def server_close(self):
                order.append("close")

        with mock.patch.object(P.PANEL, "stop", new=lambda: (True, "")):
            P._shutdown_sequence(FakeSrv(), None)
        self.assertEqual(order, ["shutdown", "close"])

    def test_ensure_stdio_passthrough(self):
        # 控制台模式:stdout 存在 → 直通不改,WINDOWED 标志不置
        saved = P.WINDOWED
        P.WINDOWED = False
        try:
            self.assertIsNone(P._ensure_stdio())
            self.assertFalse(P.WINDOWED)
        finally:
            P.WINDOWED = saved

    def test_ensure_stdio_redirect(self):
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            with tempfile.TemporaryDirectory() as td:
                with mock.patch.object(P, "HERE", td):
                    sys.stdout = None
                    sys.stderr = None
                    path = P._ensure_stdio()
                    self.assertEqual(path, os.path.join(td, "_run.log"))
                    self.assertIsNotNone(sys.stdout)
                    self.assertTrue(os.path.isfile(path))
                    with open(path, "r", encoding="utf-8") as f:
                        self.assertIn("Chester start", f.read())
                    fp1 = sys.stdout.fp
                    fp1.close()         # 关旧句柄再模拟下一次 windowed 启动

                    with open(path, "wb") as f:
                        f.write(b"x" * (2 * 1024 * 1024 + 16))
                    sys.stdout = None
                    sys.stderr = None
                    path2 = P._ensure_stdio()
                    self.assertEqual(path2, path)
                    self.assertLess(os.path.getsize(path2), 1024)   # 2MB 上限重开
                    self.assertTrue(P.WINDOWED)   # 重定向发生过 → windowed 标志
                    try:
                        sys.stdout.fp.close()
                    except OSError:
                        pass
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
            P.WINDOWED = False

    def test_fatal_windowed_uses_messagebox(self):
        # windowed(重定向后 stdout 非 None)时 _fatal 必须走 MessageBox 而非 print
        import ctypes
        saved = P.WINDOWED
        P.WINDOWED = True
        try:
            with mock.patch.object(ctypes.windll.user32, "MessageBoxW") as mb:
                with self.assertRaises(SystemExit) as cm:
                    P._fatal("boom", 2, P.MB_ICONERROR)
                self.assertEqual(cm.exception.code, 2)
                mb.assert_called_once()
        finally:
            P.WINDOWED = saved

    def test_fatal_console_prints(self):
        saved = P.WINDOWED
        P.WINDOWED = False
        try:
            with self.assertRaises(SystemExit) as cm:
                P._fatal("boom", 2, P.MB_ICONERROR)
            self.assertEqual(cm.exception.code, 2)
        finally:
            P.WINDOWED = saved

    def test_single_instance_mutex(self):
        # 用独立互斥体名,与生产实例(Chester.Singleton)隔离,可随时重跑
        with mock.patch.object(P, "MUTEX_NAME", "ChesterTrayTest.Singleton"):
            # 第一次:创建互斥体,放行
            P._single_instance_check()
            self.assertIsNotNone(P._MUTEX)
            # 第二次:同进程同名 → ERROR_ALREADY_EXISTS → 转交并退出(码 3)
            with mock.patch("webbrowser.open") as mo:
                with self.assertRaises(SystemExit) as cm:
                    P._single_instance_check()
                self.assertEqual(cm.exception.code, 3)
                mo.assert_called_once()
        # 清理:关句柄并复位,不影响后续子进程
        try:
            ctypes.windll.kernel32.CloseHandle(P._MUTEX)
        finally:
            P._MUTEX = None

    def test_bind_conflict_foreign_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            with self.assertRaises(SystemExit) as cm:
                P._bind_server(port)
            self.assertEqual(cm.exception.code, 2)
        finally:
            s.close()

    def test_bind_conflict_own_panel(self):
        saved_port = P.PORT
        srv = P.PanelServer(("127.0.0.1", 0), P.Handler)
        port = srv.server_address[1]
        P.PORT = port     # _host_ok 校验全局 PORT,对齐服务器真实端口(生产路径天然一致)
        t = threading.Thread(target=srv.serve_forever,
                             kwargs={"poll_interval": 0.2}, daemon=True)
        t.start()
        try:
            with mock.patch("webbrowser.open") as mo:
                with self.assertRaises(SystemExit) as cm:
                    P._bind_server(port)
                self.assertEqual(cm.exception.code, 3)
                mo.assert_called_once()
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(2.0)
            P.PORT = saved_port

    def test_api_quit_route(self):
        import urllib.request
        saved_port = P.PORT
        srv = P.PanelServer(("127.0.0.1", 0), P.Handler)
        port = srv.server_address[1]
        P.PORT = port     # _host_ok 校验全局 PORT,对齐服务器真实端口
        flag = threading.Event()
        srv.stop_flag = flag
        t = threading.Thread(target=srv.serve_forever,
                             kwargs={"poll_interval": 0.2}, daemon=True)
        t.start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/quit" % port, method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=3) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(b'"ok"', r.read())
            self.assertTrue(_wait(flag.is_set, 2.0), "api/quit 应置停止事件")
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(2.0)
            P.PORT = saved_port

    def test_no_tray_flag(self):
        args = mock.Mock(no_tray=True)
        self.assertIsNone(P._start_tray(args, "http://x", threading.Event(), None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
