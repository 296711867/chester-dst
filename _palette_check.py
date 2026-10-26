# -*- coding: utf-8 -*-
"""调色板 WCAG 预检:所有关键文字对 >=4.5:1,UI 对 >=3:1。"""
def lum(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i+2], 16)/255 for i in (0, 2, 4))
    f = lambda c: c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b)
def cr(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi+0.05)/(lo+0.05)

def blend(spec, bg_hex):
    """(hex, alpha) 半透明层叠在实底上的有效色;字符串原样返回。"""
    if isinstance(spec, str):
        return spec.lstrip("#")
    hx, a = spec
    bg = bg_hex.lstrip("#")
    if "," in hx:
        r, g, b = (int(v) for v in hx.split(","))
    else:
        r, g, b = (int(hx[i:i+2], 16) for i in (0, 2, 4))
    br, bgb, bb = (int(bg[i:i+2], 16) for i in (0, 2, 4))
    return "%02x%02x%02x" % (round(r*a+br*(1-a)), round(g*a+bgb*(1-a)), round(b*a+bb*(1-a)))

# 每套皮肤:卡底(半透明 alpha 合成到本皮肤 bg 后再算)、各文字/组合
SKINS = {
 "parchment": dict(afg="a84a12", bg="f3ecd9", card=("ffffff", .82), muted="eadfc4", line="b3a279", th="e9dcc0",
   text="3b2f23", text2="6b5b45", accent="a84a12", aink="ffffff", asoft=("a84a12", .14),
   ok="2e7d32", red="c0392b", err="b3261e", rsoft=("c0392b", .12),
   amber="7d5b00", asoftbg=("154,103,0", .14), boxfg="5d3a83", boxbg=("106,67,144", .12)),
 "orange": dict(afg="b53d0b", bg="f7f5f2", card=("ffffff", .92), muted="f0ece6", line="ddd6cb", th="f4f0e9",
   text="292524", text2="6d6660", accent="c2410c", aink="ffffff", asoft=("c2410c", .08),
   ok="15803d", red="dc2626", err="b91c1c", rsoft=("dc2626", .10),
   amber="8a5406", asoftbg=("138,84,6", .10), boxfg="6d28d9", boxbg=("124,58,237", .10)),
 "dark": dict(afg="fb923c", bg="161412", card=("41,37,36", .75), muted="33302e", line="57534e", th="26221f",
   text="fafaf9", text2="a8a29e", accent="fb923c", aink="431407", asoft=("251,146,60", .15),
   ok="4ade80", red="f87171", err="f87171", rsoft=("248,113,113", .15),
   amber="fbbf24", asoftbg=("251,191,36", .15), boxfg="c4b5fd", boxbg=("196,181,253", .15)),
 "winter": dict(afg="e0b95f", bg="0f1c17", card=("23,42,34", .80), muted="213a30", line="3e5c4d", th="1b3028",
   text="eef4ee", text2="9db5a4", accent="cf9f3f", aink="241a05", asoft=("207,159,63", .15),
   ok="8fd0a5", red="e57373", err="f0a08e", rsoft=("229,115,115", .15),
   amber="e6c26a", asoftbg=("230,194,106", .15), boxfg="b9a7d9", boxbg=("185,167,217", .15)),
 "forge": dict(afg="f08a6c", bg="151011", card=("40,28,26", .80), muted="2e211f", line="5a4038", th="241a18",
   text="f7ece7", text2="c0a296", accent="de5638", aink="2b0d06", asoft=("222,86,56", .16),
   ok="f4a261", red="e05252", err="f2a099", rsoft=("224,82,82", .16),
   amber="f0b429", asoftbg=("240,180,41", .15), boxfg="c3a6e8", boxbg=("195,166,232", .15)),
 "lunar": dict(afg="33517a", bg="f1f3f6", card=("ffffff", .92), muted="e8ecf1", line="ccd4dd", th="e9edf2",
   text="1f2937", text2="57606c", accent="33517a", aink="ffffff", asoft=("51,81,122", .10),
   ok="1d6f54", red="c02626", err="b91c1c", rsoft=("192,38,38", .10),
   amber="8a5a00", asoftbg=("138,90,0", .12), boxfg="6d28d9", boxbg=("109,40,217", .10)),
 "forest": dict(afg="4a7040", bg="f0f4ec", card=("252,254,250", .92), muted="e4ebdc", line="c2cfb6", th="e7eedd",
   text="2a3327", text2="5c6a52", accent="4a7040", aink="ffffff", asoft=("74,112,64", .12),
   ok="2e7d32", red="b0413a", err="9a2c25", rsoft=("176,65,58", .11),
   amber="7d5b00", asoftbg=("125,91,0", .13), boxfg="5d3a83", boxbg=("93,58,131", .11)),
}

allpass = True
rows = []
for name, s in SKINS.items():
    card = blend(s["card"], s["bg"])                      # 卡片有效底色
    soft = lambda rgbat: blend(rgbat, card)               # soft 徽章底
    pairs = [
      ("text/card",       s["text"],  card, 4.5),
      ("text2/card",      s["text2"], card, 4.5),
      ("ink/accent(按钮)", s["aink"], s["accent"], 4.5),
      ("afg/soft(badge)", s["afg"], soft(s["asoft"]), 4.5),
      ("afg/card(.eta)", s["afg"], card, 4.5),
      ("text2/th(表头)",   s["text2"], s["th"], 4.5),
      ("ok/card",         s["ok"],   card, 4.5),
      ("err/red-soft(badge)", s["err"], soft(s["rsoft"]), 4.5),
      ("err/card(.fail)", s["err"],  card, 4.5),
      ("amber/asoft(badge.cap)", s["amber"], blend(s["asoftbg"], card), 4.5),
      ("amber/card(.note)", s["amber"], card, 4.5),
      ("boxfg/boxbg",     s["boxfg"], blend(s["boxbg"], card), 4.5),
    ]
    for label, fg, bgc, need in pairs:
        r = cr(fg, bgc)
        ok = r >= need
        allpass &= ok
        rows.append((name, label, r, ok, need))

cur = None
for name, label, r, ok, need in rows:
    if name != cur:
        print("── " + name); cur = name
    print(("  PASS " if ok else "  FAIL ") + "%-24s %.2f (需>=%.1f)" % (label, r, need))
print("=====")
print("ALL-PASS" if allpass else "HAS-FAIL")
