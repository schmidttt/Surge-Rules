# GoogleAI 与 AI 审核清单

- [ ] 确认 `reports/ai/ai-report.json` 的 v2fly 与 Sukka 提交均已固定。
- [ ] 确认 v2fly 仍是唯一正式生成来源，Sukka 仅有聚合审计数字。
- [ ] 确认 `GoogleAI.list` 指向 `🔍 Google`，`AI.list` 指向
      `🤖 Intelligence`。
- [ ] 确认 GoogleAI 位于 AI 之前，且两者均早于通用 Google。
- [ ] 确认 `change-assessment.json` 没有删除或异常大比例变化。
- [ ] 检查 `unsupported_omitted`；新增 regexp/keyword 不得静默放大。
- [ ] 检查 Sukka 未覆盖数量；数量增加时逐项确认，不直接合并。
- [ ] 确认 AI 没有 Google 所有权或中国大陆 AI 条目。
- [ ] 确认 `gemini.google.com`、`generativelanguage.googleapis.com`、
      `openai.com`、`anthropic.com`、`claude.ai` 等核心条目仍存在。
- [ ] 确认补丁有明确来源，只使用最小的 domain/full 范围。
- [ ] 运行全部离线测试和 `scripts/validate_repository.py`。
- [ ] 首版和所有非低风险变更只创建审核 PR，不自动合并。
