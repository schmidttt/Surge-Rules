# TikTok.list 设计

## 范围

`rules/TikTok/TikTok.list` 用于让 TikTok 应用、网页、接口、直播和产品 CDN 域名统一进入 Surge 的 `📱 TikTok` 策略组。它不会因为同属字节跳动而自动收录 CapCut、Trae、MarsCode 等其他产品。

## 数据边界

- 唯一正式上游：`v2fly/domain-list-community` 的 `data/tiktok`。
- BlackMatrix7：只统计交集、差异数量和非域名规则类型，不自动导入。
- Sukka：只检查其地区敏感 TikTok 规则覆盖情况；其主动排除的无地区限制 CDN 仍可由本项目保留。
- `patches/tiktok/include.txt` 与 `exclude.txt`：仅用于抓包、官方资料或多源核实后的个人修正。

本项目第一版采用“TikTok 产品全流量”边界。这样更适合固定 TikTok 国家和账号环境；以后只有在实际抓包证明某个 CDN 无需跟随 TikTok 出口时，才考虑精确排除。

首版显式补充 `ibytedtos.com`、`ipstatp.com`、`isnssdk.com`：三者同时被 Sukka 与 BlackMatrix7 归入 TikTok，并存在公开的 TikTok 主机使用记录。`tiktok.us`、`tiktokw.com` 暂不因单一审计源而自动补入，继续由报告观察。

## 安全门槛

- 少于 20 条或丢失 `tiktok.com`、`tiktokv.com`、`tiktokcdn.com` 时停止构建。
- 任何删除都要求人工审核。
- 新增超过 2 条、真实变动超过 8% 或 Sukka 未覆盖数量增加时要求人工审核。
- 真实变动超过 20% 时停止构建，必须明确确认后才能重建。
- 输出文件末尾不写额外换行。

## Surge 顺序

广告与跟踪规则应位于 TikTok 之前；TikTok 应位于 Sukka 的全球流媒体总规则之前：

```ini
RULE-SET,<广告规则>,REJECT,...
RULE-SET,https://raw.githubusercontent.com/schmidttt/Surge-Rules/main/rules/TikTok/TikTok.list,📱 TikTok,update-interval=86400,extended-matching
RULE-SET,https://ruleset.skk.moe/List/non_ip/stream.conf,🎞️ 全球媒体,update-interval=86400,extended-matching
```
