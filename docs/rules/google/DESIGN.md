# 设计方案

## 1. 数据源和信任边界

### 正式上游：v2fly

正式规则只从 `v2fly/domain-list-community` 自动生成。这里的“正式上游”表示唯一写入产物的数据源，不表示它是 Google 官方清单或绝对准确的路由结论。构建器先通过 GitHub API 将分支解析为一个固定提交 SHA，再下载该提交的源码归档，因此一次构建不会混用不同时间点的文件。来源评价和 Sukka 对照见 [SOURCE_EVALUATION.md](SOURCE_EVALUATION.md)。

构建器支持 v2fly 当前的数据语法：

- 普通域名或 `domain:`：转换为 `DOMAIN-SUFFIX`；
- `full:`：转换为 `DOMAIN`；
- `include:`：递归展开，包括 `@attr` 和 `@-attr` 过滤；
- `@cn`、`@ads` 等属性；
- `&list` affiliation；
- 同名文件位于不同子目录时合并。

`keyword:` 和 `regexp:` 无法安全等价转换为本项目的 Surge `DOMAIN`/`DOMAIN-SUFFIX` 产物，因此会省略并逐条写入报告，不会静默消失。`@cn`、`@ads` 只作为上游属性保留和计数：只要规则类型可转换，域名仍进入 `Google.list`，本项目不据此决定 DIRECT 或 REJECT。

### 对照上游：BlackMatrix7

BlackMatrix7 的 `Google.list` 只用于报告：

- 双方共同条目数；
- 仅 v2fly 产物包含的条目数；
- 仅 BlackMatrix7 包含的域名类条目数；
- BlackMatrix7 中非域名规则类型的数量。

对照条目不会自动加入 `Google.list`。这样既避免把聚合源变成第二正式上游，也避免在未审核时引入 `DOMAIN-KEYWORD`、进程名或 User-Agent 规则。

### 只读审计：Sukka 与 Google 官方产品清单

构建器从同一个固定 Sukka 提交读取 `GLOBAL.GOOGLE` 和 `ai.conf` 的 Google 段，按以下优先级分类：

```text
Google AI → YouTube → 剩余 Google → needs_review
```

分类只以总量和各分类数量写入 `reports/google/reference-audit.json`，不会保存 Sukka 的具体规则条目，也不会写入规则产物。Sukka 的 `GLOBAL.GOOGLE` 服务其个人 DNS/global 逻辑，包含 YouTube 和特殊运行归类，因此不能作为全量 Google 真值表。

`references/google/official-core.txt` 来自 Google Cloud 和 ChromeOS 产品级官方文档。它只用于核心覆盖断言：任一断言无法被上述三层产品体系覆盖时，构建失败。它不是、也不会被描述为 Google 全生态完整列表。

## 2. 分类流程

```text
v2fly 完整 data 目录
       ↓ 解析 include / attribute / affiliation
google 根列表
       ├── google-deepmind（Gemini/Google AI）精确条目集合
       ├── youtube 精确条目集合
       ├── @ads / @cn 属性（保留域名、只做统计）
       └── 其他 Google 条目
```

正式产物定义：

### `rules/Google/Google.list`

由以下内容组成：

1. `google` 根列表展开结果；
2. 排除 `google-deepmind` 和 `youtube` 中出现的精确规则；
3. 保留可转换的 `@ads`、`@cn` 域名；
4. 省略无法等价表达的 keyword/regexp，并在公开报告中记录数量和类型；
5. 加入 `include.txt`；
6. 最后应用 `exclude.txt`，让显式排除具有最高优先级。

最终只发布这一份正式规则。广告拦截、GoogleCN 直连和其他网络策略由 Surge 主配置中位于它之前的规则负责。

## 3. 两个补丁文件

补丁使用 v2fly 风格的最小语法：普通域名、`domain:` 或 `full:`。不支持 `include:`、`keyword:` 和 `regexp:`。

### `patches/google/include.txt`

补充上游遗漏，统一进入 `Google.list`。补丁不用于表达 DIRECT/REJECT。

### `patches/google/exclude.txt`

按“规则类型 + 域名”从 `Google.list` 精确排除。若要排除整个后缀，应写 `domain:example.com`；若只排除一个主机，应写 `full:host.example.com`。

## 4. 为什么仍然需要规则顺序

从扁平规则文件中删除 `DOMAIN,gemini.google.com`，不能消除 `DOMAIN-SUFFIX,google.com` 对它的覆盖。因此三个产品集合只能做到“精确条目尽量分离”，最终边界仍由 Surge 首次命中顺序保证。

未来接入 Surge 时建议顺序为：

```ini
# 仅示意，不是本项目对现有配置的修改
RULE-SET,<Gemini规则地址>,<Gemini策略>,extended-matching
RULE-SET,<YouTube规则地址>,📹 YouTube,extended-matching
RULE-SET,<本项目Google.list地址>,<待确认策略>,extended-matching
```

`Google.list` 最终指向独立 `🔍 Google` 还是已有的海外策略，是后续配置设计决策；生成器不预设答案。

## 5. 失败保护

构建器会在写入前检查：

- 远程提交 SHA 是否可解析；
- 源码归档是否包含 `data/google`、`data/google-deepmind` 和 `data/youtube`；
- include 是否循环或指向缺失列表；
- 域名是否合法；
- 输出是否为空；
- 未支持的 keyword/regexp 数量和类型是否被记录；
- 核心后缀 `google.com`、`googleapis.com`、`gstatic.com` 是否仍在剩余 Google 中；
- Google 官方产品核心断言是否全部被三层产品体系覆盖；
- Sukka 参考项的分类总量和待审核数量是否异常；
- 与现有产物相比，条目数量是否变化超过阈值（默认 10%）。

首次生成时没有旧产物，因此跳过变化率检查。以后遇到超过阈值的变化，构建失败，旧文件保持不变；需要人工确认后使用 `--allow-large-change` 重新生成。

## 6. 同步节奏

### 观察期（两周）

- 每天北京时间 04:00 整触发；
- 定时任务由仓库变量 `ENABLE_SCHEDULED_SYNC=true` 才会真正执行；
- `SYNC_PHASE` 未设置或不等于 `stable` 时使用观察期节奏；
- 有变化时更新固定分支 `automation/google-rules-sync` 并创建/更新一个 PR；
- 无变化时不提交；
- PR 必须人工审核和合并。

### 稳定期

工作流同时声明观察期 cron：

```yaml
cron: "0 4 * * *"
```

以及稳定期 cron：

```yaml
cron: "0 4 * * 2,4,6"
```

两者都使用 `timezone: "Asia/Shanghai"`。两周观察结束后，只需把仓库变量设为 `SYNC_PHASE=stable`；工作流会跳过每天触发，只在周二、周四、周六执行。Surge 端以后仍可保留 `update-interval=86400`，它只负责每天检查远程文件是否变化。

## 7. 发布前仍需用户确认

1. 仓库名称、可见性和所有者；
2. 是否接受 v2fly 作为唯一正式源；
3. Gemini 规则最终使用 Sukka、v2fly 子表还是个人 override；
4. `Google.list` 最终指向哪个策略组；
5. 是否在观察期启用每天同步；
6. GitHub 仓库中是否允许 Actions 创建分支和 PR。
