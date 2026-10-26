# Chester(切斯特)· 不开游戏登饥荒联机版 + 挂机领每周礼物

**Chester — a background gift farmer for Don't Starve Together. No game launch needed. 中文为主, English summary at the bottom.**

> 📦 **为什么叫 Chester?** 饥荒里,切斯特是那只用眼骨召唤、忠实跟随你的随行礼物箱——
> 你只管冒险,它替你背着所有宝贝。这个工具就是你的切斯特:你不在游戏,它替你收礼、开礼、存礼。

![界面预览](screenshots/parchment.png)

> 对 `dontstarve_steam_x64.exe` 做 C 层逆向还原出的**可独立运行、无需启动游戏**的 Klei 登录/心跳/领礼工具链。
> 全部结论经真实账号线上验证;2026-10-26 在当前版本 exe/dll 上全链路复验通过。

- **小白入口**:双击 [挂机指南.html](挂机指南.html)(推荐,带进度勾选/排错矩阵)
- **可视化挂机**:双击 `启动控制面板.bat`(本地控制面板:开始/停止/统计/礼物监控/历史/每账号数据库)
- **黑窗口挂机**:双击 `心跳自动领取.bat`

---

## 0. 截图

| 饥荒官网(默认) | 冬季盛宴 | 熔炉 |
|---|---|---|
| ![官网明亮](screenshots/parchment.png) | ![冬季盛宴](screenshots/winter.png) | ![熔炉](screenshots/forge.png) |

共 6 套皮肤,面板顶栏一键切换(另含 暗夜营地/月色/常青森林)。

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

## 2. 快速开始

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

## 3. 脚本与运行模式

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

## 4. 读懂日志

日志位置:`credentials\hb_auto_console.log`(面板另有 SQLite 记录)。关键行:

| 行 | 含义 |
|---|---|
| `登录完成 UserID=KU_…` | 登录成功(核心标志) |
| `心跳正常,本轮无礼物(GiftReceived=False)` | 挂机成功(核心标志,多数轮长这样) |
| `🎁 心跳带礼物 Key=…` → `★ 领取礼物 …` | 服务端掉礼并已入账 |
| `Token 将到期,续期中…` → `已续期/已重登` | 正常自愈,无需动作 |
| `检测到未开礼物,自动清理 Context=3 队列` | 自动开积压礼,不是错误 |
| `Tick 网络错误: NETERR: …` | 断网,下轮自动重试 |

## 5. 故障排查(速查)

| 现象 | 动作 |
|---|---|
| 找不到 steam_api64.dll | 项目与 dst 放同级,或 `--dll` 全路径 |
| WinError 193 | 32 位 Python,换 64 位 |
| SteamAPI_Init 失败 | Steam 没开/没登录 |
| GetAuthSessionTicket handle=0 | 偶发未就绪(已自动重试);反复出现跑 `_probe_ticket.py` |
| 换 Steam 号后还是旧 KU_ | 删 `credentials\.hb_auto_session.json` + `accounts\` 旧目录 |

完整 13 条矩阵(可 Ctrl+F 日志关键词):[挂机指南.html 第 6 章](挂机指南.html)。

## 6. 文件与目录说明

```
chester\
├─ 挂机指南.html            小白教程(双击即开,零依赖)
├─ 启动控制面板.bat / 心跳自动领取.bat
├─ klei_panel.py            控制面板服务 v5(多账号勾选/设置/统计/礼物中文标签,
│                             127.0.0.1:8377,--port/--no-browser)
├─ web\挂机面板.html          面板前端(中英切换、6 套皮肤、DST 图标、Amarante 字体)
├─ web\assets\               DST 图标(icons)+ 字体(fonts),面板 /assets/ 伺服
├─ panel_settings.json       面板设置(运行期生成,gitignore)
├─ panel_accounts.json       账号注册表(运行期生成,gitignore)
├─ _cdp.py                   moli CDP 调试助手(抓图标/截图用,开发工具)
├─ klei_steam_login.py      票据+登录+续期+dll查找(库+CLI)
├─ klei_heartbeat.py        共享 HTTP/tick/日志/登录(库+CLI)
├─ klei_heartbeat_auto.py   常驻心跳+领礼(黑窗口模式)
├─ klei_open_gifts.py       开礼工具
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

## 7. 协议参考(速查)

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
| `/clientitems/DST/DailyGift` | 每日登录礼 | **实测响应 `GiftReceived:true` + 顶层 `ItemType/ItemID`**,按天幂等 |
| `/clientitems/DST/GetAllItems` | 全量库存 | 未开礼物=`Context==3` |
| `/clientitems/DST/SetItemOpened` | 开礼 | `{ItemID,ClientToken}`,Context 3→1 |
| `/clientitems/DST/OpenMysteryBox` | 开神秘箱 | 与周礼门控无关 |
| `/clientitems/DST/VerifyGiftingReceipt` | 礼物回执 | `{Receipts:[{UserID,Key}]}` |

错误码:`E_INVALID_TOKEN`(过期,自动续期/重登)/ `E_UNOPENED_ITEMS`(先开礼)/
`E_UNKNOWN_GAME`(Game 必须无空格)/ `E_BANNED`/`E_EXPIRE`/…。

