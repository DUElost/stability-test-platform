# #2741 audit_logs 分层保留期裁剪落地（ADR-0049 首版实现）

Status: implemented
Class: feature

## Decision

owner 2026-09-19 拍板（四问全采推荐项，裁决记录在 #2741 评论 + ADR-0049 §2），
按「先裁决后实现」一次落地：

1. **分层保留期**（ADR-0049 D1）：`security 180d / business 90d / session 30d`，
   env 可调（`AUDIT_LOG_{SECURITY,BUSINESS,SESSION}_RETENTION_DAYS`）。安全集 =
   认证成败、账号/凭据管理、`host_key_replaced` 等安全事件链；会话集 =
   `refresh`/`login` 成功/`logout`；business 是 **NOT IN 默认桶**（对新 action
   封闭，无需登记即自动获得 90d 保留网）。`token_issued` 归 security（与
   #2694 的 dev 观测分组不同——它记录「谁取得了凭据」，账号失陷调查要用）。
2. **裁剪作业**（D4）：`backend/scheduler/audit_log_cleanup.py`——APScheduler
   单例 sync 作业（默认 1h，`AUDIT_LOG_RETENTION_INTERVAL_SECONDS=0` 停用），
   三层各自按 id 升序批删（`AUDIT_LOG_RETENTION_BATCH_SIZE=5000`/层/tick）。
   **不**复用 `run_retention_cleanup` 的锁序机器：audit_logs 无 FK 子树、无引用
   闭包，结构上不存在 #1827「引用闭包饿死」形态。
3. **汇总审计 + 自免环**（D5）：仅当 tick 确有删除时写一条
   `audit_retention_pruned`（details 含各层删除数与配置天数），落 business 桶、
   不豁免于裁剪谓词（90d 后自清）。指标
   `stability_audit_retention_pruned_total`（按层分桶）。
4. `terminal_payload_conflict` 爆发行**不例外**（裁决问 3）；会话类同表、不拆
   表、不做 UI 折叠（裁决问 2/4）。
5. 文档同步：`.env.example`（调度节奏 + 保留期两节）、
   `docs/development/environment-variables.md` 附录（`env_inventory.py --write`
   再生成，5 个新变量全登记）、`docs/adr/README.md` 增 ADR-0049 行。

## Alternatives

- **统一 N 天**：最简但安全事件与例行心跳同寿命（浪费或丢事实链二选一）。
- **分区/按月子表**：26 万行量级够不着分区收益阈值（ADR-0049 Revisit 留出口）。
- **复用 run_retention_cleanup**：其复杂度全部服务于 FK 闭包与行锁窗口，对
  无闭包表是纯负担。
- **裁剪逐行写审计**：被陪葬行自证无价值；汇总一条记录治理动作即够。

## Verification

- `./scripts/run_pytest.sh backend/tests/scheduler/test_audit_log_cleanup.py -q`
  → **5 passed**（三层 cutoff 独立性、business 默认桶 NOT IN 封闭性、90<age<180
  安全事件保留 + idle tick 零汇总、批次有界且下一 tick 推进、汇总行自免环）。
- `./scripts/run_pytest.sh backend/tests/scheduler/ -q` → **146 passed**（含
  既有 retention/锁序/DLE 套件无回归）。
- `./scripts/run_pytest.sh backend/tests/api/test_main_lifespan.py -q` →
  **4 passed**（app_scheduler 注册面无回归）。
- `./scripts/run_pytest.sh backend/agent/tests/test_p3_3_multi_instance.py -q` →
  **15 passed**（SINGLETON_SCHEDULE_IDS 精确集合断言随新作业同步——CI 首轮
  pr-agent-tests 红灯即此处，本地复现修复后通过）。
- `python tools/dev/env_inventory.py --check` → 一致（239 个读取名，5 个新旋钮全登记）。
- `.venv/bin/python scripts/run_gates.py check:quick` → **12 gates 全绿**
  （含 ruff / eslint / gov-surface / inner-imports 棘轮 604 ≤ 604——app_scheduler
  的作业 import 因此提到模块顶层：audit_log_cleanup 无回边，函数级反而推高基线）。
- 首轮生产效果（合入并部署后）：`stability_audit_retention_pruned_total` 应在
  数个 tick 内完成存量到期行的收缩，此后随写入速率稳态跟随——**部署后需观测
  一轮再视为验收完成**（见 Revisit）。

## Revisit

- 部署后首个 24h：确认裁剪速率跟上写入（counter 增速 ≈ 写入速率）、汇总审计
  未异常累积（每 tick ≤1 行）。
- 表 >5M 行或查询退化 ⇒ 重议分区（ADR-0049 §5）。
- `SECURITY_ACTIONS` 漏登第一次实际发生 ⇒ 重议自动登记校验。
