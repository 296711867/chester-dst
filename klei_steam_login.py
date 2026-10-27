#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
klei_steam_login.py — 不开游戏登录 Klei（模拟 DST 客户端 ① 账号登录流程）

原理（来自 dontstarve_steam_x64.exe C 层逆向）:
  1. SteamUser()->GetAuthSessionTicket(buf, 0x800, &len)        得到 Steam 会话票据
  2. 票据 -> 每字节大写 HEX（游戏内 0x140294400 用 "%02X" 逐个字节转）
  3. POST https://login.kleientertainment.com/login/LoginViaSteam
     body: {"SteamTicket":<HEX>, "Game":"Don't Starve Together",
            "FriendlyHostDescriptor":<desc>, "NoEmail":true}
  4. 响应 JSON 取 Token(GameSessionToken) / UserID(KU_/OU_) / Username / AnalyticsJWT

前置条件:
  - Steam 客户端已运行并登录（不必开 DST）
  - 本脚本放在 dontstarve_steam_x64.exe 所在 bin64 目录（沿用其 steam_api64.dll），
    或通过 --dll 指定 steam_api64.dll 路径
  - appid 322330 通过 steam_appid.txt 声明（脚本自动写、结束删除）

用法:
  python klei_steam_login.py            # 登录并打印凭证
  python klei_steam_login.py --json out  # 把响应原样存到 out.json
  python klei_steam_login.py --desc "mypc"   # 自定义 FriendlyHostDescriptor
"""

import argparse
import ctypes
import ctypes.wintypes as w
import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.error

APPID = "322330"
BASE = "https://login.kleientertainment.com"
ENDPOINT = BASE + "/login/LoginViaSteam"
GAME = "DontStarveTogether"   # 游戏代码（Klei 服务端识别用，见 /login/auto/?Game=）
# 游戏内 HTTP UA 前缀：DontStarveTogether/<build>
UA = "DontStarveTogether/747465"
TICKET_SIZE = 0x800

# ---------------------------------------------------------------- steam_api 调用
def find_steam_dll():
    """按 脚本目录 → 上级 → 上级/dst 顺序查找 steam_api64.dll;
    打包(PyInstaller)模式下额外尝试 exe 所在目录及其 dst 子目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [here, os.path.dirname(here), os.path.join(os.path.dirname(here), "dst")]
    if getattr(sys, "frozen", False):
        exedir = os.path.dirname(sys.executable)
        cands = [exedir, os.path.join(exedir, "dst")] + cands
    for d in cands:
        p = os.path.join(d, "steam_api64.dll")
        if os.path.exists(p):
            return p
    return os.path.join(cands[0], "steam_api64.dll")


