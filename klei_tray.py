# -*- coding: utf-8 -*-
"""klei_tray.py — Chester 系统托盘图标(纯 ctypes,零第三方依赖)。

设计要点(2026-10-28 实测定稿):
- 隐藏顶层窗口(不用 HWND_MESSAGE message-only:收不到 TaskbarCreated 广播,
  explorer 重启后图标无法复活);
- 窗口必须创建在跑消息泵的线程里(主线程建窗+子线程泵 → 消息石沉大海,实测复现);
- x64 下全部 API 必须显式 argtypes/restype,句柄按默认 c_int 转换会抛
  "int too long to convert"(实测复现);
- WNDPROC 闭包必须长持有实例引用,否则 GC 后回调进野指针直接崩;
- 弹菜单三件套:SetForegroundWindow → TrackPopupMenu(TPM_RETURNCMD) → PostMessage(WM_NULL),
  缺一则菜单点外部不消失/下次不弹(Win32 经典坑);
- 不调 NIM_SETVERSION,沿用 version-0 语义:lParam 即鼠标消息(WM_LBUTTONUP/WM_RBUTTONUP)。

对外接口:TrayController / create_tray()。本模块不 import 项目内任何其他模块,
状态经构造函数注入的回调获取,可独立单测。非 Windows / DLL 缺失 → create_tray() 返回 None。
"""
import ctypes
try:
    import ctypes.wintypes as w
except (ImportError, ValueError):         # 非 Windows:wintypes 可能不可导入
    w = None
import os
import sys
import threading
import time

# ---- 消息常量 ----
WM_NULL = 0x0000
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP_TRAY = 0x8001            # WM_APP+1:shell 发给本窗口的托盘回调消息号

# ---- Shell_NotifyIconW ----
NIM_ADD = 0x00
NIM_MODIFY = 0x01
NIM_DELETE = 0x02
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10                 # 气球通知(仅启动提示用)
NIIF_INFO = 0x01

# ---- 菜单 ----
MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_DISABLED = 0x0002
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
TPM_RETURNCMD = 0x0100

# ---- 图标 ----
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
SM_CYSMICON = 50
IDI_APPLICATION = 32512         # MakeIntResourceW:直接传整数值

# ---- 菜单命令 id(测试依赖,不许改值)----
IDM_STATUS = 0                  # 状态行(恒禁用,纯展示)
IDM_OPEN = 1001                 # 打开控制面板(默认项,左键同动作)
IDM_DATADIR = 1002              # 打开数据目录
IDM_LOG = 1003                  # 打开运行日志(无日志文件时灰)
IDM_AUTORUN = 1004              # 开机自启(仅打包版显示,勾选态)
IDM_QUIT = 1005                 # 退出

REOPEN_MS = 1500                # 左键打开面板节流:单击/双击连击只开一次
TIP_REFRESH_MS = 5000           # tooltip 轮询刷新周期
TIP_MAX = 127                   # szTip 128 wchar 含 NUL

LRESULT = ctypes.c_ssize_t

# ---- Win32 定义与 DLL 绑定(非 Windows / 缺 DLL → _OK=False,create_tray 返回 None)----
_OK = False
if sys.platform == "win32":
    try:
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", w.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE),
                ("hIcon", w.HICON),
                ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.UINT), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HICON),
                ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT),                       # union uTimeout
                ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", w.BYTE * 16),                  # wintypes 无 GUID,裸 16 字节
                ("hBalloonIcon", w.HICON),
            ]

        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _OK = True
    except (OSError, AttributeError):
        _OK = False

