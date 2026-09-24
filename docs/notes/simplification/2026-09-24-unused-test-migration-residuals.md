# 清理空 PBT 包与 ADR-0020 一次性迁移预检

Status: implemented
Class: simplification

## Decision

删除两个已无现行职责的历史残留：

- `backend/tests/pbt/__init__.py`：原属性测试已随旧状态机删除，目录只剩注释占位；
  `hypothesis` 也已因全仓零引用从开发依赖移除。同步更新依赖文件注释，不再保留空包。
- `backend/scripts/migration/preflight_adr_0020.py`：该脚本只服务 ADR-0020 从
  WorkflowDefinition / TaskTemplate 到 Plan / PlanStep 的一次性切换。当前旧模型与旧表已
  退场，脚本仍查询 `workflow_definition`、`task_template` 等历史 schema，继续作为可执行
  入口只会制造误导。

现行执行协议的迁移前检查仍由
`backend/scripts/migration/preflight_execution_protocol.py` 承担，本次不改变它。

## Alternatives

- 保留空 `pbt` 包等待未来复用：否。未来是否采用属性测试应由真实用例决定；空目录不提供
  契约或发现能力，反而暗示该测试层仍然存在。
- 给 ADR-0020 preflight 加 deprecated 标记后继续保留：否。它依赖已删除的表结构，无法对
  当前数据库给出有效结论；Git 历史已保留一次性迁移实现。
- 把脚本移入 `docs/archive/`：否。归档区承载决策与历史说明，不应存放看似可执行的生产库
  诊断工具。

## Verification

- 全仓引用检查确认没有现行代码、测试、CI 或运维文档调用被删脚本或 `pbt` 包；命中只剩
  本 note 的退役说明。
- `python -m pytest tests/test_requirements_dev_lock.py -q`：12 passed。
- `python -m pytest tests/ --collect-only -qq`：收集通过，且不再出现空
  `backend/tests/pbt` 包。
- `python scripts/run_gates.py check:quick`：16 gates 通过；隔离 worktree 未配置
  `DATABASE_URL`，`schema-at-head` 按设计跳过。

## Revisit

- 若重新引入 property-based testing，必须同时提交首个真实用例、恢复 `hypothesis` 开发依赖
  及 lock，而不是先恢复空目录。
- 若需要对历史 ADR-0020 数据迁移做取证，从 Git 历史读取脚本；不要把它恢复为当前运维入口。
