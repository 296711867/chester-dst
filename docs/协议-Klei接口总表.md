# Klei 接口总表(DST 客户端接口详细手册)

> 📦 Chester 深入文档 · [← 返回 README](../README.md)
> 来源:对 `dontstarve_steam_x64.exe` / 专用服 exe 的全量字符串收割 + 真实账号实测抓包。
> 标注说明:**[实测]** = 本机真实请求/响应抓取;**[推断]** = 由代码/文档推断,未实测。
> 2026-10-27。

---

## 0. 通用约定

- **物品/登录服务**:`https://items.kleientertainment.com` / `https://login.kleientertainment.com`,全部 `POST + application/json`;
- **请求头**:服务端会校验 `User-Agent: DontStarveTogether/<build>`(缺省也能通,带上更稳);
- **两种令牌**:
  - `Token` — 会话令牌(LoginViaSteam/TokenPurpose 签发,60 分钟),心跳/回执类用;
  - `ClientToken` — 同一个 Token,但物品接口的字段名叫 ClientToken(历史命名);
- **统一错误包装**(HTTP 仍为 200,看 body 判断):
  ```json
  {"SecurityTxt":"https://www.klei.com/.well-known/security.txt",
   "ErrorCode":"E_INVALID_TOKEN","Error":true}
  ```
- **常见错误码**:
  | 错误码 | 含义 | 处置 |
  |---|---|---|
  | `E_INVALID_TOKEN` | token 过期/被新登录顶掉 | TokenPurpose 续期或重登 |
  | `E_UNOPENED_ITEMS` | 有未开礼物挡住新掉落 | 先 SetItemOpened 清队列 |
  | `E_UNKNOWN_GAME` | Game 字段写错(必须 `DontStarveTogether`,无空格) | 改字段 |
  | `E_EXPIRE` / `E_BANNED` / `E_DELETED` / `E_UPGRADE` | 会话过期/封禁/账号删除/需升级 | 人工处理 |
- ⚠️ **每次新的 LoginViaSteam 会使同账号旧 Token 失效**(实测);TokenPurpose 续期不会。

---

## 1. 登录组(login.kleientertainment.com/login/*)

### 1.1 LoginViaSteam —— Steam 票据登录 **[实测]**

```
POST /login/LoginViaSteam
{"SteamTicket":"<480字符大写HEX票据>",
 "Game":"DontStarveTogether",
 "FriendlyHostDescriptor":"klei_panel",   // 主机描述,随意
 "NoEmail":true,                          // 不关联邮箱通知
 "TokenExpiry":3600}                      // 可选;省略=长效(1年),带上=限时
```
响应(实测):
```json
{"Token":"pcl-sing^KU_xxx^DontStarveTogether^<b64>",
 "UserID":"KU_xxxxxxxx","Username":"昵称",
 "AnalyticsJWT":"<RS512 JWT,1年>","Info":[...]}
```
| 字段 | 说明 |
|---|---|
| `Token` | GameSessionToken,60 分钟有效,后续所有接口的身份凭证;格式 `pcl-<区域>^<KU_>^<游戏>^<b64>` |
| `UserID` | Klei 账号 ID,`KU_` 前缀(玩家)/`OU_`(组织) |
| `AnalyticsJWT` | 遥测用 JWT,与游戏会话无关 |

### 1.2 TokenPurpose —— token 续期(链式)**[实测]**

```
POST /login/TokenPurpose
{"Token":"<当前Token>"}          // 不带 TokenExpiry → 长效
```
响应:全新 `Token` + `ExpirationTime`(= 签发 + 3605s)。**用返回的新 token 还能继续续**
(实测 ≥2 跳),这就是"一次登录长期挂机"的机制基础。省略 `TokenExpiry` 时响应无 `ExpirationTime`。

### 1.3 GameSessionToken —— 主机校验

```
POST /login/GameSessionToken {"Token":"<Token>"}
```
响应含 `Server` 字段(须等于本机 KU_)。游戏进服时主机用来验玩家身份;挂机工具用不到。

### 1.4 其余(未实测,[推断] 自字面与调用点)

| 端点 | 用途 |
|---|---|
| `/login/ServerLoginTokenViaSteam` | 专用服拿登录令牌(开服链路,配合 accounts.klei.com 的开服 Token) |
| `/login/Login` | 账号密码登录(Klei 账号,非 Steam) |
| `/login/CreateAccount` / `RegisterGameKey` / `RegisterSteamAccount` | 注册/激活游戏KEY/绑定 Steam 号 |
| `/login/RecoverPassword` / `ChangePassword` | 找回/改密 |
| `/login/Logout`(`{Token}`)/ `LogoutAll` | 注销当前会话 / 注销该账号全部会话 |
| `/login/auto/?Game=DontStarveTogether` | 客户端启动时的自动登录探测 |
| `/login/ReportUser` | 举报用户 |

