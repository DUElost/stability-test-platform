# stability-test-platform

共享启动契约：

@AGENTS.md

跨 Harness 的硬不变量由 `AGENTS.md` 统一承载。具体状态、参数、路径和操作步骤必须从
[`docs/DOC-MAP.md`](docs/DOC-MAP.md) 定位后按需读取。

## 按需读取

- 执行状态机和 Agent 终态协议：
  [`07-execution-protocol.md`](docs/design/07-execution-protocol.md)
- 存储角色与路径：
  [`2026-storage-roles-and-aliases.md`](docs/design/2026-storage-roles-and-aliases.md)
- 脚本目录、参数和退役：
  [`script-versioning.md`](docs/development/script-versioning.md)
- 环境变量：
  [`environment-variables.md`](docs/development/environment-variables.md)
- ADR 状态：
  [`docs/adr/README.md`](docs/adr/README.md)
- Harness 加载边界：
  [`harness-adapters.md`](docs/development/ai/harness-adapters.md)

修改具体领域时，继续读取目标目录内的 scoped `CLAUDE.md` 和对应设计文档；不要把
领域细节重新复制回本文件。