### 挂机策略(2026-10-26 定版)
- 周礼 PC 上限 **8 个/周**(社区情报),领满约 24h 不再掉 → 面板滑动 7 天窗口**领满即停**,自动恢复;
- 每日礼与周礼是两条线,面板每天自动调一次 DailyGift;
- 间隔默认 360s 对齐游戏行为;社区实测缩短间隔可加速出礼(4-5h→1h)但属加速行为,风控自担;
- 交叉验证:皮肤即 Steam 物品,`steamcommunity.com/inventory/<steamid>/322330/2`。

## 8. 安全与合规

- Token=你 Steam 账号的 Klei 登录凭证,60 分钟自过期,仍勿外传;`credentials\` 整目录(会话/日志/账号库)
  已被 `.gitignore` 排除,不要发布;
- 控制面板只监听 127.0.0.1,Host 头校验,路由白名单,响应不含 token;日志/事件/礼物统计中 UserID 脱敏,
  仅账号管理接口(/api/accounts)因勾选/移除操作需要返回完整 userid(本地面板专属,不对外);
- 频率纪律:间隔下限 30s(强制),默认 360s;别高频循环登录;
- 行为受 Klei 服务条款约束,仅供自用学习;日志分享前抹掉 `KU_`/SteamID/`pcl-` 串;
- **免责声明**:本项目与 Klei Entertainment 无任何关联;使用本工具产生的账号风险由使用者自行承担;
- **素材署名**:界面中的游戏图标与 "Don't Starve" 字样版权归 Klei Entertainment 所有,
  此处仅作个人工具的非商业性标识使用;如权利方提出要求,将立即替换。

## 9. 多账号挂机(面板已支持)

面板「添加账号」= 人肉环节:先把 Steam 客户端**切换登录**到目标账号 → 点添加(面板代为登录并存档会话)
→ 勾选启用 → 开始挂机;之后该账号日常走 token 链式续期,纯 HTTP,不再需要 Steam 在场。
worker 每轮顺序处理所有启用账号(账号间错峰 3 秒),领满/暂停按账号独立;
账号不符(Steam 停在别的账号)只暂停该账号,切号后在面板把该账号关→开即重试。
每账号独立 SQLite 数据库(credentials\accounts\KU_…\)。详见 [docs/多账号路线图.md](docs/多账号路线图.md)。

## 10. FAQ

- **能和游戏同时开?** 能,实测 6+ 次无冲突(会话追加式);但面板与黑窗口 bat 别同时开。
- **几天没礼物正常?** 正常(约 2 小时一个、周上限 8;或电脑睡眠了)。
- **会被封?** 未知;低频同构请求,风险自担。

## 11. 附录

### 11.1 研究笔记索引
`docs\协议-登录与会话.md`(C 层登录还原+脚本实现对照)、`docs\协议-心跳与礼物.md`(Tick 协议)、
`docs\附录-礼物机制分层笔记.md`、`docs\附录-会话转录-礼物机制.md`(原始材料)、
`docs\情报-社区与SDK调研_2026-10-26.md`。

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
    (顶层字段,非嵌套),按天幂等;当日 TOPHAT_CHEF 已入账。

### 11.4 版本
工具链 v1.1(2026-10-26)· 适用当前 DST 版本 · Python 3.8+(纯标准库)
- v1.0 08-30:协议逆向+四脚本全链路验证;10-26 复验当前版本可用
- v1.1a 10-26:票据 5 参 identity=None;周礼 8/周领满即停;DailyGift 接入
- v1.1b 10-26:控制面板(设置持久化/统计/礼物监控/每账号 SQLite)
- v1.1c 10-26:多账号勾选挂机(注册表/顺序轮询/L2 单账号暂停)
- v1.1d 10-26:DST 图标 + Amarante 字体 + 中英切换 + /assets/ 静态服务
- v1.1e 10-26:皮肤系统(6 套主题:饥荒官网明亮=默认/暗夜营地/冬季盛宴/熔炉/月色/常青森林)
- **v1.2 10-26:项目更名 Chester(切斯特)**;视觉验收轮优化(品牌标/favicon/切换过渡)


---

## English summary

**Chester** is a background companion for *Don't Starve Together*: it logs into your Klei account
with a Steam session ticket (no game launch), sends a heartbeat every 360 s, claims weekly skin
gifts automatically (8/week cap with auto-pause), and keeps one SQLite database per account.

- **Web panel** (Chinese/English, 6 themes, game icons): run `启动控制面板.bat` or
  `python klei_panel.py`, then open `http://127.0.0.1:8377`.
- **Adding an account** (manual, once per account): switch the Steam client to that account,
  click *Add account* in the panel. Afterwards it renews tokens over pure HTTP — no Steam needed.
- **Requirements**: Windows x64, 64-bit Python 3.8+ (standard library only), Steam client signed in.
- Full guide: `挂机指南.html`. Protocol/design docs live in `docs/` (Chinese).

> ⚠️ Not affiliated with Klei Entertainment. Automating gift collection may violate the game's
> Terms of Service — use at your own risk, for personal and educational purposes only.
> Game icons shown in the UI are © Klei Entertainment, used as non-commercial identifiers.
