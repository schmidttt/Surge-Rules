# GoogleAI 与 AI 设计

## 目标

把 Google 自有 AI 和其他海外 AI 拆成两张互不覆盖的域名表：

- `GoogleAI.list` 固定交给 `🔍 Google`；
- `AI.list` 交给 `🤖 Intelligence`；
- 中国大陆 AI 只作为排除边界，不发布第三张表。

这样 Gemini 页面、Google 登录、验证跳转和 Google API 都使用同一个
Google 出口，不再要求用户手动让两个策略组保持相同节点。

## 正式来源

正式产物只从固定提交的 `v2fly/domain-list-community` 生成：

- GoogleAI：`google-deepmind`；
- 海外 AI 候选：`category-ai-!cn`；
- 国内排除边界：`category-ai-cn`；
- Google 所有权隔离边界：完整展开的 `google`。

Sukka `Source/non_ip/ai.conf` 只作只读聚合审计，不直接复制到产物。
这既吸收其按服务拆分和保持 Google/Gemini 同出口的经验，也避免把
混合的 `DOMAIN-KEYWORD`、`URL-REGEX` 或范围过宽主机自动带入配置。

## 生成边界

构建器按以下顺序处理：

1. 展开 `google-deepmind`，只保留可安全转换的 domain/full。
2. 展开 `category-ai-!cn`。
3. 排除任何与 `google-deepmind` 父子域覆盖的条目。
4. 排除任何与 `category-ai-cn` 父子域覆盖的条目。
5. 用完整 Google 集合隔离剩余 Google 所有权条目。
6. 省略 keyword/regexp，并逐条写入报告。
7. 应用人工 include/exclude 补丁，再重新执行所有硬门禁。

人工补丁不能绕过 Google、国内 AI 或两张产物互斥门禁。当前
`full:api.jetbrains.ai` 由 JetBrains 官方网络文档确认，只补精确主机，
不放大为整个 `jetbrains.ai` 后缀。

## Sukka 对照结论

Sukka 的 AI 表同时包含 OpenAI、Claude、Google AI、GitHub Copilot、
JetBrains AI 等多类服务，并为 Gemini 的 Google 验证跳转说明了同一
出口 IP 的必要性。本项目采用以下处理：

- Google AI 和通用 Google 都指向同一 `🔍 Google` 组；
- 不启用 `www.google.com` HTTPS MITM；
- 不复制依赖 MITM 的 Gemini `URL-REGEX`；
- `api.github.com` 因覆盖全部 GitHub API，不作为 AI 专用规则加入；
- 已被 v2fly 精确主机覆盖的 Google 条目不放大成后缀；
- 归属证据不足的短域名只保留在人工审计结论中。

参考：

- <https://github.com/v2fly/domain-list-community>
- <https://github.com/SukkaW/Surge/blob/master/Source/non_ip/ai.conf>
- <https://www.jetbrains.com/help/ai-assistant/disable-ai-assistant.html>

## 风险门禁

构建和仓库校验共同保证：

- GoogleAI 不少于 30 条，AI 不少于 100 条；
- 两张表的核心服务仍存在；
- GoogleAI 与 AI 没有精确或父子域覆盖；
- AI 不与完整 Google 或国内 AI 参考集合重叠；
- 不支持语法集合变化必须人工审核；
- Sukka 未覆盖数量增加或审计不可用时不能自动合并；
- 任何删除、过量新增或大比例变化都必须人工审核；
- 文件必须是 UTF-8、无重复规则、无末尾换行。

首版基线必须人工审核。完成试运行前保持
`AUTO_MERGE_LOW_RISK=false`。

## Surge 顺序

```ini
RULE-SET,<GoogleAI.list>,🔍 Google,no-resolve,extended-matching
RULE-SET,<AI.list>,🤖 Intelligence,no-resolve,extended-matching
RULE-SET,<YouTube.list>,📹 YouTube,no-resolve,extended-matching
RULE-SET,<GoogleCN.list>,DIRECT,no-resolve,extended-matching
RULE-SET,<Google.list>,🔍 Google,no-resolve,extended-matching
```

`🔍 Google` 应选择一个稳定的实际节点。若把它改为会自动漂移出口的
策略组，Gemini 验证过程中仍可能遇到前后 IP 不一致。
