# 第三方来源与许可证

本项目自己的脚本、测试和文档采用仓库根目录中的 MIT `LICENSE`。第三方数据不因被本项目处理而改变其原有许可证。

## v2fly/domain-list-community

- 地址：https://github.com/v2fly/domain-list-community
- 许可证：MIT。
- 用途：Google、YouTube、TikTok 三类规则的唯一正式数据上游。
- 使用方式：读取固定提交中的 `data` 目录，按其 include、属性和 affiliation 语义生成 Surge `Google.list`、`YouTube.list` 和 `TikTok.list`。
- 署名与许可证：生成文件头保留来源、提交 SHA、版权与许可证提示；MIT 全文保存在 `THIRD_PARTY_LICENSES/v2fly-MIT.txt`。

## blackmatrix7/ios_rule_script

- 地址：https://github.com/blackmatrix7/ios_rule_script
- 上游许可证：GPL-2.0，并附有其项目自己的使用声明。
- 用途：构建时在线读取 Google、YouTube、TikTok 对应规则，只计算双方总量、交集数量、差异数量和非域名规则类型数量。
- 持久化边界：不自动合并，不在规则、报告或测试夹具中保存 BlackMatrix7 的具体规则条目。

## SukkaW/Surge

- 地址：https://github.com/SukkaW/Surge
- 上游许可证：除其特别说明的文件外为 AGPL-3.0。
- 用途：Google 构建读取 `GLOBAL.GOOGLE` 和 `ai.conf` Google 段；YouTube、TikTok 构建读取 `Source/stream.ts` 对应服务段，仅进行覆盖分类统计。
- 持久化边界：不自动合并；公开报告只保留来源提交、总量、分类数量和 unsupported 类型数量，不保存 Sukka 的具体规则条目或原始规则行。

## Google 官方产品文档

- Google Cloud 必需域名：https://docs.cloud.google.com/docs/get-started/required-domains
- ChromeOS 主机名允许列表：https://support.google.com/chrome/a/answer/6334001
- 用途：维护少量产品级核心覆盖断言，防止账号、认证和 API 等基础域名明显遗漏。
- 边界：这些资料不被描述为 Google 全生态完整域名表，公开报告只保存聚合覆盖结果。

## 发布原则

- 正式 `Google.list`、`YouTube.list` 和 `TikTok.list` 只由 v2fly 数据和各自显式补丁生成。
- BlackMatrix7 与 Sukka 仅作为在线、只读、聚合统计参照。
- 测试夹具使用合成 `.example` 数据模拟第三方格式，不复制第三方规则片段。
- 新增任何正式上游前，必须先确认许可证兼容性并更新本文件。
