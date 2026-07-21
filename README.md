# Surge-Rules

为 Surge 构建可审核、可回退、可独立维护的个人规则库。仓库采用多规则项目结构；每类规则分别维护生成脚本、补丁、报告、测试和同步工作流，避免不同规则之间相互耦合。

当前只实现 Google 规则。私有仓库已完成首次上传和 GitHub Actions 手动验证；尚未修改或覆盖任何 Surge 配置。

## 规则项目

| 规则 | 状态 | 正式上游 | 正式产物 |
|---|---|---|---|
| Google | 本地审核 | `v2fly/domain-list-community` | `rules/Google/Google.list` |

未来新增其他规则时，应建立独立的 `rules/<Name>/`、`scripts/<name>/`、`patches/<name>/`、`reports/<name>/`、`tests/<name>/` 和对应工作流；不能让某一规则的补丁或发布流程隐式影响其他规则。

## 当前 Google 目标

- 以 [`v2fly/domain-list-community`](https://github.com/v2fly/domain-list-community) 为唯一用于自动生成的正式上游；它是社区数据源，不是 Google 官方域名清单。
- 以完整 Google 根集合为起点，内部识别 Google AI 与 YouTube，正式只输出剩余 `Google.list`。
- BlackMatrix7 只用于差异统计，Sukka 只用于分类审计；两者都不会自动合并，公开报告也不保存其具体规则条目。
- 用 Google 官方产品文档整理的小型核心断言检查明显缺失；该断言不冒充 Google 全生态清单。
- 使用 `include.txt` 和 `exclude.txt` 处理个人例外，不手工维护整张大表。
- 保留 v2fly 的 `@cn`、`@ads` 可转换域名；DIRECT/REJECT 由以后调整 Surge 规则顺序时决定。
- 输出标准 Surge `RULE-SET`，并在每次同步时生成可审查报告。
- GitHub Actions 有变化时只创建或更新审核 PR，不直接修改默认分支。

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
- 无变化不提交；有变化只创建或更新审核 PR，不自动合并。

## 发布边界

- 当前不修改 Surge v36 或其他 Surge 配置。
- 当前不启用定时同步变量。
- 当前不自动合并审核 PR。
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

这些数字只是当前上游快照。后续同步若总量变化超过 10%，构建会停止并等待人工确认。
