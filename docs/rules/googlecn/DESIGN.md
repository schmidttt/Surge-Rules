# GoogleCN 设计

## 目标

GoogleCN 是一张固定 `DIRECT` 的保守规则表，不是“所有 Google `@cn`”
的镜像。若某个条目需要切换国家或代理，它就不应进入 GoogleCN。

## 自动审查

生成器展开 v2fly `google`，只选带 `@cn` 的候选，然后依次执行：

1. 排除 `@ads`、`@!cn` 和无法表达的规则。
2. 排除 Google AI、YouTube 与 Google FCM 覆盖项。
3. 排除广告统计、遥测、账号和共享应用风险项。
4. 自动批准中国专属根域名和显式维护的精确技术主机。
5. 其余条目隔离到审查报告，不发布。

新的隔离条目阻止自动合并；未变化的历史隔离条目不会重复阻断。人工
`allow` 只能处理模糊项，不能绕过硬排除。

## DOMAIN 与 DOMAIN-SUFFIX

- 明确的中国专属注册域可以使用 `DOMAIN-SUFFIX`。
- 全球共享 Google 主机只能使用精确 `DOMAIN`。
- 不把单个下载、证书、字体或更新主机放大成父域后缀。

## 不采用大陆网络探测作为门禁

GitHub Actions 运行位置、DNS 线路和 CDN 调度不代表用户的中国大陆
网络。探测可验证 URL 可获取，但不能证明某个 Google 主机长期适合
直连。分类证据来自上游语义、服务职责、冲突检查和显式策略。

## Surge 顺序

推荐顺序为广告、GoogleAI、AI、YouTube、GoogleCN、Google。GoogleCN
固定 `DIRECT`，GoogleAI 与 Google 通用表都使用 `🔍 Google`。
