# 来源评估：v2fly 与 Sukka

## 结论

`v2fly/domain-list-community` 适合继续作为本项目**唯一用于自动生成的正式上游**，但应准确理解为：

> V2Fly/V2Ray 生态中成熟、结构化、可审查的社区域名数据库，而不是 Google 官方发布的完整域名清单，也不是可以直接决定代理/DIRECT 的权威路由表。

Sukka 的规则适合成为第二只读参考，尤其适合检查 Gemini、Google AI 和分流理念；不适合直接替代 v2fly 作为广义 Google 数据主源，也不自动合并。

## 1. v2fly 为什么值得作为主源

### 项目归属和采用范围

- 仓库属于 V2Fly 官方 GitHub 组织，并被 V2Ray 文档用于 `geosite` 域名分类。
- 项目历史长、提交和发布数量多，仍持续接受 Issues 和 Pull Requests。
- 旧的 `v2ray/domain-list-community` 已把日常维护迁移到 v2fly，理由是增加可参与审核的维护者，让项目更活跃。

参考：

- https://github.com/v2fly/domain-list-community
- https://github.com/v2ray/domain-list-community
- https://github.com/v2ray/manual/blob/master/en/configuration/routing.md

### 维护流程不是无审核聚合

v2fly 的贡献说明要求：

1. 改动通过 Pull Request 提交；
2. 必须由另一名成员审核并批准；
3. 自动化脚本验证语法和生成结果；
4. 只有测试通过的 PR 才能合并。

它的 `include`、属性和 affiliation 语法也有明确生成语义，比直接抓取一张扁平文本表更适合我们识别 Google、Gemini 与 YouTube，同时保留 `@cn`、`@ads` 来源信息。

### Google 数据有部分可追溯来源

`data/google` 中部分内容直接注明来源，例如：

- Google 各地区搜索域名来自 Google 的 supported domains 页面；
- Google Play、DeepMind/Gemini、YouTube 等通过独立子表组合；
- 广告条目标记为 `@ads`；
- 中国大陆接入候选标记为 `@cn`，其中部分来自 `dnsmasq-china-list`。

这让我们可以保留完整 Google 基线，按产品子表做可解释的分离，并将属性留给 Surge 配置层决定路由。

## 2. v2fly 不能被称为“Google 官方权威表”

v2fly 自己明确说明项目是 non-opinionated：它只管理域名分类，不保证某个域名应该代理、直连或拦截。

具体局限包括：

- 域名由社区贡献，可能遗漏新服务，也可能保留历史业务；
- Google 产品大量共享 `google.com`、`googleapis.com`、`gstatic.com` 等父域，无法做到数学意义上的完全互斥；
- `@cn` 只表示社区认为存在中国大陆接入点，不等于所有运营商、IPv4/IPv6 和时间段都稳定；
- Google 表包含正则、TLD 和收购业务，转换到 Surge 时必须进行兼容性和范围审核；
- 数据库覆盖广不等于实际账号风控一定更稳。

因此本项目使用固定提交、差异报告、10% 变化阈值和人工 PR，而不是直接追踪 `master` 后自动发布。

## 3. 当前静态效果核验

2026-07-21 的本地基线：

- v2fly `google` 完整展开：1110 条；
- 清洗后的剩余 `Google.list`：890 条；
- Gemini/YouTube 精确条目排除：218 条；
- `@cn` 标记 126 条、`@ads` 标记 53 条；可转换域名保留，仅做数量审计；
- BlackMatrix7 域名类规则：685 条；
- 本项目与 BlackMatrix7 完全相同的域名类规则：613 条；
- 仅 BlackMatrix7：72 条；
- 仅本项目：277 条。

双方有较大交集，说明 v2fly 生成结果不是偏离主流的孤立列表；差异也足够大，说明不能把任何一个社区表当成绝对真值。

目前验证属于：

- 结构解析验证；
- Surge 格式验证；
- 多来源静态差异验证；
- 可重复构建验证。

尚未完成的“实际效果”是把新列表接入 Surge 后，用真实抓包观察遗漏、误匹配和账号相关流量。当前项目仍未修改 Surge，因此不能把静态检查描述为已完成实网验证。

## 4. Sukka 有没有类似参考

有，但不是一份可以直接替换的独立 Google.list。

### `Source/non_ip/global.ts` 的 Google 人工集合

Sukka 在 `GLOBAL.GOOGLE` 中维护了一份较精简的 Google 域名集合，包括：

- `google.com`、`googleapis.com`、`gstatic.com`；
- Gmail、Firebase、GVT、Google TLD；
- YouTube、`youtu.be`、`ytimg.com`；
- 多个 Google 收购或关联业务。

但该对象标记为 `ruleset: false`，并且 YouTube 仍混在 Google 集合中。它主要服务 Sukka 自己的 global/DNS 生成逻辑，不是“Gemini + YouTube 被抽走后的剩余 Google”成品。

参考：https://github.com/SukkaW/Surge/blob/master/Source/non_ip/global.ts

### `Source/non_ip/ai.conf` 的 Google AI 规则

Sukka 把 Gemini、AI Studio、Generative Language API、NotebookLM、Jules、Antigravity 等放进 AI 规则，并对 `www.google.com/sorry/...gemini...` 的 IP 一致性验证给出了专门处理逻辑。

这对我们判断 Gemini 边界和 Google 风控很有价值，也支持“Gemini 先匹配、剩余 Google 后兜底”的结构。

参考：https://github.com/SukkaW/Surge/blob/master/Source/non_ip/ai.conf

### Sukka 对中国大陆直连数据的警告

Sukka 明确批评把 `dnsmasq-china-list` 直接当作流量分流依据：该项目最初面向 DNS 解析优化，只检查权威 DNS 等信号，可能把不适合 DIRECT 的海外域名纳入。

而 v2fly 的部分 Google `@cn` 条目正注明来自 `dnsmasq-china-list`。因此本项目不根据 `@cn` 生成直连规则，只保留域名和属性统计；是否 DIRECT 由 Surge 主配置独立判断。

参考：https://blog.skk.moe/post/i-have-my-unique-surge-setup/

## 5. 推荐的数据治理结构

```text
唯一自动生成主源
└── v2fly/domain-list-community

自动差异对照
└── BlackMatrix7 Google.list（已实现，不自动合并）

自动只读审计
├── Sukka GLOBAL.GOOGLE
└── Sukka ai.conf / Google AI 特殊规则

官方核心断言
└── Google Cloud / ChromeOS 产品级允许列表

个人修正
├── patches/google/include.txt
└── patches/google/exclude.txt
```

这样可以避免多上游无脑合并，同时吸收 Sukka 的产品分类和风控经验。

## 6. 当前建议

1. 暂时保留 v2fly 为唯一自动生成主源。
2. 不把“正式上游”写成“Google 官方”或“绝对权威”。
3. BlackMatrix7 继续自动做差异报告。
4. Sukka 已加入自动只读差异报告，只保存聚合统计，不保存具体规则条目，也不自动合并。
5. 不生成 GoogleCN 或广告策略产物；`@cn`、`@ads` 只作为元数据审计。
6. `reference-audit.json` 只公开 `needs_review` 数量；数量异常时回到对应固定提交人工核对，未判断的差异不合并。
