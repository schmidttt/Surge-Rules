# GoogleCN 审核清单

只需处理 `reports/googlecn/review.md` 中的“本次新增待确认”：

- 是否仍由 v2fly 明确标记为 `@cn`？
- 是否属于广告、统计、FCM、账号、Gemini、YouTube 或共享 API？
- 是否是精确主机，而不是会放大全球流量的父域？
- 直连后是否可能让登录、验证码或 Gemini 与 Google 主出口不一致？
- 是否有明确的下载、PKI 或中国专属域名职责证据？

结论：

- 明确安全：加入 `patches/googlecn/allow.txt`。
- 明确不需要直连：加入 `patches/googlecn/deny.txt`。
- 证据不足：保持隔离，不必修改补丁。

审核后重新运行构建器和全部验证。任何删除或硬排除变化仍需人工合并。
