# MTBF 用例集 CLI（tools/dev/mtbf-cases.py）+ suite_unbound 观测层 + P1c 收口（#404 PR-E）

Status: implemented
Class: feature

## Decision

ADR-0030 P1c（issue #404 收尾批），三件事：

1. **CLI 定稿落地**：`tools/dev/mtbf-cases.py`（单文件 kebab-case，对齐
   `backfill-test-project.py` 先例）。与 backfill 工具的本质区别：它是
   **REST 便捷层而非直连 DB**（D4「REST 为主通道」）——与平台页面共用端点、
   权限与审计，CLI 的每个写操作在 audit_logs 里与页面操作无异。六个子命令
   （list/show/import/export/validate/export-to-tool-dir），套件以对外键
   `name` 引用；凭据三级回退（`--token` > ambient
   `STP_ADMIN_USER/STP_ADMIN_PASSWORD` > 仓库根 `.env.backend` 约定源），
   token 换发带 `X-Agent-Secret` 绕 CSRF（AGENTS.md「Production access」
   口径）；明文不进任何输出；退出码 0 成功 / 2 本地错误 / 3 远端拒绝。
2. **suite_unbound WARNING**（#404 第三步采纳项）：prepare 派发 mtbf 系脚本
   且 `plan.suite_id` 为空时记 WARNING（观测层信号，P0 存量兼容期提示 env
   回落仍在、绑定可进门禁）；翻转硬拒不默认启用，另起小 PR。
3. **文档收口**：mtbf-api.md §2 补 CLI 小节并把关键语义刷新为 PR-C 后现状
   （守卫精确化两档 409）；ADR-0030 v1.6 回写 CLI 位置定稿（设计 §4 明文
   要求的悬项关闭）+ 七挂靠位刷新。

## 放弃的备选

- **CLI 直连 DB**（backfill 先例）：拒绝。D4 定 REST 为主通道，直连会绕过
  审计与权限面，且 import/export 的解析渲染逻辑在服务层唯一实现，复制一份
  到客户端必然漂移。
- **CLI 自带 requests session 重试**：拒绝。运维工具失败可见优于静默重试
  ——import 是整体替换语义，重试可能掩盖第一次已成功的事实。
- **suite_unbound 记审计**：拒绝。它是调度观测信号不是管理动作；
  record_audit 的资源模型没有合适 resource_type，日志即可（翻转硬拒时
  自然升级为 admission fatal，有审计）。

## 如何验证

- `backend/tests/tools/test_mtbf_cli.py` ×9：凭据三级回退（含 `.env.backend`
  引号值解析）、无凭据 exit 2 且明文不出现在输出、套件 name 精确解析
  （缺失 exit 2）、export 落盘 + stale 提示、validate 失败 exit 3、409
  detail（SUITE_RUNS_ACTIVE）透传 exit 3。HTTP 层 monkeypatch 注入，不发真网。
- `test_suite_binding_gate.py` 增 2 例：未绑定 mtbf 派发记 `suite_unbound`
  WARNING、绑定派发不记。
- **真机只读冒烟**：对本机生产控制面执行 `list --include-inactive` →
  token 换发成功、返回空表（生产尚无套件，符合预期）；写路径未在生产执行。
- backend/tests 全量 1629 例通过（首跑 1 例 devices 排序偶发失败，单跑与
  api 目录复跑均绿，与本批改动无交集）、ruff 干净。

## 边界与何时重议

- D6 总验收信号剩**真机冒烟**一项：init trace `suite_sha256` == 门禁比对
  sha，需 fleet 在线窗口，由运营侧按验收模板执行后回写 ADR 修订记录；
- `X-Agent-Secret` 写权限开放（§7 #1）仍保守关闭，import/export 对 agent
  放开需评审定；
- `suite_unbound` 翻转硬拒：观察一个完整运行周期的告警量后另起小 PR；
- P2 前端（用例管理页 + `test_case_result`）独立立项。
