# -*- coding: utf-8 -*-
"""Moli CDP 裸驱:开页/取图链/截图。用法见底部 main。"""
import base64, json, sys, time, urllib.request
import websocket

BASE = "http://127.0.0.1:9222"

def new_tab(url):
    req = urllib.request.Request(BASE + "/json/new?" + url.replace(":", "%3A").replace("/", "%2F"), method="PUT")
    # 新版 Chrome 需要 PUT /json/new?url=
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception:
        data = json.loads(urllib.request.urlopen(BASE + "/json/new", data=b"", timeout=10).read())
    return data

def list_tabs():
    return json.loads(urllib.request.urlopen(BASE + "/json/list", timeout=10).read())

class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.mid = 0
    def cmd(self, method, **params):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
    def nav(self, url, wait=3.0):
        self.cmd("Page.enable")
        self.cmd("Page.navigate", url=url)
        time.sleep(wait)
    def js(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")
    def shot(self, path):
        r = self.cmd("Page.captureScreenshot", format="png")
        open(path, "wb").write(base64.b64decode(r["data"]))

def wiki_file_url(title):
    """Special:FilePath 重定向到原始文件。"""
    return "https://dontstarve.wiki.gg/wiki/Special:FilePath/" + title

if __name__ == "__main__":
    tab = new_tab("https://dontstarve.wiki.gg/wiki/Gift")
    ws = tab["webSocketDebuggerUrl"]
    c = CDP(ws)
    time.sleep(2)
    print("TITLE:", c.js("document.title"))
    # 抓页面里所有缩略图/文件链接,供人工挑
    imgs = c.js("""JSON.stringify((()=>{const out=[];
      document.querySelectorAll('img').forEach(im=>{
        const s=im.getAttribute('src')||'';
        if(/\\.(png|jpg|webp)/i.test(s)) out.push({src:s.split('/revision')[0], alt:im.alt||'', w:im.naturalWidth});
      });
      return out.slice(0,40);})())""")
    print("IMGS:", imgs)
    c.shot("_wiki_gift.png")
    print("screenshot saved")
