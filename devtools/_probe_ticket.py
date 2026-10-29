#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断探针:为什么 GetAuthSessionTicket 返回 0。只打印状态,票据写 credentials/。"""
import ctypes
import os
import sys
import time
from ctypes import wintypes as w

import klei_steam_login as _K

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = _K.find_steam_dll()   # 与主脚本同一查找顺序:脚本目录→上级→上级/dst
APPID = "322330"

os.chdir(HERE)
appid_path = os.path.join(HERE, "steam_appid.txt")
existed = os.path.exists(appid_path)
with open(appid_path, "w") as f:
    f.write(APPID)

d = ctypes.WinDLL(DLL)
try:
    # 优先新式 SteamAPI_InitEx;失败退回旧 SteamAPI_Init
    init_ok = False
    if hasattr(d, "SteamAPI_InitEx"):
        d.SteamAPI_InitEx.restype = ctypes.c_int
        d.SteamAPI_InitEx.argtypes = [ctypes.c_char_p]
        msg = ctypes.create_string_buffer(1024)
        rc = d.SteamAPI_InitEx(msg)
        print("[probe] SteamAPI_InitEx rc=%r msg=%r" % (rc, msg.value))
        init_ok = (rc == 1)
    if not init_ok:
        d.SteamAPI_Init.restype = w.BOOL
        ok = bool(d.SteamAPI_Init())
        print("[probe] SteamAPI_Init -> %s" % ok)
        init_ok = ok
    if not init_ok:
        sys.exit("[probe] 初始化失败")

    getuser = getattr(d, "SteamAPI_SteamUser_v021", None) or getattr(d, "SteamAPI_SteamUser")
    getuser.restype = ctypes.c_void_p
    getuser.argtypes = []
    user = getuser()
    print("[probe] SteamUser=%#x" % (user or 0))
    if not user:
        sys.exit("[probe] SteamUser 为空")

    if hasattr(d, "SteamAPI_ISteamUser_BLoggedOn"):
        d.SteamAPI_ISteamUser_BLoggedOn.restype = w.BOOL
        d.SteamAPI_ISteamUser_BLoggedOn.argtypes = [ctypes.c_void_p]
        print("[probe] BLoggedOn=%s" % bool(d.SteamAPI_ISteamUser_BLoggedOn(user)))

    d.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
    d.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
    print("[probe] SteamID=%d" % d.SteamAPI_ISteamUser_GetSteamID(user))

    d.SteamAPI_ISteamUser_GetAuthSessionTicket.restype = w.UINT
    # 5 参 + identity=None:通吃新旧 SDK(Klei 走 AuthenticateUserTicket,identity 必须为 NULL)
    d.SteamAPI_ISteamUser_GetAuthSessionTicket.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
        ctypes.POINTER(w.UINT), ctypes.c_void_p]
    if hasattr(d, "SteamAPI_ISteamUser_CancelAuthTicket"):
        d.SteamAPI_ISteamUser_CancelAuthTicket.argtypes = [ctypes.c_void_p, w.UINT]
    if hasattr(d, "SteamAPI_RunCallbacks"):
        d.SteamAPI_RunCallbacks.restype = None
        d.SteamAPI_RunCallbacks.argtypes = []

    for i in range(8):
        buf = ctypes.create_string_buffer(0x800)
        n = w.UINT(0)
        h = d.SteamAPI_ISteamUser_GetAuthSessionTicket(user, buf, 0x800, ctypes.byref(n), None)
        print("[probe] try%d handle=%#x len=%d" % (i, h, n.value))
        if h and n.value:
            head = buf.raw[:min(n.value, 12)].hex().upper()
            print("[probe] ticket head=%s..." % head)
            os.makedirs(os.path.join(HERE, "credentials"), exist_ok=True)
            with open(os.path.join(HERE, "credentials", "_probe_ticket.txt"), "w") as f:
                f.write(buf.raw[:n.value].hex().upper())
            if hasattr(d, "SteamAPI_ISteamUser_CancelAuthTicket"):
                d.SteamAPI_ISteamUser_CancelAuthTicket(user, h)
            print("[probe] OK -> credentials/_probe_ticket.txt")
            break
        if hasattr(d, "SteamAPI_ISteamUser_CancelAuthTicket") and h:
            d.SteamAPI_ISteamUser_CancelAuthTicket(user, h)
        if hasattr(d, "SteamAPI_RunCallbacks"):
            d.SteamAPI_RunCallbacks()
        time.sleep(1.5)
    else:
        print("[probe] 8 次重试全部失败")
finally:
    try:
        d.SteamAPI_Shutdown()
    except Exception:
        pass
    if not existed and os.path.exists(appid_path):
        os.remove(appid_path)
