# BiliBili.list 设计

## 范围

`rules/BiliBili/BiliBili.list` 覆盖哔哩哔哩大陆主站、应用接口、视频与图片 CDN、漫画、游戏及 Bilibili International 相关域名，统一交给 Surge 的 `📺 BiliBili` 策略组。日常选择 `DIRECT`，需要访问港澳台版权内容或国际版时整体切换香港或台湾入口。

不把大陆版与国际版拆成两个远程规则。两者存在共享 API、视频 CDN 和应用连接，拆分容易造成同一次播放混用直连与代理出口。

## 数据边界

- 唯一正式上游：`v2fly/domain-list-community` 的 `data/bilibili`，递归展开 `bilibili-cdn` 与 `bilibili-game`。
- BlackMatrix7：只统计交集、差异数量和非域名规则类型，不自动导入其 IP、进程、User-Agent 或历史 CDN 条目。
- Sukka：只检查 Bilibili International 地区敏感核心规则是否被本列表覆盖，不自动导入。
- `patches/bilibili/include.txt` 与 `exclude.txt`：仅用于有证据的个人修正。

首版显式补充 `upos-bstar-mirrorakam.akamaized.net` 与 `upos-bstar1-mirrorakam.akamaized.net`。二者同时出现在 Sukka 与 BlackMatrix7 的国际版分类中；Sukka 明确标注后者涉及地区限制判断。

v2fly 的 `@!cn` 等属性不会转换为 Surge 路由动作。列表只负责识别产品流量，最终走 `DIRECT`、香港或台湾由策略组选择。

## 安全门槛

- 少于 45 条或丢失 `biliapi.com`、`bilibili.com`、`bilibili.tv`、`biliintl.com`、`bilivideo.com` 时停止构建。
- 任何删除都要求人工审核。
- 新增超过 3 条、真实变动超过 6% 或 Sukka 未覆盖数量增加时要求人工审核。
- 真实变动超过 20% 时停止构建，必须明确确认后才能重建。
- 输出文件末尾不写额外换行。

## Surge 顺序与策略

BiliBili 应位于 Sukka 全球流媒体总规则之前：

```ini
RULE-SET,https://raw.githubusercontent.com/schmidttt/Surge-Rules/main/rules/BiliBili/BiliBili.list,📺 BiliBili,update-interval=86400,extended-matching
RULE-SET,https://ruleset.skk.moe/List/non_ip/stream.conf,🎞️ 全球媒体,update-interval=86400,extended-matching
```

推荐策略组：

```ini
📺 BiliBili = select, ➡️ DIRECT, 🇭🇰 香港节点, 🇼🇸 台湾节点, 🧰 手动选择, hidden=0, icon-url=<沿用现有图标>
```

不放入 `🎯 自动选择` 或 `🌐 海外服务`，避免需要地区解锁时自动落到美国、日本等不合适出口。
