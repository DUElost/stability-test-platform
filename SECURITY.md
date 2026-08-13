# Security Policy

## Supported versions

仅维护 `main` 分支的最新版本。已发布 tag 遇到安全修复时，以补丁 tag 形式发布。

## Reporting a vulnerability

请通过 GitHub 的 **Private vulnerability reporting** 提交：

仓库页 → Security → Report a vulnerability（私有，仅维护者可见）。

报告请尽量包含：

- 受影响版本 / commit；
- 复现步骤或最小复现；
- 影响评估（哪条链路、是否可被未授权触发）。

处理流程：

1. 3 个工作日内确认收到；
2. 修复走正常 PR 流程，公开前不披露细节；
3. 修复合并后以 advisory + 补丁 tag 公告，并致谢报告者（如你愿意署名）。

## Out of scope

- 上游依赖的 0day（由 Dependabot 告警与上游公告跟踪）；
- 公开仓库中已文档化的非敏感信息；
- 需要物理接触设备 / 已有主机权限的场景。
