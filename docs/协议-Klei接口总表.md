# Klei 接口总表(DST 客户端全量接口目录)

> 📦 Chester 深入文档 · [← 返回 README](../README.md)
> 来源:对 `dontstarve_steam_x64.exe` / `dontstarve_dedicated_server_nullrenderer_x64.exe` 的全量字符串收割
> (完整 URL、相对路径、域名三路交叉),并抽样实测。2026-10-27。

---

## 0. 域名总览

| 域名 | 用途 |
|---|---|
| `login.kleientertainment.com` | 账号/登录/会话 |
| `items.kleientertainment.com` | 物品/皮肤/礼物 |
| `lobby-v2.klei.com` / `lobby-v2-{region}.klei.com` / `lobby-v2-cdn.klei.com` | 服务器大厅(浏览/注册) |
| `motd.klei.com` / `klei-motd.klei.com` | 主菜单公告(内容/图片) |
| `dst-translation-mods.klei.com` | 官方翻译 MOD 分发 |
| `accounts.klei.com` | 账号网页(开服管理/奖励) |
| `mods.klei.com` | MOD 服务 |
| `dst.metrics.klei.com` / `clientservice.kleientertainment.com` / `analytics-staging.office.kleientertainment.com` | 遥测/统计 |
| `games-feedback.klei.com` / `crashes.klei.ca` / `feedback.klei.com` | 反馈/崩溃 |
| `{region}.gsm.events.klei.ca` / `theforge.kleientertainment.com` | 活动专用服(熔炉等) |
| `s3.amazonaws.com/dstbuilds/` 等 S3/CloudFront | 客户端构建/资源分发 |

## 1. 账号与会话(login.kleientertainment.com/login/*)

| 端点 | 用途 | 鉴权 |
|---|---|---|
| `/login/LoginViaSteam` | Steam 票据登录(✅ Chester 使用) | SteamTicket |
| `/login/TokenPurpose` | token 续期/换发(✅ Chester 使用) | Token |
| `/login/GameSessionToken` | 主机校验进服玩家(✅ 文档已录) | Token |
| `/login/ServerLoginTokenViaSteam` | **专用服专用登录 token**(开服链路) | SteamTicket(服务端) |
| `/login/Login` | 账号密码登录 | 密码 |
| `/login/CreateAccount` / `RegisterGameKey` / `RegisterSteamAccount` | 注册/绑号 | — |
| `/login/RecoverPassword` / `ChangePassword` | 找回/改密 | — |
| `/login/Logout` / `LogoutAll` | 注销(单会话/全设备) | Token |
| `/login/auto/` | 自动登录辅助 | — |
| `/login/ReportUser` | 举报用户 | Token |

## 2. 物品与礼物(items.kleientertainment.com/clientitems/DST/*)

✅ = Chester 已接入;头:物品接口用 `ClientToken`,心跳/回执类用 `Token`。

| 端点 | 用途 |
|---|---|
| ✅ `/Tick` | 心跳(360s),推进周礼掉落 |
| ✅ `/GetAllItems` | 全量库存(`Context==3` 为未开礼物) |
| ✅ `/SetItemOpened` | 开礼(Context 3→1) |
| ✅ `/VerifyGiftingReceipt` | 礼物回执校验(防伪造) |
| ✅ `/DailyGift` | 每日登录礼 |
| ✅ `/OpenMysteryBox` | 开神秘箱 |
| `/Swap` | 物品交换/皮肤升级(编织 обмен) |
| `/RedeemCode` | 兑换码核销 |
| `/ValidateItems` / `/ValidateSignature` | 库存与签名校验(离线降级校验) |
| `/GrantSkinDLCEntitlements` | DLC 皮肤权益发放 |
| `/ReportUnknownItemType` / `/ReportMission/{CROW,REDBIRD,SNOWBIRD,CROWKID}_GAME` | 未知物品上报 / 打鸟任务上报(换皮肤的小任务) |

## 3. 服务器大厅(lobby-v2,详见[协议-服务器查询.md](协议-服务器查询.md))

| 端点 | 用途 | 鉴权 |
|---|---|---|
| `GET lobby-v2-cdn.klei.com/regioncapabilities-v2.json` | 大区表(4 区) | 无(✅ 已实测) |
| `/lobby/write` | 专用服注册上表 | 开服 Token |
| `/lobby/update` / `keepAlive` | 状态更新/保活 | 开服 Token |
| `/lobby/read` | 详情读取(玩家/天数/MOD) | 开服 Token |
| `/lobby/getIP` | 取服务器 IP | ? |
| `/lobby/delete` | 注销 | 开服 Token |
| 列表批量简报 | `lobby-v2-cdn.klei.com/{Region}-{Platform}.json.gz`(gzip) | 实测 403,当前需代理/镜像 |

## 4. 公告与资源分发

| 接口 | 用途 | 实测 |
|---|---|---|
| `GET motd.klei.com/motd.json/?game=dst&platform=steam` | **主菜单公告流**(在售礼箱/活动/版本号),图片为 `.tex` 存于 `klei-motd.klei.com` | ✅ 200,含实时内容(2026-10-27:Chess Chest/Cursed Chest 在售,live-version 736805) |
| `https://dst-translation-mods.klei.com/` | 官方中文翻译 MOD 分发 | 403(需具体路径/签名) |
| `s3.amazonaws.com/dstbuilds/` + CloudFront | 客户端自更新分发 | — |
| `s3.amazonaws.com/ds-motd/`、`ds-motd.s3.amazonaws.com` | MOTD 历史资源 | — |

## 5. 遥测与反馈(仅收录,勿滥用)

`dst.metrics.klei.com/write`、`clientservice.kleientertainment.com/analysisCL`、
`analytics-staging.office.kleientertainment.com/write`(统计上报);
`games-feedback.klei.com/submit`、`crashes.klei.ca/submitCrash`(反馈/崩溃);
`{region}.gsm.events.klei.ca`(活动专用服 GSM)。

## 6. 账号网页(浏览器)

- `accounts.klei.com/account/game/servers?game=DontStarveTogether` — **开服管理页(申请开服 Token)**
- `accounts.klei.com/account/rewards` — Klei 积分/奖励页
- `steamcommunity.com/profiles/{SteamID}/friends/` 等 — Steam 好友富状态(客户端引用)

## 7. Chester 工具覆盖情况

- 登录/续期:`LoginViaSteam` + `TokenPurpose`(klei_steam_login.py / 面板)
- 心跳/领礼:`Tick` / `DailyGift` / `GetAllItems` / `SetItemOpened` / `VerifyGiftingReceipt`(面板 worker)
- 服务器查询:lobby-v2 数据(经 dstserverlist 镜像,dst_servers.py;原始接口见 §3)
- 未使用:Swap/RedeemCode/ReportMission/账号注册类(记录备查,无计划接入)
