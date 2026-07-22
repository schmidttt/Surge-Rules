# Surge-Rules

为 Surge 构建可审核、可回退、可独立维护的个人规则库。仓库采用多规则项目结构；每类规则分别维护生成脚本、补丁、报告、测试和同步工作流，避免不同规则之间相互耦合。

当前只实现 Google 规则。公开仓库已完成首次发布和 GitHub Actions 手动验证；尚未修改或覆盖任何 Surge 配置。

## Google.list 的范围与用法

[`rules/Google/Google.list`](rules/Google/Google.list) 是面向 Surge 的 Google 通用兜底规则。它以 v2fly `google` 根集合为唯一正式源，先排除上游中可明确识别的 Google AI / Gemini 与 YouTube 精确产品条目，再输出其余 `DOMAIN` 和 `DOMAIN-SUFFIX` 规则。

需要注意：这不是 Google 官方域名清单，也不是与 Gemini、YouTube 完全互斥的数学集合。`DOMAIN-SUFFIX,google.com`、`DOMAIN-SUFFIX,googleapis.com` 等父级规则仍可能覆盖产品子域，因此必须依靠 Surge 从上到下、首次命中即停止的规则顺序完成最终分流。

推荐顺序：

```ini
# 广告与跟踪规则应放在更前面
RULE-SET,<你的 Google AI / AI 规则地址>,🤖 Intelligence,...
RULE-SET,<你的 YouTube 规则地址>,📹 YouTube,...
RULE-SET,<你当前使用的 GoogleCN 规则地址>,DIRECT,...
RULE-SET,https://raw.githubusercontent.com/schmidttt/Surge-Rules/main/rules/Google/Google.list,🔍 Google,update-interval=86400,extended-matching
```

- `🤖 Intelligence` 先接管 Gemini、AI Studio、Google AI API 等已识别的 Google AI 流量。
- `📹 YouTube` 先接管 YouTube 页面、接口和视频相关流量。
- `GoogleCN` 如继续使用，应在本列表之前处理可直连的 Google 静态资源。
- 本列表最后接管其余 Google 域名；它本身不决定 `DIRECT`、`REJECT` 或具体代理节点。
- 切换时应替换原有广义 `Google.list` 规则，不要与旧 Google 大表并列重复引用。
- v2fly 的 `@cn`、`@ads` 来源属性不会写入 Surge 规则行；相关直连和拦截仍由前置 GoogleCN、广告规则负责。

## 规则项目

| 规则 | 状态 | 正式上游 | 正式产物 |
|---|---|---|---|
| Google | 自动同步、风险分级 | `v2fly/domain-list-community` | `rules/Google/Google.list` |

未来新增其他规则时，应建立独立的 `rules/<Name>/`、`scripts/<name>/`、`patches/<name>/`、`reports/<name>/`、`tests/<name>/` 和对应工作流；不能让某一规则的补丁或发布流程隐式影响其他规则。

## 当前 Google 目标