class SteamAPIBridge:
    """ctypes 调用 steam_api64.dll 的 flat API 拿到 Steam 会话票据。"""

    def __init__(self, dll_path=None):
        if dll_path is None:
            dll_path = find_steam_dll()
        if not os.path.exists(dll_path):
            raise FileNotFoundError("找不到 steam_api64.dll: %s" % dll_path)
        self.dll = ctypes.WinDLL(dll_path)
        self.pipe_ok = False
        self.user = None
        self.hTicket = 0
        self._resolve()

    def _resolve(self):
        d = self.dll
        # ---- 初始化类型签名 ----
        d.SteamAPI_RestartAppIfNecessary.restype = w.BOOL
        d.SteamAPI_RestartAppIfNecessary.argtypes = [w.UINT]
        d.SteamAPI_Init.restype = w.BOOL
        d.SteamAPI_Init.argtypes = []
        try:
            d.SteamAPI_InitEx.argtypes = [ctypes.POINTER(ctypes.c_int)]
        except AttributeError:
            pass
        d.SteamAPI_Shutdown.argtypes = []
        getuser = getattr(d, "SteamAPI_SteamUser_v021", getattr(d, "SteamAPI_SteamUser", None))
        if getuser is None:
            raise RuntimeError("DLL 中没有 SteamAPI_SteamUser 导出")
        getuser.restype = ctypes.c_void_p
        getuser.argtypes = []
        self._getuser = getuser
        # ISteamUser::GetSteamID -> uint64（打印用）
        if hasattr(d, "SteamAPI_ISteamUser_GetSteamID"):
            d.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
            d.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
            self._getsteamid = d.SteamAPI_ISteamUser_GetSteamID
        else:
            self._getsteamid = None
        d.SteamAPI_ISteamUser_GetAuthSessionTicket.restype = w.UINT
        # SDK>=1.56 的平铺包装多一个 identity 指针参数;旧版 DLL 忽略多传的第 5 参。
        # identity 必须为 None:Klei 走 ISteamUserAuth/AuthenticateUserTicket 验票,
        # 绑定 identity 的票据会被 Steam 拒绝(SDK 头文件注释明示)。
        d.SteamAPI_ISteamUser_GetAuthSessionTicket.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(w.UINT), ctypes.c_void_p]
        if hasattr(d, "SteamAPI_ISteamUser_CancelAuthTicket"):
            d.SteamAPI_ISteamUser_CancelAuthTicket.argtypes = [ctypes.c_void_p, w.UINT]

    # -- 需要一个能说明"我是 322330"的 steam_appid.txt；Steam 以此识别 appid --
    def _ensure_appid(self, workdir):
        p = os.path.join(workdir, "steam_appid.txt")
        if os.path.exists(p):
            with open(p) as f:
                if f.read().strip().isdigit():
                    return None  # 已有合法 appid，不动它
        with open(p, "w") as f:
            f.write(APPID)
        return p

    def get_ticket(self):
        """初始化 steamapi 并请求会话票据；返回 (hex_ticket, steamid_str)。"""
        # 关键：Steam 按当前工作目录下的 steam_appid.txt 认定 appid
        old = os.getcwd()
        tmpappid = None
        try:
            os.chdir(os.path.dirname(os.path.abspath(__file__)))
            tmpappid = self._ensure_appid(os.getcwd())
            if self.dll.SteamAPI_RestartAppIfNecessary(int(APPID)):
                raise RuntimeError("需要先以 appid=%s 通过 Steam 启动进程" % APPID)
            if not self.dll.SteamAPI_Init():
                raise RuntimeError("SteamAPI_Init 失败：Steam 客户端未运行或未登录？")
            self.pipe_ok = True
            self.user = self._getuser()
            if not self.user:
                raise RuntimeError("SteamUser() 返回空")

            buf = ctypes.create_string_buffer(TICKET_SIZE)
            nlen = w.UINT(0)
            # Steam user 上下文偶发未就绪时直接返回 handle=0，重试即可恢复
            for _ in range(5):
                nlen = w.UINT(0)
                self.hTicket = self.dll.SteamAPI_ISteamUser_GetAuthSessionTicket(
                    self.user, buf, TICKET_SIZE, ctypes.byref(nlen), None)
                if self.hTicket and nlen.value:
                    break
                time.sleep(1.5)
            if self.hTicket == 0 or nlen.value == 0:
                raise RuntimeError("GetAuthSessionTicket 失败 (handle=%#x len=%d)"
                                   % (self.hTicket, nlen.value))
            raw = bytes(buf.raw[:nlen.value])
            steamid = None
            if self._getsteamid:
                steamid = self._getsteamid(self.user)
            return raw.hex().upper(), steamid
        finally:
            if tmpappid and os.path.exists(tmpappid):
                try:
                    os.remove(tmpappid)
                except OSError:
                    pass
            os.chdir(old)

    def cancel_ticket(self):
        try:
            if self.pipe_ok and self.user and self.hTicket and \
                    hasattr(self.dll, "SteamAPI_ISteamUser_CancelAuthTicket"):
                self.dll.SteamAPI_ISteamUser_CancelAuthTicket(self.user, self.hTicket)
        except Exception:
            pass

    def shutdown(self):
        try:
            if self.pipe_ok and hasattr(self.dll, "SteamAPI_Shutdown"):
                self.dll.SteamAPI_Shutdown()
        except Exception:
            pass

