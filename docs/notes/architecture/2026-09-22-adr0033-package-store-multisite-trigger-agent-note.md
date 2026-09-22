# ADR-0033 v1.11：§5.4 多站点触发包存储

Status: implemented
Class: architecture

## Decision

用户裁定（2026-09-22）：**多站点部署 = ADR-0033 §5.4 触发**。本 PR 把该裁定写成
§5.4 **第 4 条**，评估结论锚改为**已触发**，撤销「触发前不得排期」，并开实现跟踪
[#3075](https://github.com/DUElost/stability-test-platform/issues/3075)。

**本 PR 只改文档 / ADR / 索引面**，不实现 Package Store 代码。

交付：

- ADR-0033 **v1.11**（§5.4 条件 4 + 头部落地状态 + §5.6 对账指针）
- 评估正本：`docs/notes/architecture/2026-09-22-adr0033-package-store-multisite-trigger.md`
- 索引：`docs/adr/README.md`、`docs/DOC-MAP.md`、`2026-storage-roles-and-aliases`、
  `2026-log-chain-global-semantics`
- 历史 09-20「未触发」评估加 supersession 指针（不改写当日结论）

### 明确不做

- `tar.gz` / `tools_cache` / manifest 注册流 / 周期巡检代码
- Phase 3 Jira 全迁、Web 工具管理面板
- 启用 auto-merge；关闭误开占位 #3074（无 issue 写权限，需人工）

## Alternatives

| 选项 | 为何不选 |
|---|---|
| 只在 issue 记裁定、不改 §5.4 原文 | 索引与评审仍会按「三条未触发」拦排期 |
| 等条件 1–3 再触发 | 与用户绑定决策冲突；多站点需要预防性分发 |
| 本 PR 做最小 tar.gz 证明切片 | 用户明确本轮 ADR + 设计 only |

## Verification

- ADR 正文条件表含第 4 条；评估文总判「已触发」
- 索引面不再写「包存储未触发 / 不排期」作为**当前**结论
- `python3 scripts/run_gates.py check:quick`
- governance surface（若适用）与 v1.11 一致

## Revisit

- #3075 第一切片批准并合入后：更新存储角色 `tools/` 现态行与日志链过渡例外表
- 误开 #3074（title=`test`）请人工关闭
