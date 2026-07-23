# Game / GameCN 设计

## 内部模块与正式产物

构建器分别展开 Epic、PlayStation、Steam、Nintendo，报告中保留各平台
数量和异常；正式产物按最终策略目标合并：

- `Game.list` → `🎲 Gamer`
- `GameCN.list` → 固定 `DIRECT`

平台模块化不等于为每个平台发布一张表。当前不生成
`GameDownload.list`，海外游戏下载继续跟随 Gamer，避免网页、登录与
下载使用不一致的出口。

## GameCN 收紧

v2fly `@cn` 是候选来源：

- 中国平台注册根域保留 `DOMAIN-SUFFIX`。
- CDN、镜像和下载主机转换为精确 `DOMAIN`。
- 无法安全转换的正则不会放大为整个云厂商/CDN 后缀。

GameCN 中少量精确主机可能被 Game 的父域覆盖，因此 Surge 必须先引用
GameCN，再引用 Game。

## 上游边界

BlackMatrix7 Game 不作为正式源，避免引入过宽 IP 段、通用 Sony 域名、
第三方商店或其他无关服务。正式生成只使用 v2fly，人工补丁必须是小型、
可解释的精确例外。