if _OK:
    _kernel32.GetModuleHandleW.restype = w.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
    _user32.RegisterClassW.restype = w.ATOM
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    _user32.UnregisterClassW.restype = w.BOOL
    _user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
    _user32.CreateWindowExW.restype = w.HWND
    _user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    _user32.DestroyWindow.restype = w.BOOL
    _user32.DestroyWindow.argtypes = [w.HWND]
    _user32.PostMessageW.restype = w.BOOL
    _user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    _user32.PostQuitMessage.restype = None
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.SetTimer.restype = ctypes.c_size_t
    _user32.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
    _user32.KillTimer.restype = w.BOOL
    _user32.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
    _user32.GetCursorPos.restype = w.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
    _user32.SetForegroundWindow.restype = w.BOOL
    _user32.SetForegroundWindow.argtypes = [w.HWND]
    _user32.LoadImageW.restype = w.HANDLE
    _user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT,
                                   ctypes.c_int, ctypes.c_int, w.UINT]
    _user32.LoadIconW.restype = w.HICON
    _user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPVOID]
    _user32.DestroyIcon.restype = w.BOOL
    _user32.DestroyIcon.argtypes = [w.HICON]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.RegisterWindowMessageW.restype = w.UINT
    _user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
    _user32.FindWindowW.restype = w.HWND
    _user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
    _user32.IsWindow.restype = w.BOOL
    _user32.IsWindow.argtypes = [w.HWND]
    _user32.CreatePopupMenu.restype = w.HMENU
    _user32.CreatePopupMenu.argtypes = []
    _user32.AppendMenuW.restype = w.BOOL
    _user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
    _user32.SetMenuDefaultItem.restype = w.BOOL
    _user32.SetMenuDefaultItem.argtypes = [w.HMENU, w.UINT, w.UINT]
    _user32.TrackPopupMenu.restype = w.BOOL   # TPM_RETURNCMD 时承载命令 id(值域 ≤0xFFFF 足够)
    _user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, w.HWND, ctypes.c_void_p]
    _user32.DestroyMenu.restype = w.BOOL
    _user32.DestroyMenu.argtypes = [w.HMENU]
    _user32.GetMessageW.restype = ctypes.c_int   # -1=错误,0=WM_QUIT,>0=消息
    _user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
    _user32.TranslateMessage.restype = w.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    _shell32.Shell_NotifyIconW.restype = w.BOOL
    _shell32.Shell_NotifyIconW.argtypes = [w.UINT, ctypes.c_void_p]
    try:
        _ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        _ole32.CoInitializeEx.restype = ctypes.c_long
        _ole32.CoInitializeEx.argtypes = [w.LPVOID, w.DWORD]
    except OSError:
        _ole32 = None

# 测试钩子:非 None 时记录每次 Shell_NotifyIcon 调用 (op, ret);生产为 None 零开销
NI_LOG = None


def _ni(op, ref):
    ret = _shell32.Shell_NotifyIconW(op, ref)
    if NI_LOG is not None:
        NI_LOG.append((op, int(ret)))
    return ret


