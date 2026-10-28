<div align="center">

# 📦 Chester · 切斯特

**不开游戏,挂着自动收《饥荒联机版》每周礼物**

**中文** | [**English**](README.en.md)

*You adventure in the waking world. Chester keeps the gifts.*

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![平台](https://img.shields.io/badge/平台-Windows%20x64-0078D6)](https://github.com/)
[![依赖](https://img.shields.io/badge/第三方依赖-零-2E7D32)]()
[![皮肤](https://img.shields.io/badge/皮肤-7%20套-C25A1E)]()
[![界面](https://img.shields.io/badge/界面-中文%20%2F%20English-C25A1E)]()
[![许可](https://img.shields.io/badge/许可-MIT-B3A279)]()

![Chester 面板预览](screenshots/dark.png)

*暮夜炭火皮肤 · 礼物自动配游戏图标 / 中文名 / 稀有度配色*

| 饥荒官网(默认) | 橙白 | 冬季盛宴 | 熔炉余烬 |
|---|---|---|---|
| ![](screenshots/parchment.png) | ![](screenshots/orange.png) | ![](screenshots/winter.png) | ![](screenshots/forge.png) |

</div>

## ✨ 它能做什么

| | |
|---|---|
| 🎁 **自动收周礼** | 每 360 秒向 Klei 心跳一次;礼物自动回执、开箱、入账,周上限 8/8 领满即停 |
| 🌙 **不开游戏** | 借 Steam 会话票据登录,日常只走纯 HTTP——连 Steam 客户端都不用开 |
| 👥 **多账号勾选挂机** | 每账号一次 Steam 切号+点添加;之后勾选即挂,账号间自动错峰,独立数据库 |
| 🖼️ **2953 件皮肤图鉴** | 礼物自动配游戏图标、官方英文名、中文名、稀有度配色(数据来自 dst-skins-checklist) |
| 🔎 **服务器查询** | 免登录查在线服务器:列表/筛选/详情/玩家/MOD(接口逆向) |
| 🎨 **7 套皮肤 / 中英界面** | 饥荒官网明亮(默认)、橙白、暮夜炭火、冬青鎏金、熔炉余烬、月白、苔林 |
| 🩹 **全自动自愈** | token 过期自动续期/重登、断网退避重试、未开礼物自动清理,挂着不用管 |

## ⚡ 快速开始

1. 装 **64 位 Python 3.8+**,Steam 客户端登录(不必开游戏);
2. 双击 **`启动控制面板.bat`**,浏览器自动打开 `http://127.0.0.1:8377`;
3. 点「▶ 开始挂机」,看到「心跳正常」就成了——完整图文见 [挂机指南.html](挂机指南.html)。

> ⚠️ 本项目与 Klei Entertainment 无关联;自动化行为可能违反游戏条款,**风险自担,仅供学习与个人使用**。

---

> 📦 **为什么叫 Chester?** 饥荒里,切斯特是那只用眼骨召唤、忠实跟随你的随行礼物箱——
> 你只管冒险,它替你背着所有宝贝。这个工具就是你的切斯特:你不在游戏,它替你收礼、开礼、存礼。

---

## 1. 这是什么(30 秒)

- **登录**:借本机已登录的 Steam 客户端取会话票据(ctypes 调 `steam_api64.dll`),
  `POST login.kleientertainment.com/login/LoginViaSteam` 拿 60 分钟 GameSessionToken——与游戏主菜单登录完全相同的请求。
- **心跳**:每 360 秒 `POST items.kleientertainment.com/clientitems/DST/Tick {"Token":…}`,
  服务端按心跳推进每周礼物掉落(不需要进世界,游戏主菜单挂机同理)。
- **领礼**:Tick 返回 `GiftReceived.Key` → 回执 `VerifyGiftingReceipt` → `GetAllItems` 找 `Context==3`
  → 逐个 `SetItemOpened` 开礼入账;`E_UNOPENED_ITEMS` 门控随之解除。
- **续期**:token 到期前 `TokenPurpose` 换新(已实测**链式续期 ≥2 跳**,续期后的 token 可正常 Tick)。
- **策略**(2026-10-26 起):控制面板按社区情报实现**周礼 8 个/周、领满即停**(滑动 7 天窗口自动恢复)
  并接入**每日登录礼 DailyGift**(旧脚本长期漏调的独立端点)。

边界:不改游戏文件、不注入进程、不伪造请求体,只发与游戏完全相同的正规 HTTP;
登录/礼物行为仍受 Klei 服务条款约束,仅供学习与自用。

## 2. 安装与运行(详细)

前置(一次性):Windows x64 + **64 位** Python 3.8+(仅标准库);Steam 客户端已登录(不必开 DST);
`steam_api64.dll` 在查找路径上(脚本目录 → 上级 → 上级 `dst\`,当前布局已就位)。

```bat
双击 启动控制面板.bat     :: 浏览器自动开 http://127.0.0.1:8377,填间隔点开始
双击 心跳自动领取.bat       :: 或纯黑窗口常驻
python klei_heartbeat_auto.py --once   :: 命令行单轮测试
```

成功的标志(日志 4 行):`开始 ══════` → `Steam 票据 … 字节` → `登录完成 UserID=KU_…` → `心跳正常,本轮无礼物`。
⚠️ 面板与黑窗口 bat **不要同时开**(互相顶掉 token);换 Steam 账号前先删
`credentials\.hb_auto_session.json` 与 `credentials\accounts\` 旧目录。

## 3. 打包成 exe(可选)

不想装 Python?一条命令打成单文件 exe(双击即启动面板并自动打开浏览器):

```bash
pip install pyinstaller
python -m PyInstaller --onefile --name Chester --icon icon.ico --add-data "web;web" --add-data "..\dst\steam_api64.dll;." klei_panel.py
```

产物在 `dist\Chester.exe`(约 26 MB,已内置 Python 运行时、全部网页资源与 `steam_api64.dll`,单文件即可分发)。注意事项:

- 账号数据库/会话/设置生成在 **exe 同目录**(`credentials\` 与 `panel_*.json`),整个文件夹随便搬;
- 重登录(添加账号)需要对方机器 Steam 在线并已登录目标账号;`steam_api64.dll` 已内置进 exe,exe 旁如另有同名 dll(或 `dst\steam_api64.dll`)会优先使用;
- 单文件 exe 首次启动需自解压,个别杀软可能误报,加白名单即可;
- 想发布:把 exe 挂到 GitHub Releases 即可,仓库本体不必入库构建产物。

## 4. 脚本与运行模式

| 入口 | 什么时候用 |
|---|---|
| `启动控制面板.bat` / `klei_panel.py` | **日常首选**:可视化开始/停止、心跳统计、礼物监控+历史、每账号 SQLite、领满即停、每日礼 |
| `心跳自动领取.bat` / `klei_heartbeat_auto.py` | 黑窗口常驻(360s 心跳+自动领礼+自动续期/重登) |
| `klei_steam_login.py` | 只想登录看凭证/续期(`--json` 存盘,`--refresh <TOKEN>` 续期) |
| `klei_heartbeat.py` | 纯心跳(适合计划任务 `--once`) |
| `klei_open_gifts.py` | 手动开礼物(`--dry-run` 列队,`--open-boxes` 开神秘箱,`--verify` 验门控) |
| `_probe_ticket.py` | 票据诊断(Init/登录态/8 次重试明细) |

常用参数:`--once` 只跑一轮 · `--interval N` 心跳间隔(默认 360,下限 30 强制,防风控别调快)
· `--dll PATH` 指定 steam_api64.dll · `--no-logfile` 不落盘日志 · `--token FILE`/`--save FILE` 复用/保存会话。

## 5. 读懂日志

日志位置:`credentials\hb_auto_console.log`(面板另有 SQLite 记录)。关键行:

| 行 | 含义 |
|---|---|
| `登录完成 UserID=KU_…` | 登录成功(核心标志) |
| `心跳正常,本轮无礼物(GiftReceived=False)` | 挂机成功(核心标志,多数轮长这样) |
| `🎁 心跳带礼物 Key=…` → `★ 领取礼物 …` | 服务端掉礼并已入账 |
| `Token 将到期,续期中…` → `已续期/已重登` | 正常自愈,无需动作 |
| `检测到未开礼物,自动清理 Context=3 队列` | 自动开积压礼,不是错误 |
| `Tick 网络错误: NETERR: …` | 断网,下轮自动重试 |

## 6. 故障排查(速查)

| 现象 | 动作 |
|---|---|
| 找不到 steam_api64.dll | 项目与 dst 放同级,或 `--dll` 全路径 |
| WinError 193 | 32 位 Python,换 64 位 |
| SteamAPI_Init 失败 | Steam 没开/没登录 |
| GetAuthSessionTicket handle=0 | 偶发未就绪(已自动重试);反复出现跑 `_probe_ticket.py` |
| 换 Steam 号后还是旧 KU_ | 删 `credentials\.hb_auto_session.json` + `accounts\` 旧目录 |

完整 13 条矩阵(可 Ctrl+F 日志关键词):[挂机指南.html 第 6 章](挂机指南.html)。

## 7. 文件与目录说明

```
chester\
├─ 挂机指南.html            小白教程(双击即开,零依赖)
├─ 启动控制面板.bat / 心跳自动领取.bat
├─ klei_panel.py            控制面板服务 v5(多账号勾选/设置/统计/礼物中文标签,
│                             127.0.0.1:8377,--port/--no-browser)
├─ web\挂机面板.html          面板前端(中英切换、7 套皮肤、DST 图标、Amarante 字体)
├─ web\assets\               DST 图标(icons)+ 字体(fonts)+ 全皮肤图标库(items,2953 件)
│                             面板 /assets/ 伺服
├─ panel_settings.json       面板设置(运行期生成,gitignore)
├─ panel_accounts.json       账号注册表(运行期生成,gitignore)
├─ _cdp.py                   moli CDP 调试助手(抓图标/截图用,开发工具)
├─ klei_steam_login.py      票据+登录+续期+dll查找(库+CLI)
├─ klei_heartbeat.py        共享 HTTP/tick/日志/登录(库+CLI)
├─ klei_heartbeat_auto.py   常驻心跳+领礼(黑窗口模式)
├─ klei_open_gifts.py       开礼工具
├─ dst_servers.py           在线服务器查询(接口逆向,见 docs\协议-服务器查询.md)
├─ _probe_ticket.py         票据诊断
├─ docs\
│  ├─ 协议-登录与会话.md       C 层登录全流程还原+实现对照+Token寿命
│  ├─ 协议-心跳与礼物.md       Tick/出礼协议还原
│  ├─ 多账号路线图.md          多账号方案设计(面板已实现,含 L0/L1/L2 降级)
│  ├─ 情报-社区与SDK调研_2026-10-26.md   SDK签名演化/SteamKit2/周礼情报
│  └─ 附录-*.md               原始研究笔记×2(存档)
└─ credentials\             凭证+日志+每账号数据库(gitignore 整目录)
   ├─ accounts\KU_…\account.db   heartbeats/gifts/events/meta 四表
   └─ .hb_auto_session.json      旧单账号会话(面板会自动接管迁移)
```

## 8. 协议参考(速查)

### 认证(login.kleientertainment.com)
| 端点 | body | 响应要点 |
|---|---|---|
| `/login/LoginViaSteam` | `{SteamTicket,Game:"DontStarveTogether",FriendlyHostDescriptor,NoEmail[,TokenExpiry]}` | `Token`/`UserID`(KU_…)/`AnalyticsJWT` |
| `/login/TokenPurpose` | `{"Token":…}` | 新 `Token`(+3605s);**链式续期实测成立** |
| `/login/GameSessionToken` | `{"Token":…}` | 主机校验进服玩家(`Server`==本地 userid) |

票据注意:`GetAuthSessionTicket` 调用按 5 参、identity 显式 `None`——SDK≥1.56 的平铺包装多一个
identity 指针,且 Klei 走 `ISteamUserAuth/AuthenticateUserTicket` 验票,绑定 identity 的票据会被拒。

### 物品(items.kleientertainment.com,表头 `ClientToken`,心跳用 `Token`)
| 端点 | 用途 | 备注 |
|---|---|---|
| `/clientitems/DST/Tick` | 心跳(360s) | 常见 `GiftReceived:{Key}` 或 false;门控 `E_UNOPENED_ITEMS` |
| `/clientitems/DST/DailyGift` | 每日登录礼 | **实测响应 `GiftReceived:true` + 顶层 `ItemType/ItemID`**,自上次领取 24h 滚动幂等 |
| `/clientitems/DST/GetAllItems` | 全量库存 | 未开礼物=`Context==3` |
| `/clientitems/DST/SetItemOpened` | 开礼 | `{ItemID,ClientToken}`,Context 3→1 |
| `/clientitems/DST/OpenMysteryBox` | 开神秘箱 | 与周礼门控无关 |
| `/clientitems/DST/VerifyGiftingReceipt` | 礼物回执 | `{Receipts:[{UserID,Key}]}` |

错误码:`E_INVALID_TOKEN`(过期,自动续期/重登)/ `E_UNOPENED_ITEMS`(先开礼)/
`E_UNKNOWN_GAME`(Game 必须无空格)/ `E_BANNED`/`E_EXPIRE`/…。

### 挂机策略(2026-10-26 定版,2026-10-27 增疑似满仓问询)
- 周礼 PC 上限 **8 个/周**(社区情报),领满约 24h 不再掉 → 面板滑动 7 天窗口**领满即停**,自动恢复;
- 每日礼与周礼是两条线:DailyGift 服务器为**自上次领取 24h 滚动**口径(非本地日界,2026-10-28 实测),面板领取成功后静默 4h、其后每 2h 探测一次自动补领,不会再因本地日界误判漏领;
- 间隔默认 360s 对齐游戏行为;社区实测缩短间隔可加速出礼(4-5h→1h)但属加速行为,风控自担;
- 交叉验证:皮肤即 Steam 物品,`steamcommunity.com/inventory/<steamid>/322330/2`。

### 疑似满仓监控与问询(2026-10-27)
面板外领的周礼(游戏内挂机、别的机器)协议层**查不到**(Tick 响应只有 GiftReceived.Key,无进度字段),
面板只能按启发式判断:**有效挂机干燥期 = 距上次周礼(或首个成功心跳)的成功心跳数 × 6 分钟**,
不看墙钟——电脑睡眠/断网/面板关闭期间服务端进度不走,干燥期自动冻结,不会误报。

- 默认 **6 小时有效挂机无周礼** → 判"疑似领满",弹窗问询:「继续挂机」/「暂停该账号」/「稍后提醒」;
- 问询挂起中该账号降频探测(30 分钟一跳),**30 分钟无人应答自动按"继续"**(缺席不坏事);
- 选「继续」→ 恢复全速,24h 内不再重复问;选「暂停该账号」→ 2 小时一跳低频探测,
  收到礼物自动恢复正常(窗口滑走后零人工自愈);期间每日礼照领;
- 探测期间真收到礼物 → 疑似态自动清除(误报自愈);面板实证 weekly≥8 时不会弹(已知答案不问);
- 期间有 `E_UNOPENED_ITEMS` 心跳 → 弹窗附注"礼物可能卡在库存未开"提示看日志;
- 设置卡可关(`疑似满仓问询`开关)或调阈值(3~12 有效小时,默认 6);
- 状态跨重启恢复(存每账号 SQLite meta);移除重加/关→开勾选即重置重新评估。

## 9. 安全与合规

- Token=你 Steam 账号的 Klei 登录凭证,60 分钟自过期,仍勿外传;`credentials\` 整目录(会话/日志/账号库)
  已被 `.gitignore` 排除,不要发布;
- 控制面板只监听 127.0.0.1,Host 头校验,路由白名单,响应不含 token;日志/事件/礼物统计中 UserID 脱敏,
  仅账号管理接口(/api/accounts)因勾选/移除操作需要返回完整 userid(本地面板专属,不对外);
- 频率纪律:间隔下限 30s(强制),默认 360s;别高频循环登录;
- 行为受 Klei 服务条款约束,仅供自用学习;日志分享前抹掉 `KU_`/SteamID/`pcl-` 串;
- **免责声明**:本项目与 Klei Entertainment 无任何关联;使用本工具产生的账号风险由使用者自行承担;
- **素材署名**:界面中的游戏图标与 "Don't Starve" 字样版权归 Klei Entertainment 所有,
  此处仅作个人工具的非商业性标识使用;如权利方提出要求,将立即替换;
  全皮肤名称/图标数据库来自 [dst-skins-checklist](https://dst-skins-checklist.github.io/)(2953 件,
  原码→英文名/收藏组/稀有度+图标),致谢该开源项目。

## 10. 多账号挂机(面板已支持)

面板「添加账号」= 人肉环节:先把 Steam 客户端**切换登录**到目标账号 → 点添加(面板代为登录并存档会话)
→ 勾选启用 → 开始挂机;之后该账号日常走 token 链式续期,纯 HTTP,不再需要 Steam 在场。
worker 每轮顺序处理所有启用账号(账号间错峰 3 秒),领满/暂停按账号独立;
账号不符(Steam 停在别的账号)只暂停该账号,切号后在面板把该账号关→开即重试。
每账号独立 SQLite 数据库(credentials\accounts\KU_…\)。详见 [docs/多账号路线图.md](docs/多账号路线图.md)。

## 11. FAQ

- **能和游戏同时开?** 能,实测 6+ 次无冲突(会话追加式);但面板与黑窗口 bat 别同时开。
- **几天没礼物正常?** 正常(约 2 小时一个、周上限 8;或电脑睡眠了)。
- **弹窗问"疑似领满"是怎么回事?** 有效挂机约 6 小时没等到周礼,多半是本周 8 个已在面板外(游戏内等)
  领满。点「继续挂机」照常挂;点「暂停该账号」转 2 小时低频探测,来礼物自动恢复。不想被打扰可在设置关掉。
- **会被封?** 未知;低频同构请求,风险自担。

## 12. 附录

### 11.1 研究笔记索引
`docs\协议-登录与会话.md`(C 层登录还原+脚本实现对照)、`docs\协议-心跳与礼物.md`(Tick 协议)、
`docs\附录-礼物机制分层笔记.md`、`docs\附录-会话转录-礼物机制.md`(原始材料)、
`docs\情报-社区与SDK调研_2026-10-26.md`、`docs\协议-服务器查询.md`(服务器浏览逆向)。
`docs\协议-Klei接口总表.md`(客户端全量接口目录,含 MOTD/lobby 生命周期/账号全家桶)。

### 11.2 C 层关键地址速查(dontstarve_steam_x64.exe,image base 0x140000000)
| 项 | VA |
|---|---|
| 心跳周期常量 float 360.0 | `0x140554eb4` |
| SendItemDropHeartbeat | `0x140277ae0`(gate `0x1402755e0`,update `0x140278020`) |
| Tick 完成回调(GiftReceived.Key→入队) | `0x1402760c0` / `0x140275ee0` |
| VerifyGiftingReceipt 构建 | `0x140277da0` |
| SetItemOpened | `0x1402587F0` · OpenMysteryBox `0x14027FAE0` |
| LoginViaSteam 端点选择/请求发送 | `0x1402CC650` / `0x140298D30` |
| 响应分发(type-3 成功/type-6 续期) | `0x140297B50`(跳表 `0x140298994`) |
| TokenPurpose 构建 | `0x140299300` |
| 票据→hex / body 构建 | `0x140294400` / `0x1402CCA30` |
| UA | `DontStarveTogether/<build>`(运行时拼接) |

### 11.3 实测记录时间线
- **2026-08-30**:首版全链路验证(登录 200/`ExpirationTime`+3605s/78min 旧 token 失效/
  SetItemOpened Context 3→1/开神秘箱/游戏共存 6+ 次)。
- **2026-10-26**:
  - 当前版 exe/dll 复验:协议未变,登录/Tick 原样可用;
  - `GetAuthSessionTicket` 偶发 handle=0(Steam user 未就绪)→ 重试恢复,已内置 5 次重试;
  - 新登录使同账号旧 token 失效(E_INVALID_TOKEN)→ 自动重登恢复,实测走通;
  - **TokenPurpose 链式续期 ≥2 跳成立**,续期后 token 可正常 Tick;
  - 票据调用改 5 参 identity=None(通吃新旧 SDK,Klei 走 AuthenticateUserTicket);
  - 控制面板实测:会话接管→心跳 200→SQLite 入库→404/405/Host 校验全对;
  - **DailyGift 实测响应形态**:`{"GiftReceived":true,"ItemType":"TOPHAT_CHEF","ItemID":…}`
    (顶层字段,非嵌套),24h 滚动幂等;当日 TOPHAT_CHEF 已入账。

### 11.4 版本
工具链 v1.1(2026-10-26)· 适用当前 DST 版本 · Python 3.8+(纯标准库)
- v1.0 08-30:协议逆向+四脚本全链路验证;10-26 复验当前版本可用
- v1.1a 10-26:票据 5 参 identity=None;周礼 8/周领满即停;DailyGift 接入
- v1.1b 10-26:控制面板(设置持久化/统计/礼物监控/每账号 SQLite)
- v1.1c 10-26:多账号勾选挂机(注册表/顺序轮询/L2 单账号暂停)
- v1.1d 10-26:DST 图标 + Amarante 字体 + 中英切换 + /assets/ 静态服务
- v1.1e 10-26:皮肤系统(6 套主题:饥荒官网明亮=默认/暗夜营地/冬季盛宴/熔炉/月色/常青森林)
- v1.1f 10-27:配色全面重设计+新增橙白,7 套皮肤;稀有度配色;全皮肤数据库 2953 件(图标/中文名/稀有度)接入面板
- **v1.2 10-26:项目更名 Chester(切斯特)**;视觉验收轮优化(品牌标/favicon/切换过渡)
