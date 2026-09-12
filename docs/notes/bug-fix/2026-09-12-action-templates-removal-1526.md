# action_templates 死代码删除闭环（#1526）

Status: implemented
Class: bug-fix

## Decision

ADR-0020 Phase 6 删除「工作流编辑器」概念后，其配套的 ActionTemplate
资源（模板/端点/类型）未进 Phase 7 强制 grep 清单——**代码·测试·文档三者
共同维持一个已退场子系统的假象**：后端完整 CRUD 路由生产真实注册
（`require_admin` 写面）、有测试文件制造安全感；前端 0 消费（唯一"调用"
是 `toBeDefined()` 断言）；设计文档与 ADR 仍标注「仍活跃」。

按 issue 清单删除（全仓引用已逐处核对）：

- **后端**：`routes/action_templates.py`、`models/action_template.py`、
  `tests/api/test_action_templates.py` 删除；`models/__init__.py`（import +
  `__all__`）、`main.py`（import + `include_router`）去注册；
  **漏网三处随验证补删**：`tests/conftest.py`、`scripts/init_dev_db.py`、
  `alembic/env.py` 的 `import backend.models.action_template`（模型元数据
  注册用途；收集期 / `pr-migrate-empty-db` ImportError 暴露，非 issue 原清单）；
- **前端**：`tools.ts`（import 类型 + `actionTemplates` 封装）、`types.ts`
  （三个 interface）、`index.ts`（4 处聚合）、`api.test.ts`（两行断言）；
- **文档**：`02-backend.md` 端点表删行；`ADR-0007` 活跃清单删两行、
  「仍活跃」行改「已删除（#1526）」。

**未 drop `action_template` 表**：迁移 `f4a5b6c7d8e9` 保留（历史不可改），
表级删除需先确认生产数据无依赖（issue 明示前置）——独立后续动作
（Revisit）。

## Alternatives

- **保留路由仅加 deprecation 标注**——放弃：零消费 + 概念已退场，保留即
  维持假象与 `require_admin` 无用攻击面；
- **连带 drop 表迁移**——放弃：删除型 ADR 的数据边界需生产数据确认，
  schema 层动作与代码删除解耦（可独立评审）。

## Verification

- 残留检查：`grep -rn "action_template\|ActionTemplate\|actionTemplates"`
  全仓仅剩 alembic 历史迁移与 ADR 的「已删除」标注（`env.py` 侧效应
  import 已随 `pr-migrate-empty-db` 失败补删）；
- `from backend.main import fastapi_app` 成功（路由注册移除后 34 条路由）；
  `pytest backend/tests --collect-only` **2285 tests collected**（首次暴露
  并修复 conftest/init_dev_db 漏网引用后无错）；
- CI 补丁验证：`backend/models/action_template.py` 确认已删；`env.py`
  不再 import 该模块；`python3 -m py_compile backend/alembic/env.py` 通过
  （本机无 sqlalchemy 时以 CI `pr-migrate-empty-db` 为最终门禁）；
- `backend/tests/api` 全量 **1019 passed**（7m29s）；
- 前端 `api.test.ts` **16 passed**；`check:quick` 7 门禁全绿（tsc/knip 对
  删除后的导出面干净）。

## Revisit

- `action_template` 表的生产数据确认与 drop（含 schema-sync 基线的再收敛）
  ——独立动作，需生产只读核对后另单；
- issue 的流程改进建议（方向级「删除概念」ADR 的 Verification 清单应覆盖
  「被删概念的配套资源」）属 ADR 模板演进面，不在本单。
