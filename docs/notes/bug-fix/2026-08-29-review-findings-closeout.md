# 审查遗留项收口（CLI force / ADR-0031 / 部署核查）

Status: implemented
Class: bug-fix

## Decision

2026-08-29 周审查指出的可代码化遗留逐项闭合：

| 项 | 改动 |
|----|------|
| CLI/API `--force` 漂移 | `mtbf-cases.py` 删 `--force`；测试头注释同步 |
| ADR-0031 状态滞后 | 翻 Accepted v1.5；`docs/adr/README.md` + `CLAUDE.md` 决策表 |
| 部署协调无脚本 | 新增 `tools/dev/check-deploy-readiness.py`（只读 alembic + mtbf 绑定） |
| 部署清单缺口 | `production-minimum-deployment-checklist.md` §5.1 |
| AI T1 生产暴露面 | `.env.example` 注释补 ADR-0031 D1 评估提示 |
| #519 watcher-summary 半完成 | `log_observation.py` 模块 doc 登记 follow-up |

## 放弃的备选

- **恢复 export `force` API**：拒绝。#516 有意删除宽匹配逃生阀。
- **本批做 P2 套件管理前端**：拒绝。ADR-0030 P2 仍独立 issue，超出审查收口范围。

## 如何验证

- `python -m pytest backend/tests/tools/test_mtbf_cli.py backend/tests/api/test_mtbf_suite_routes.py -q`
- `./venv/bin/python tools/dev/check-deploy-readiness.py`（需 DATABASE_URL）

## 何时重议

- watcher-summary 改读 DLE（#519 UI 面）
- ADR-0030 P2 前端套件管理页
