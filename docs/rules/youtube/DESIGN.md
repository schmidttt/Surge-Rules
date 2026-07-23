# YouTube.list 设计

## 范围

`rules/YouTube/YouTube.list` 用于让 YouTube 页面、接口、视频、图片和产品相关域名统一进入 Surge 的 `📹 YouTube` 策略组。它是产品全流量规则，不只收录地区检测接口。

## 数据边界

- 唯一正式上游：`v2fly/domain-list-community` 的 `data/youtube`。
- BlackMatrix7：只统计交集、差异数量和非域名规则类型，不自动导入。
- Sukka：只检查其 YouTube 地区敏感核心规则是否被本列表覆盖，不自动导入。
- `patches/youtube/include.txt` 与 `exclude.txt`：仅用于有证据的个人修正。

v2fly 的 `@ads`、`@cn` 等属性不会转换为 Surge 路由动作；域名仍保留在列表中。广告拦截和直连策略必须在 Surge 中通过更靠前的规则决定。

## 安全门槛

- 少于 150 条或丢失 `youtube.com`、`googlevideo.com`、`ytimg.com` 时停止构建。
- 任何删除都要求人工审核。
- 新增超过 5 条、真实变动超过 3% 或 Sukka 未覆盖数量增加时要求人工审核。
- 真实变动超过 15% 时停止构建，必须明确确认后才能重建。
- 输出文件末尾不写额外换行。

## Surge 顺序

广告与跟踪规则应位于 YouTube 之前；YouTube 必须位于广义 Google 规则之前：

```ini
RULE-SET,<广告规则>,REJECT,...
RULE-SET,https://raw.githubusercontent.com/schmidttt/surge-rules/main/rules/YouTube/YouTube.list,📹 YouTube,update-interval=86400,extended-matching
RULE-SET,https://raw.githubusercontent.com/schmidttt/surge-rules/main/rules/Google/Google.list,🔍 Google,update-interval=86400,extended-matching
```
