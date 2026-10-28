# -*- coding: utf-8 -*-
"""_e2e_tray.py — 打包版托盘 exe 无头端到端冒烟(可复跑证据)。

验证:① PE 子系统=GUI(无黑框的结构性证明) ② 启动后 /api/status 应答
③ 托盘窗口存在 ④ 二次启动退出码 3(单实例) ⑤ /api/quit 干净退出码 0、端口释放、
窗口消失 ⑥ windowed 模式 stdout 已落 dist/_run.log。
"""
import ctypes
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "Chester.exe")
PORT = 8399
CLASS = "ChesterTrayWnd"

user32 = ctypes.WinDLL("user32")
user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
kernel32 = ctypes.WinDLL("kernel32")


def _tree_kill(pid):
    """PyInstaller onefile 是引导父+真实子双层结构,必须杀整棵树,否则留孤儿弹窗。"""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def find_tray():
    return user32.FindWindowW(CLASS, None)


def pe_subsystem(path):
    """读 PE 头 Subsystem 字段:2=GUI(无控制台),3=console。"""
    with open(path, "rb") as f:
        head = f.read(4096)
    off = int.from_bytes(head[0x3C:0x40], "little")
    sig = head[off:off + 4]
    assert sig == b"PE\x00\x00", "非 PE 文件"
    subsystem = int.from_bytes(head[off + 4 + 20 + 68:off + 4 + 20 + 70], "little")
    return subsystem


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait(cond, timeout, tag):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            print("PASS %s" % tag)
            return True
        time.sleep(0.2)
    print("FAIL %s" % tag)
    return False


def main():
    results = []
    print("== E2E: %s ==" % EXE)

    sub = pe_subsystem(EXE)
    print("PE Subsystem = %d (2=GUI,3=console)" % sub)
    results.append(("无黑框:PE 子系统=GUI", sub == 2))

    log_path = os.path.join(HERE, "dist", "_run.log")
    if os.path.isfile(log_path):
        os.remove(log_path)

    p = subprocess.Popen([EXE, "--no-browser", "--port", str(PORT)])
    alive = wait(lambda: http_ok("http://127.0.0.1:%d/api/status" % PORT),
                 15, "status 200")
    if not alive and p.poll() == 3:
        print("\nSKIP:已有 Chester 实例持有单实例互斥体(如生产在跑)。"
              "先 taskkill /IM Chester.exe 再跑本脚本。")
        sys.exit(0)
    results.append(("启动:15s 内 /api/status 应答", alive))
    results.append(("托盘:ChesterTrayWnd 窗口存在", wait(lambda: bool(find_tray()), 5, "tray wnd")))

    # 二次启动:互斥体路径 → 码 3。windowed 报错必须走 MessageBox(审查 I1 回归断言):
    # 探测标准对话框类 #32770 + 标题 Chester,出现后 WM_CLOSE 自动关掉,进程退出码 3
    # (新构建 exe 首跑要过 Defender 扫描+自解压,弹窗可能 >10s,窗口放宽到 30s)
    p2 = subprocess.Popen([EXE, "--no-browser", "--port", str(PORT)])
    dlg = wait(lambda: user32.FindWindowW("#32770", "Chester"), 30,
               "fatal MessageBox 出现")
    if dlg:
        user32.PostMessageW(dlg, 0x0010, 0, 0)   # WM_CLOSE → MessageBox 返回
    try:
        rc2 = p2.wait(timeout=15)
    except subprocess.TimeoutExpired:
        rc2 = None
        _tree_kill(p2.pid)
    print("second instance rc = %r" % rc2)
    results.append(("单实例:windowed 弹窗转交+退出码 3", rc2 == 3 and bool(dlg)))

    # AttachConsole 探测子进程有无控制台(需先脱离自身控制台)
    have_console = True
    try:
        kernel32.FreeConsole()
        ok = kernel32.AttachConsole(p.pid)
        have_console = bool(ok)
        if ok:
            kernel32.FreeConsole()
        kernel32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS,尽力恢复
    except Exception as e:
        print("AttachConsole 探测异常(忽略): %r" % e)
    print("child has console = %r" % have_console)
    results.append(("无黑框:子进程无控制台", not have_console))

    # /api/quit 干净退出
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/quit" % PORT, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=3) as r:
            print("api/quit resp %d" % r.status)
    except Exception as e:
        print("api/quit 请求异常: %r" % e)
    try:
        rc = p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _tree_kill(p.pid)
        rc = "TIMEOUT"
    print("quit rc = %r" % rc)
    results.append(("退出:/api/quit 后退出码 0", rc == 0))
    time.sleep(0.5)
    results.append(("退出:端口已释放", not http_ok(
        "http://127.0.0.1:%d/api/status" % PORT, 1)))
    results.append(("退出:托盘窗口已消失", not find_tray()))
    results.append(("日志:windowed stdout 落 _run.log",
                    os.path.isfile(log_path) and b"Chester start" in open(
                        log_path, "rb").read()))

    print("\n===== 结果 =====")
    allok = True
    for name, ok in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
        allok = allok and ok
    print("E2E %s" % ("ALL-PASS" if allok else "HAS-FAIL"))
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
