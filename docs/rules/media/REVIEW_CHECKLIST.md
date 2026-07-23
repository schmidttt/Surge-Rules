# YouTube / TikTok / BiliBili 同步审核清单

正常的少量纯新增可以由工作流自动合并。出现 `review-required` 时按以下顺序检查：

1. 查看 `change-assessment.json` 的 `reasons`、新增和删除数量。
2. 任何删除都核对 v2fly 对应提交，确认不是误删、改名或上游解析异常。
3. 核对核心域名仍存在，输出总数没有异常下降。
4. 查看 `reference-audit.json` 的 `verification.manual_review`；只有
   Sukka 与 BlackMatrix7 共同指出但仍未覆盖的差异需要人工判断。
5. 单一参考源独有条目会自动记录并结束；不要因此扩大产品范围。
6. 新增补丁前记录理由；优先依据实际抓包、产品官方资料或至少两个相互独立的来源。
7. 合并后手动运行一次对应工作流；无变化应不再创建新 PR。

不要直接把审计源差异批量复制到 `include.txt`。如果需要大规模改变范围，应先修改设计文档和安全门槛，再单独审核。