---

## 2. 物品组(items.kleientertainment.com/clientitems/DST/*)

### 2.1 Tick —— 心跳 **[实测]**

```
POST /clientitems/DST/Tick
{"Token":"<Token>"}               // 注意字段名是 Token
```
响应(实测三种):
```json
{"Error":false,"GiftReceived":false}                       // 多数轮:无礼
{"Error":false,"GiftReceived":{"Key":"<回执Key>"}}          // 有礼:拿 Key 去回执
{"Error":true,"ErrorCode":"E_UNOPENED_ITEMS",...}          // 有积压未开礼
```
掉落节奏由服务端控制(约 2 小时一掉,周上限 8)。

### 2.2 GetAllItems —— 全量库存 **[实测]**

```
POST /clientitems/DST/GetAllItems
{"ClientToken":"<Token>"}
```
响应(实测,顶层 9 字段):
```json
{"Error":false,"UserID":"KU_…","Items":[…1054 件…],
 "CurrencyMap":{"SPOOLS":20},"Unlocks":{},"EventHash":"4c0f5008",
 "InventoryStateToken":"","Unchanged":false,
 "SecurityTxt":"https://www.klei.com/.well-known/security.txt"}
```
| 字段 | 说明 |
|---|---|
| `Items[].ItemID` | 物品实例 ID(uint64,交易/升级定位用) |
| `Items[].ItemType` | 物品类型码,如 `TOPHAT_CHEF`(槽位_角色_皮肤集) |
| `Items[].Modified` | 最后修改时间(unix 秒) |
| `Items[].Context` | **1=已开/正常,3=未开礼物**(收礼判定就靠它) |
| `CurrencyMap` | 钱包余额;`SPOOLS`=线轴(编织皮肤的材料货币) |
| `Unlocks` | 已解锁的合成配方/收藏 |
| `EventHash` | 限时活动皮肤归属哈希 |
| `InventoryStateToken` | 库存状态令牌(增量同步用) |
| `Unchanged` | 库存与上次相比无变化 |

### 2.3 SetItemOpened —— 开礼物 **[实测]**

```
POST /clientitems/DST/SetItemOpened
{"ItemID":<uint64>,"ClientToken":"<Token>"}
```
响应:`{"Error":false,"UserID":"KU_…","Modified":<新时间戳>,"ItemID":<id>}`(Context 3→1)。

### 2.4 VerifyGiftingReceipt —— 礼物回执 **[实测]**

```
POST /clientitems/DST/VerifyGiftingReceipt
{"Receipts":[{"UserID":"KU_…","Key":"<Tick给的Key>"}]}
```
防伪造:服务端核对 Key 确实发放过。成功后该礼进入 `Context==3` 队列。

### 2.5 DailyGift —— 每日登录礼 **[实测]**

```
POST /clientitems/DST/DailyGift
{"ClientToken":"<Token>"}
```
响应(实测两种):
```json
{"Error":false,"GiftReceived":true,"ItemType":"TOPHAT_CHEF","ItemID":61701429816}  // 今日有礼
{"Error":false,"GiftReceived":false}                                               // 今日已领/无
```
自上次领取起 **24h 滚动幂等**(2026-10-28 实测修订:本地 00:01 调用答 false、滚动点过后同日再调即 true——旧"自然日每天一次"结论作废;与周礼是两条独立线)。

### 2.6 OpenMysteryBox —— 开神秘箱 **[实测]**

```
POST /clientitems/DST/OpenMysteryBox
{"ItemID":<uint64>,"ClientToken":"<Token>"}
```
响应:`{"Error":false,"Items":[开出的物品…]}`。神秘箱 `Context=1`,不挡心跳。

### 2.7 其余(未实测)

| 端点 | 用途([推断]) |
|---|---|
| `/Swap` | 皮肤编织交换(消耗线轴 Spools 换/升级皮肤) |
| `/RedeemCode` | 兑换码核销 |
| `/ValidateItems` / `/ValidateSignature` | 进服时主机校验玩家皮肤合法性 |
| `/GrantSkinDLCEntitlements` | DLC 皮肤权益发放 |
| `/ReportUnknownItemType` | 未知物品上报(客户端自检) |
| `/ReportMission/{CROW,REDBIRD,SNOWBIRD,CROWKID}_GAME` | 打鸟小任务上报(任务链换皮肤) |

### 2.8 礼物获取历史:不存在(2026-10-28 逆向实证)

