# ADR-0030 P2 文档传播 + 部署 runbook v2.5 附录

## 决定了什么

- ADR-0030 推进至 **v1.9**：P2 核心（#429 套件 UI + `test_case_result`/`TestCaseResultsCard`）记账为已落地。
- 七挂靠位同步：ADR 头部/修订记录、adr README 清单+M7、CLAUDE.md、DOC-MAP Living 表。
- `2026-08-29-post-review-deploy-runbook.md` 增补 **§7**（ADR-0029 v2.5 D10 M1–M4、G15 catalog、OpenRouter/P2 前端重建、head `j0k1l2m3n4o5`）。
- `production-minimum-deployment-checklist.md` §5.1 指向 §7 并补前端重建勾选项。

## 放弃的备选

- 另开 `2026-09-01-deploy-runbook.md`：与 8 月 runbook 大量重复，改为同文档 §7 增量附录。

## 仍开放

- JobArtifact `report` 类型白名单（ADR-0030 §实施影响 原 P2 悬项，大文件下载场景）。

## 如何验证

- 文档 grep：`仅余 P2` / `P2 前端与 test_case_result` 在 ADR-0030 挂靠位应归零（除历史修订行「仍未做」原文）。
- 部署侧：`check-deploy-readiness.py --expect-revision j0k1l2m3n4o5` 在控制面主机只读通过。

## 何时重议

- #429 后续块（artifact 白名单）合入时再 bump ADR-0030 修订记录。
