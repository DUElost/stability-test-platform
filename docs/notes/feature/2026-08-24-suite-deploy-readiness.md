# #404 部署就绪核验（只读）+ 真机验收 runbook

Status: implemented
Class: feature

## Decision

#404 B/C/D/E 四批合入后的收尾批：把「剩余两项非代码事项」（真机冒烟、
suite_unbound 硬拒翻转）中**能提前做的部分**做掉——生产侧只读核验 +
实机验收 runbook，让运营窗口打开时照单执行即可。

1. **只读核验**（本机即生产机的优势，全程零写操作）：
   - 生产 DB `alembic_version = v8w9x0y1z2a3` == PR-B 迁移 == repo 链一致
     （事故前向修复闭环的最终确认）；`plan.suite_id` 列在位；
   - 生产 backend 服务跑 8-22 旧代码（9 个 suite 端点在、`PlanCreate.suite_name`
     不在）→ **PR-B/C/D/E 需部署窗口重启激活**——这是真机冒烟的唯一硬前置，
     已写进 runbook §0 复核表；
   - `STP_SCRIPT_ROOT` 即本仓库 `backend/agent/scripts` → main 合入后新脚本
     版本目录随部署自然可见，catalog 扫描注册即可，无需带外同步；
   - fleet 抽样 3/3 台 host `.env` 存在 `STP_MTBF_EXPECTED_TESTPOINT_COUNT`
     残留行——印证 PR-D 分析（merge_env_overrides 只增改不删）；绑定 Run
     注入优先不受影响、v1.3.0 忽略、≤v1.2.0 行为同退役前，清理为可选。
2. **runbook**：`docs/acceptance/2026-08-suite-binding-mtbf-signoff.md`
   （对齐 #72 AEE signoff 先例体例）：部署复核表 P1–P5 + 七步验证矩阵
   （冻结/门禁放行/注入/**suite_sha256 总信号**/审计/守卫反向/门禁反向）
   + R1–R4 判据 + 实测填空。数据准备全程走 CLI，即 D6「外部 agent 仅凭
   API/CLI」口径的现场演练。

## 放弃的备选

- **直接在生产执行冒烟**：拒绝。派发真机 Run 属破坏性写操作，超出 AGENTS.md
  「手工 API 冒烟可连控制面但避免破坏性写」的授权边界；且生产服务尚未重启到
  新代码，现在跑不出被验收的行为。
- **顺手清理 fleet 残留 env 行**：拒绝。20+ 台手工 SSH 写 .env 违背变更纪律，
  收益趋零（三档影响分析均无害）；留给下次 host 维护窗口顺带处理。
- **把 suite_unbound 硬拒翻转一起做**：拒绝。issue 口径要求先观察一个完整
  运行周期的告警量，条件未触发。

## 如何验证

- 核验命令全部只读（psycopg SELECT / openapi.json GET / ssh grep）；
- runbook 为模板文档，无代码路径；docs 链接经 acceptance/README.md 索引收口。

## 边界与何时重议

- 部署窗口（git pull + 重启 backend + catalog 扫描注册 mtbf_check v1.3.0）
  由运营执行；runbook §0 的 P3/P4 是重启后第一步复核项；
- 多 alembic head（l2m3n4o5p6q7 / w1x2y3z4a5b6）为历史存量分支点，与本批
  无关、生产版本锚定正确；若未来迁移工具化时再统一收敛；
- 真机冒烟完成后：runbook 回填实测值 → issue #404 关单 → ADR-0030 修订记录
  补 D6 达成行（对齐 v1.2 P0 先例）。