Klei **没有任何"礼物获取历史/掉落记录"端点**。证据:①clientitems 全家桶如上,无 History 类路径;
②`dontstarve_steam_x64.exe` 全字符串挖掘,所有 History 命中均无关(TransformationHistory=网络插值/
PartyChatHistory=聊天/Entitlements=Twitch 权益系),无 GiftHistory/ItemHistory 端点——客户端只能调它
认识的接口,故服务端不提供。`GetAllItems` 的 `Items[].Modified`(unix 秒)也不能当获取时间用:
编织 `/Swap`、交易、神秘箱开出同样刷新 Modified,无法区分礼物获取。结论:历史只能本地攒
(Chester 每账号 SQLite gifts 表即本地史);"面板外领取"协议层不可见,只能启发式(见 README 疑似满仓)。

---

## 3. 服务器大厅(lobby-v2)

### 3.1 大区表 **[实测,免鉴权]**

```
GET https://lobby-v2-cdn.klei.com/regioncapabilities-v2.json
→ {"LobbyRegions":[{"Region":"us-east-1"},{"Region":"eu-central-1"},
                   {"Region":"ap-southeast-1"},{"Region":"ap-east-1"}]}
```

### 3.2 生命周期(开服 Token 鉴权,[推断] 自调用点)

| 端点 | 用途 |
|---|---|
| `POST lobby-v2.klei.com/lobby/write` | 专用服注册(上表) |
| `POST /lobby/update` | 更新状态(人数/版本/世界信息) |
| `POST /lobby/keepAlive` | 保活(不调则列表摘除) |
| `POST /lobby/read` | **详情读取**:body `{"__gameId":"DontStarveTogether","__token":"<开服Token>","query":{"__rowId":"<RowId>"}}`,响应 `{"GET":[完整数据]}`(玩家/天数/MOD);**需开服 Token** |
| `POST /lobby/getIP` | 取服务器连接 IP |
| `POST /lobby/delete` | 注销 |

批量简报的 Klei 原始形态:`lobby-v2-cdn.klei.com/{Region}-{Platform}.json.gz`(gzip,实测现 403)。

### 3.3 公开镜像(dstserverlist.top,免 Token,字段=游戏浏览器)

**`POST api/v2/server/list`** 请求体:
| 字段 | 类型 | 说明 |
|---|---|---|
| `ServerName` | `{Value,IsExclude,IsRegex}` | 服务器名筛选(支持中文/正则) |
| `PlayerName` | 同上 | 按在线玩家名筛 |
| `Sort` | `["+/-字段"]` | `Connected/Days/ServerName/PlayerName/Season` |
| `PageCount` / `PageIndex` | int | 每页条数 / 页码(从 0) |

响应 `List[]` 字段:`Name`(服名)、`Address{IP,IsoCode}`、`Port`(10999)、`RowId`(房主 KU_)、
`Connected`/`MaxConnections`、`Mode`(survival/endless/wilderness)、`Intent`(生存/无尽…)、
`Season`(autumn/winter/spring/summer)、`IsPvp`/`IsPassword`/`IsMods`/`IsDedicated`/`IsClanOnly`/
`IsFriendsOnly`/`IsAllowNewPlayers`/`IsServerPaused`、`Version`(游戏版本号)、`Session`(存档 GUID)、
`SteamId`/`SteamRoom`、`LobbyRegion`、`Secondaries`(从服[{Address,Port,Id,SteamId}])。

**`POST api/v2/server/details?id=<RowId>&forceUpdate=false`**(参数走查询串)→
`Server:{Players:[{Name,NetId(SteamID),Color}], DaysInfo{Day,DaysElapsedInSeason,DaysLeftInSeason,TotalDaysSeason},
ModsInfo[{Id,Name,CurrentVersion,NewVersion,IsClientDownload}], Tick, Nat, …}`。

**`POST api/v2/server/GetTotal`** → `{Servers:10240, Connections:6514, Version, DateTime}`。

## 4. 公告(MOTD)**[实测]**

```
GET https://motd.klei.com/motd.json/?game=dst&platform=steam
```
`platform` 必带(缺省 400)。响应:`{Audience:"DST", live-version:"736805", testing-version,
boxes-live:{Category:{Skins:[{guid,start-time,finish-time,title,text,image(.tex),href,filter_menu}]}}}`——
即游戏主菜单的在售礼箱/活动横幅(当前:Chess Chest/Cursed Chest)。

## 5. 其它分发/遥测

`dst-translation-mods.klei.com`(官方翻译 MOD,直接访问 403,需具体路径)、
`s3.amazonaws.com/dstbuilds/`(客户端自更新)、`klei-motd.klei.com`(MOTD 图片 .tex)、
遥测:`dst.metrics.klei.com/write` / `clientservice…/analysisCL` / `analytics-staging…/write`
(仅收录,勿滥用)。

## 6. Chester 工具覆盖

LoginViaSteam + TokenPurpose(登录/续期)· Tick/DailyGift/GetAllItems/SetItemOpened/
VerifyGiftingReceipt/OpenMysteryBox(挂机领礼)· lobby 数据(dst_servers.py 经镜像)。
未使用:Swap/RedeemCode/账号注册类/遥测(记录备查)。