# ---------------------------------------------------------------- 续期
def refresh_token(token, timeout=30):
    """用现有 GameSessionToken 调 /login/TokenPurpose 换取新的（游戏加入服务器前的
    写法：type 6，body {"Token": <当前token>}）。实测成功，每次可再续 60 分钟。"""
    body = {"Token": token}
    req = urllib.request.Request(
        BASE + "/login/TokenPurpose", data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": UA})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", errors="replace")
    return status, raw

# ---------------------------------------------------------------- HTTP
def login(ticket_hex, desc, token_expiry=None):
    body = {
        "SteamTicket": ticket_hex,
        "Game": GAME,
        "FriendlyHostDescriptor": desc,
        "NoEmail": True,
    }
    if token_expiry is not None:   # 游戏内该字段为 0 时整键省略
        body["TokenExpiry"] = token_expiry
    data = json.dumps(body).encode("utf-8")
    # 与游戏一致：仅标准头（Content-Type/Accept/UA）
    req = urllib.request.Request(
        ENDPOINT, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        })
    ctx = ssl.create_default_context()  # 系统 CA 链，与游戏一致（无证书固定）
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", errors="replace")
    return status, raw

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="不开游戏登录 Klei（模拟 DST Steam 登录）")
    ap.add_argument("--dll", default=None, help="steam_api64.dll 路径（默认取脚本同目录）")
    ap.add_argument("--desc", default="standalone_python_login", help="FriendlyHostDescriptor")
    ap.add_argument("--json", metavar="OUT", default=None, help="把 HTTP 响应原样保存到文件")
    ap.add_argument("--tokenexpiry", type=int, default=None,
                    help="带上 TokenExpiry=<秒数> 字段（游戏默认省略）")
    ap.add_argument("--refresh", metavar="TOKEN", default=None,
                    help="不重新登录，直接用现有 GameSessionToken 走 /login/TokenPurpose 续期")
    args = ap.parse_args()

    if args.refresh:
        print("[*] 续期模式：TokenPurpose with %s..." % args.refresh[:24])
        st, raw = refresh_token(args.refresh)
        print("[*] HTTP %d" % st, raw[:300])
        return

    print("[*] 1/3 请求 Steam 会话票据 ...")
    bridge = SteamAPIBridge(args.dll)
    try:
        ticket_hex, steamid = bridge.get_ticket()
        print("[*]    票据长度: %d 字节 (hex %d 字符)  SteamID: %s"
              % (len(ticket_hex) // 2, len(ticket_hex), steamid or "?"))

        print("[*] 2/3 POST %s" % ENDPOINT)
        status, raw = login(ticket_hex, args.desc, args.tokenexpiry)

        print("[*] 3/3 HTTP %d" % status)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                f.write(raw)
            print("[*]    响应原文 -> %s" % args.json)

        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            j = None

        if j is not None:
            tok = j.get("Token") or j.get("token")
            uid = j.get("UserID") or j.get("userid")
            print("=" * 60)
            print("登录成功！Klei 帐号凭证：")
            if uid:    print("  UserID        : %s" % uid)
            if tok:    print("  GameSessionToken: %s" % tok)
            if j.get("Username"):  print("  Username      : %s" % j["Username"])
            if j.get("AnalyticsJWT"): print("  AnalyticsJWT  : %s" % j["AnalyticsJWT"])
            if j.get("ErrorCode") is not None:
                print("  接口返回错误: code=%s  %s" % (j["ErrorCode"], j.get("ErrorMessage", "")))
            print("=" * 60)
            if tok:
                print("[*] 该 Token 即 GameSessionToken；加入服务器时主机拿它 POST")
                print("    /login/GameSessionToken 校验（与游戏内行为一致）。")
        else:
            print("[!] 非 JSON 响应（HTTP %d）：" % status)
            print(raw[:2000])
    finally:
        bridge.cancel_ticket()
        bridge.shutdown()

if __name__ == "__main__":
    main()