- 以 [`v2fly/domain-list-community`](https://github.com/v2fly/domain-list-community) 为唯一用于自动生成的正式上游；它是社区数据源，不是 Google 官方域名清单。
- 以完整 Google 根集合为起点，内部识别 Google AI 与 YouTube，正式只输出剩余 `Google.list`。
- BlackMatrix7 只用于差异统计，Sukka 只用于分类审计；两者都不会自动合并，公开报告也不保存其具体规则条目。
- 用 Google 官方产品文档整理的小型核心断言检查明显缺失；该断言不冒充 Google 全生态清单。
- 使用 `include.txt` 和 `exclude.txt` 处理个人例外，不手工维护整张大表。
- 保留 v2fly 的 `@cn`、`@ads` 可转换域名；DIRECT/REJECT 由以后调整 Surge 规则顺序时决定。
- 输出标准 Surge `RULE-SET`，表头随采用的 v2fly 提交时间和规则总数更新。
- 每次同步分别统计新增、删除和真实变动率；低风险更新可自动合并，任何删除和可疑变化保留 PR。

## 目录

```text
Surge-Rules/
├── .github/workflows/
│   └── sync-google-rules.yml
├── docs/rules/google/
│   ├── DESIGN.md
│   ├── SOURCE_EVALUATION.md
│   └── REVIEW_CHECKLIST.md
├── patches/google/
│   ├── include.txt
│   └── exclude.txt
├── references/google/
│   └── official-core.txt
├── reports/google/
│   ├── google-report.json
│   ├── change-assessment.json
│   └── reference-audit.json
├── rules/Google/
│   └── Google.list
├── scripts/google/
│   └── build_google_rules.py
├── tests/google/
│   ├── fixtures/
│   └── test_build_google_rules.py
├── THIRD_PARTY_LICENSES/
│   └── v2fly-MIT.txt
├── LICENSE
├── README.md
└── THIRD_PARTY.md
```

## Google 数据流

```text
v2fly Google 根集合（唯一正式源）
  ├── Google AI / Gemini 精确产品条目：由前置 AI 规则负责
  ├── YouTube 精确产品条目：由前置 YouTube 规则负责
  └── 剩余 Google 生态：rules/Google/Google.list

BlackMatrix7 + Sukka + Google 官方产品断言
  └── 只生成聚合统计与覆盖审计，不保存具体第三方规则条目
```

`DOMAIN-SUFFIX,google.com` 等父级规则仍可能覆盖 Gemini 或 YouTube 子域。因此产品分流最终必须依赖 Surge 的首次命中顺序，不能仅靠物理删除显式条目实现完全互斥。详细设计见 [`docs/rules/google/DESIGN.md`](docs/rules/google/DESIGN.md)。

## 本地验证

运行离线测试：

```bash
python3 -m unittest discover -s tests/google -v
```

从当前上游构建：

```bash
python3 scripts/google/build_google_rules.py --fetch
```

构建器会先把产物写入临时目录，全部校验通过后才替换 `rules/Google/` 和 `reports/google/` 中的文件。下载、解析或数量变化保护失败时，现有产物不会被覆盖。

## 同步节奏

- 观察期两周：每天北京时间 04:00 整触发。
- 稳定期：每周二、周四、周六北京时间 04:00 整触发。
- 定时任务默认不执行构建；明确设置仓库变量 `ENABLE_SCHEDULED_SYNC=true` 后才启用。
- 默认阶段为观察期；两周后设置 `SYNC_PHASE=stable` 即切换到稳定期。
- 无变化不提交；有变化先创建 PR 留痕。
- 设置 `AUTO_MERGE_LOW_RISK=true` 后，仅“无删除、新增不超过 20 条、真实变动不超过 2%、不支持语法集合未变化”的更新自动合并。
- 任何删除、超出低风险门槛或不支持语法变化都会保留 PR；真实变动超过 10% 或核心检查失败时构建直接停止。

## 发布边界

- 当前不修改 Surge v36 或其他 Surge 配置。
- 定时同步与低风险自动合并都由独立仓库变量显式控制，随时可以关闭。
- 当前不生成 GoogleCN、广告或其他路由策略文件。
- 项目脚本采用 MIT；v2fly 的 MIT 许可证全文和署名独立保留。
- Sukka 与 BlackMatrix7 只在线读取并生成聚合统计，不在公开报告和测试夹具中转载其具体规则。

仓库公开后，Google 规则的远程地址将是：

```text
https://raw.githubusercontent.com/schmidttt/Surge-Rules/main/rules/Google/Google.list
```

## 本地审核基线（2026-07-21，Asia/Shanghai）

- v2fly 固定提交：`462dc5706f1d578a2135e8f5dfbdade511094fa7`
- `Google.list`：890 条
- v2fly `google` 展开：1110 条
- Google AI / YouTube 精确产品条目排除：218 条
- `@cn` 标记：126 条；`@ads` 标记：53 条，相关可转换域名仍保留在 `Google.list`
- Surge 无法等价表达的正则：2 条，已省略并仅报告数量与类型
- BlackMatrix7 域名类对照：685 条；共同 613 条；自动合并 0 条
- Sukka `GLOBAL.GOOGLE`：98 条；只读分类，自动合并 0 条
- Google 官方核心断言：15 条，当前全部被三层产品体系覆盖

这些数字只是当前上游快照。后续同步若实际新增与删除总量超过原列表 10%，构建会停止并等待人工确认。
