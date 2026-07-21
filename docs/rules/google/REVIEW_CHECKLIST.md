# 审核清单

## 首次发布前

- [ ] 确认项目仍未修改任何 Surge 配置。
- [ ] 确认 `Google.list` 只以 v2fly 为正式源。
- [ ] 确认 BlackMatrix7 只以聚合数量出现在差异报告中，没有具体条目样本。
- [ ] 确认 Sukka 只以聚合数量出现在 `reference-audit.json`，没有具体条目或自动混入规则产物。
- [ ] 确认 Google 官方核心断言没有未覆盖项。
- [ ] 审阅 `include.txt` 与 `exclude.txt` 两个补丁文件。
- [ ] 运行 `python3 -m unittest discover -s tests/google -v`。
- [ ] 运行一次手动构建，查看 `Google.list` 和两份报告。
- [ ] 检查报告里的 unsupported 数量与类型；异常时回到上游固定提交人工核对。
- [ ] 抽查 `google.com`、`googleapis.com`、`gstatic.com` 仍在 `Google.list`。
- [ ] 抽查 Gemini/YouTube 的精确条目已从 `Google.list` 移出。
- [ ] 确认项目没有生成 GoogleCN、广告或其他路由策略文件。
- [ ] 确认 GitHub Actions 默认只允许最小权限。
- [ ] 确认定时同步变量默认未开启。

## 每次自动 PR

- [ ] 查看 v2fly 提交 SHA 和提交时间是否合理。
- [ ] 查看 `Google.list` 数量变化。
- [ ] 查看 Gemini、YouTube 精确排除数量以及 `@cn`、`@ads` 标记数量是否突变。
- [ ] 查看 BlackMatrix7 差异只作为参考，没有自动混入。
- [ ] 查看 Sukka `needs_review` 数量；数量变化异常时不要合并 PR。
- [ ] 查看 Sukka AI 的 unsupported 类型统计，特别是 URL-REGEX 和 DOMAIN-KEYWORD。
- [ ] 确认官方核心断言 `needs_review` 为 0。
- [ ] 查看是否出现新的 `keyword:` 或 `regexp:`。
- [ ] 查看是否删除了账号、认证、搜索和 API 核心域名。
- [ ] 查看补丁文件是否仍能应用。
- [ ] 测试通过后再人工合并。