def icon_path_candidates(frozen=None):
    """图标解析链(纯函数):frozen → _MEIPASS / exe 旁;源码 → 脚本目录。"""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return [os.path.join(getattr(sys, "_MEIPASS", "") or "", "icon.ico"),
                os.path.join(os.path.dirname(sys.executable), "icon.ico")]
    return [os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")]


def tooltip_text(version, running, accounts):
    """托盘 tooltip 模板(纯函数,WCHAR 计数 ≤127)。"""
    if running:
        t = "Chester 饥荒礼物箱 v%s · 挂机中(%d 账号)" % (version, accounts)
    else:
        t = "Chester 饥荒礼物箱 v%s · 已停止 · 单击图标打开面板" % version
    return t[:TIP_MAX]


class TrayController:
    """全部 Win32 活动都发生在 start() 起的 'klei-tray' 线程内。

    回调约定:state_line()/tooltip()/autorun_enabled() 在托盘线程被调,必须快进快出;
    on_open/on_datadir/on_log/on_autorun_toggle/on_quit 也在托盘线程被调,
    重活(如整体退出时序)由调用方在自己的线程模型里收敛。
    """

    CLASS_NAME = "ChesterTrayWnd"

    def __init__(self, url, on_open, on_quit, state_line, tooltip,
                 on_datadir, on_log=None, autorun_enabled=None,
                 on_autorun_toggle=None, frozen=None, show_start_balloon=False,
                 logger=None):
        if frozen is None:
            frozen = bool(getattr(sys, "frozen", False))
        self._url = url
        self._cb_open = on_open
        self._cb_quit = on_quit
        self._cb_state = state_line
        self._cb_tip = tooltip
        self._cb_datadir = on_datadir
        self._cb_log = on_log
        self._cb_autorun_on = autorun_enabled
        self._cb_autorun_toggle = on_autorun_toggle
        self._frozen = frozen
        self._balloon = show_start_balloon
        self._log = logger or (lambda m: None)
        self._thread = threading.Thread(target=self._run, name="klei-tray", daemon=True)
        self._ready = threading.Event()
        self._ok = False
        self._hwnd = None
        self._nid = None
        self._hicon = None
        self._hinst = None
        self._proc_ref = None               # 防 GC(坑:回调进野指针)
        self._last_open = 0.0
        self._last_tip = ""
        if _OK:
            self._msg_taskbar = _user32.RegisterWindowMessageW("TaskbarCreated")

    # ---- 生命周期 ----
    def start(self, timeout=3.0):
        """起托盘线程并等待 NIM_ADD 完成;返回是否成功(失败调用方降级无托盘)。"""
        if not _OK:
            return False
        try:  # DPI 感知:125%/150% 缩放下按真实像素挑图标帧;失败(已设/老系统)静默
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
        except OSError:
            pass
        except AttributeError:
            pass
        self._thread.start()
        self._ready.wait(timeout)
        return self._ok

    def request_quit(self):
        """任意线程可调:投 WM_CLOSE,托盘线程走销毁链(NIM_DELETE+PostQuitMessage)。"""
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def hard_delete_icon(self):
        """兜底:托盘线程卡在模态菜单时,跨线程直接删图标(同 (hwnd,uID) 键任意线程有效)。"""
        if not self._hwnd or not _OK:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self._hwnd
        nid.uID = 1
        _ni(NIM_DELETE, ctypes.byref(nid))

    def join(self, timeout):
        self._thread.join(timeout)

    def alive(self):
        return self._thread.is_alive()

    @property
    def hwnd(self):
        return self._hwnd

    # ---- 托盘线程体 ----
    def _run(self):
        if _ole32 is not None:
            try:  # ShellExecute 系(打开网页/目录)走 COM 激活时需要 STA,保险带
                _ole32.CoInitializeEx(None, 0x2)
            except OSError:
                pass
        self._hinst = _kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        self._proc_ref = WNDPROC(self._wndproc)
        wc.lpfnWndProc = self._proc_ref
        wc.hInstance = self._hinst
        wc.lpszClassName = self.CLASS_NAME
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            self._log("[tray] RegisterClassW 失败 err=%d" % ctypes.get_last_error())
            self._ready.set()
            return
        self._hwnd = _user32.CreateWindowExW(
            0, self.CLASS_NAME, "Chester Tray", 0,   # 隐藏顶层窗口,永不 Show
            0, 0, 0, 0, None, None, self._hinst, None)
        if not self._hwnd:
            self._log("[tray] CreateWindowExW 失败 err=%d" % ctypes.get_last_error())
            _user32.UnregisterClassW(self.CLASS_NAME, self._hinst)
            self._ready.set()
            return
        self._hicon = self._load_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._hicon
        self._write_tip(nid, self._safe_tip())
        if self._balloon:
            nid.uFlags |= NIF_INFO
            nid.szInfoTitle = "Chester 已在后台运行"
            nid.szInfo = "面板 %s · 右键托盘图标可退出" % self._url
            nid.dwInfoFlags = NIIF_INFO
        self._nid = nid
        if not _ni(NIM_ADD, ctypes.byref(nid)):
            # explorer 未起/通知区被策略禁:面板照常,仅无托盘
            self._log("[tray] NIM_ADD 失败(无 explorer?)err=%d" % ctypes.get_last_error())
            self._nid = None
            _user32.DestroyWindow(self._hwnd)
            _user32.UnregisterClassW(self.CLASS_NAME, self._hinst)
            self._ready.set()
            return
        _user32.SetTimer(self._hwnd, 1, TIP_REFRESH_MS, None)
        self._ok = True
        self._ready.set()
        msg = w.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        # 泵退出(WM_QUIT)后到这里,线程自然结束
        if self._hicon:
            _user32.DestroyIcon(self._hicon)
        _user32.UnregisterClassW(self.CLASS_NAME, self._hinst)

    # ---- WndProc:全包 try/except,异常绝不外泄(ctypes 回调异常=静默吞+行为未定义)----
    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            return self._wndproc_inner(hwnd, msg, wp, lp)
        except Exception as e:                       # noqa: BLE001 兜底层,必须最宽
            try:
                self._log("[tray] wndproc 异常: %r" % (e,))
            except Exception:
                pass
            return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _wndproc_inner(self, hwnd, msg, wp, lp):
        if msg == WM_APP_TRAY:                        # version-0:wp=uID,lp=鼠标消息
            if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._open_panel()
                return 0
            if lp in (WM_RBUTTONUP, WM_CONTEXTMENU):  # CONTEXTMENU 为防御分支
                self._popup_menu(hwnd)
                return 0
            return 0
        if self._nid is not None and msg == self._msg_taskbar:
            # explorer 重启:图标已死,先删后加才能成功(直接 ADD 会因已存在而失败)
            _ni(NIM_DELETE, ctypes.byref(self._nid))
            _ni(NIM_ADD, ctypes.byref(self._nid))
            return 0
        if msg == WM_TIMER:
            self._refresh_tip()
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == 0x0002:                             # WM_DESTROY:不留僵尸图标
            _user32.KillTimer(hwnd, 1)
            if self._nid is not None:
                _ni(NIM_DELETE, ctypes.byref(self._nid))
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    # ---- 行为 ----
    def _open_panel(self):
        now = time.time()
        if now - self._last_open < REOPEN_MS / 1000.0:   # 单击+双击连击只开一次
            return
        self._last_open = now
        try:
            self._cb_open()
        except Exception as e:
            self._log("[tray] 打开面板失败: %r" % (e,))

    def _safe_tip(self):
        try:
            return str(self._cb_tip())[:TIP_MAX]
        except Exception:
            return "Chester"

    def _write_tip(self, nid, text):
        nid.szTip = text[:TIP_MAX]

    def _refresh_tip(self):
        if self._nid is None:
            return
        tip = self._safe_tip()
        if tip == self._last_tip:
            return                                     # 无变化零系统调用
        self._last_tip = tip
        self._write_tip(self._nid, tip)
        self._nid.uFlags = NIF_TIP
        _ni(NIM_MODIFY, ctypes.byref(self._nid))
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    # ---- 菜单:每次右键现构建(状态永不陈旧),弹完即毁 ----
    def _popup_menu(self, hwnd):
        hmenu = _user32.CreatePopupMenu()
        try:
            self._build_menu(hmenu)
            pt = w.POINT(0, 0)
            _user32.GetCursorPos(ctypes.byref(pt))
            _user32.SetForegroundWindow(hwnd)          # 三件套之一:否则菜单点外部不消失
            cmd = self._popup(hmenu, pt.x, pt.y)
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)  # 三件套之二点五:否则下次偶发不弹
        finally:
            _user32.DestroyMenu(hmenu)
        self._dispatch(int(cmd) if cmd else 0)

    def _popup(self, hmenu, x, y):
        """可注入测试缝:真实现弹模态菜单并同步带回命令 id。"""
        return _user32.TrackPopupMenu(
            hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
            x, y, 0, self._hwnd, None)

    def _build_menu(self, hmenu):
        def append(flags, mid, label):
            _user32.AppendMenuW(hmenu, flags, mid, label)

        try:
            state = str(self._cb_state())
        except Exception:
            state = "Chester"
        append(MF_STRING | MF_GRAYED | MF_DISABLED, IDM_STATUS, state)
        append(MF_SEPARATOR, 0, None)
        append(MF_STRING, IDM_OPEN, "打开控制面板")
        _user32.SetMenuDefaultItem(hmenu, IDM_OPEN, 0)  # 默认项加粗
        append(MF_STRING, IDM_DATADIR, "打开数据目录")
        if self._cb_log:
            append(MF_STRING, IDM_LOG, "打开运行日志")
        else:
            append(MF_STRING | MF_GRAYED, IDM_LOG, "打开运行日志")
        if self._frozen and self._cb_autorun_on and self._cb_autorun_toggle:
            append(MF_SEPARATOR, 0, None)
            checked = MF_CHECKED if self._safe_autorun_on() else 0
            append(MF_STRING | checked, IDM_AUTORUN, "开机自启")
        append(MF_SEPARATOR, 0, None)
        append(MF_STRING, IDM_QUIT, "退出")

    def _safe_autorun_on(self):
        try:
            return bool(self._cb_autorun_on())
        except Exception:
            return False

    def _dispatch(self, cmd):
        if cmd == IDM_OPEN:
            self._open_panel_force()
        elif cmd == IDM_DATADIR:
            try:
                self._cb_datadir()
            except Exception as e:
                self._log("[tray] 打开数据目录失败: %r" % (e,))
        elif cmd == IDM_LOG and self._cb_log:
            try:
                self._cb_log()
            except Exception as e:
                self._log("[tray] 打开日志失败: %r" % (e,))
        elif cmd == IDM_AUTORUN and self._frozen and self._cb_autorun_toggle:
            try:
                self._cb_autorun_toggle()
            except Exception as e:
                self._log("[tray] 切换开机自启失败: %r" % (e,))
        elif cmd == IDM_QUIT:
            try:
                self._cb_quit()
            except Exception as e:
                self._log("[tray] 退出回调失败: %r" % (e,))
        # cmd==0(点菜单外取消)/未知 id:忽略

    def _open_panel_force(self):
        self._last_open = 0.0
        self._open_panel()

    def _load_icon(self):
        cx = _user32.GetSystemMetrics(SM_CXSMICON)
        cy = _user32.GetSystemMetrics(SM_CYSMICON)
        for p in icon_path_candidates(self._frozen):
            if p and os.path.isfile(p):
                h = _user32.LoadImageW(None, p, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
                if h:
                    return h
        return _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))


def create_tray(**kw):
    """工厂:非 Windows / 绑定失败 → None(绝不抛),调用方无感降级。"""
    if not _OK:
        return None
    try:
        return TrayController(**kw)
    except Exception:
        return None
