# Security Policy

## Supported versions

| 版本 | 是否支持 | 说明 |
|------|----------|------|
| `main` 分支最新提交 | ✅ | 持续维护 |
| 最新补丁 tag | ✅ | 安全修复以补丁 tag 形式发布 |
| 历史 tag | ❌ | 建议升级到最新；仅对被实际使用的旧 tag 视情况回移修复 |

历史 tag 的支持期限为**该 tag 被新补丁 tag 取代即结束**；无长期维护（LTS）版本。

## Reporting a vulnerability

请通过 GitHub 的 **Private vulnerability reporting** 提交：

仓库页 → Security → Report a vulnerability（私有，仅维护者可见）。

报告请尽量包含：

- 受影响版本 / commit；
- 复现步骤或最小复现；
- 影响评估（哪条链路、是否可被未授权触发）。

处理流程：

1. 3 个工作日内确认收到；
2. 通过 **draft repository security advisory** 的 temporary private fork 协作修复；
   协调披露完成前**不创建公开 PR、不公开细节**；
3. 修复合并后以 advisory + 补丁 tag 公告，并致谢报告者（如你愿意署名）。

## Out of scope

- **上游依赖的 0day**：维护者不直接修复上游，但仍接受影响本仓库的私密报告并
  转交上游（同时由 Dependabot 告警与上游公告跟踪）；
- 公开仓库中已文档化的非敏感信息；
- **物理接触设备**，或报告者**已预先拥有目标机 root / 管理员权限**的场景。
  低权限账户提权、容器逃逸、跨主机横向等**在范围内**。
