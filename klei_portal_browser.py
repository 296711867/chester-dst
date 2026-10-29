# -*- coding: utf-8 -*-
"""klei_portal_browser.py — Klei 门户会话浏览器(v6.0 一键对账)。

架构(2026-10-29 实测定稿):
- 内核 = 系统 Edge(Windows 10/11 系统组件)→ Chrome 兜底;零打包依赖,零包体增长。
- 登录面:headed 窗口(--user-data-dir 指向面板专用 profile),用户亲手登录,
  密码不经过面板进程;窗口进程由 Popen 直接持有。
- 数据面:同 profile headless `--dump-dom` 纯子进程调用,stdout 即页面;
  data.json/drops.json 落在 <pre> 包裹里,由调用方截取(与粘贴路径同一解析器)。
- 不开任何调试端口/CDP(无 9222 冲突面);cookie 由浏览器自身加密落盘,
  面板进程读不了也不读。

实测坑位(照抄规避):
- Edge 启动加速:关窗后进程可能仍存活 → 判"窗口关闭"不能只看进程。
  v1 用最保守策略:登录窗口打开后,由用户在前端点「我已登录,检测」驱动探测;
  探测(headless)若与 headed 窗口并发,Edge 会委托既有进程导致 stdout 空 —— 因此
  open_login 打开的窗口在探测前由调用方保证已让用户关闭(前端文案明确提示)。
- stdout 空且 rc=0 = 委托失败信号,必须显式判空,不能当成功。
"""
import os
import subprocess
import winreg

DATA_URL = "https://accounts.klei.com/account/transactions/data.json"
DROPS_URL = "https://accounts.klei.com/account/transactions/drops.json"
LOGIN_LANDING = "https://accounts.klei.com/account/transactions"

FLAGS_DUMP = ["--headless=new", "--disable-gpu", "--no-first-run",
              "--disable-extensions", "--disable-sync", "--no-default-browser-check",
              "--user-data-dir={PROF}", "--virtual-time-budget=20000", "--dump-dom"]
FLAGS_HEADED = ["--user-data-dir={PROF}", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions",
                "--window-size=1100,800"]

_FETCH_TIMEOUT_S = 45.0


def _reg_lookup(app_exe):
    """App Paths 注册表查浏览器全路径(HKLM+HKCU)。"""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion"
                                     r"\App Paths\%s" % app_exe) as k:
                v = winreg.QueryValue(k, None)
                if v and os.path.isfile(v):
                    return v
                if v and not os.path.isabs(v):
                    # 有些注册值是裸文件名,再查常见目录
                    for d in (os.path.join(os.environ.get("PROGRAMFILES(X86)", ""),
                                           r"Microsoft\Edge\Application"),
                              os.path.join(os.environ.get("PROGRAMFILES", ""),
                                           r"Google\Chrome\Application"),
                              os.path.join(os.environ.get("PROGRAMFILES(X86)", ""),
                                           r"Google\Chrome\Application")):
                        cand = os.path.join(d, v)
                        if os.path.isfile(cand):
                            return cand
        except OSError:
            continue
    return None


def detect_kernel(candidates=None):
    """返回 (name, exe_path) 或 (None, None)。检测顺序 Edge → Chrome。"""
    if candidates is not None:                     # 测试注入
        for name, exe in candidates:
            if exe:
                return name, exe
        return None, None
    for name, exe in (("edge", _reg_lookup("msedge.exe")),
                      ("chrome", _reg_lookup("chrome.exe"))):
        if exe:
            return name, exe
    return None, None


class PortalBrowser:
    """系统浏览器的面板专用 profile 封装。所有方法可注入测试(exe 路径/超时)。"""

    def __init__(self, profile_dir, exe=None, name=None):
        self.profile_dir = profile_dir
        if exe is None:
            self.name, exe = detect_kernel()
        else:
            self.name = name or "custom"
        self.exe = exe
        os.makedirs(profile_dir, exist_ok=True)

    @property
    def available(self):
        return bool(self.exe)

    def _flags(self, template_flags):
        return [f.replace("{PROF}", self.profile_dir) for f in template_flags]

    def fetch_dump(self, url, timeout_s=_FETCH_TIMEOUT_S):
        """headless 抓取页面 DOM(子进程,stdout);空输出 → PortalKernelError。"""
        if not self.exe:
            raise PortalKernelError("bad_kernel", "未找到可用的浏览器内核(Edge/Chrome)")
        cmd = [self.exe] + self._flags(FLAGS_DUMP) + [url]
        try:
            cp = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise PortalKernelError("portal_fetch_failed",
                                    "浏览器抓取超时(%ds)" % int(timeout_s))
        out = (cp.stdout or b"").decode("utf-8", "replace")
        if not out.strip():
            # rc=0 但空输出 = 委托给既有浏览器进程失败(实测坑),或页面异常
            raise PortalKernelError(
                "kernel_error",
                "浏览器无输出(可能有同名浏览器窗口开着,请关闭后重试)")
        return out

    def open_login_window(self, url=LOGIN_LANDING):
        """打开 headed 登录窗口(非阻塞);返回 Popen。调用方持有引用等用户关闭。"""
        if not self.exe:
            raise PortalKernelError("bad_kernel", "未找到可用的浏览器内核(Edge/Chrome)")
        cmd = [self.exe] + self._flags(FLAGS_HEADED) + [url]
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)


class PortalKernelError(RuntimeError):
    """携带 .code 的内核错误,路由层转译。"""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail
