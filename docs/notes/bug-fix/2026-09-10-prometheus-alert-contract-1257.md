# Prometheus 告警规则契约：三条标签/直方图漂移修复 + PR 路径门禁（#1257 / R14-F11）

Status: implemented
Class: bug-fix

## Decision

审计发现三条规则的选择器与 `backend/core/metrics.py` 实际导出不一致——挂载后
对应异常**静默不告警**（不推断生产已挂载）。逐条修正并加机械门禁，防止同类
漂移再次静默发生：

1. `StabilityDispatchGateFailed`：`{status="failed"}` → `{outcome="failed"}`
   （Counter 标签是 `outcome`：passed / synced_passed / failed / skipped）；
2. `StabilityPatrolStall`：直方图**裸用基础名** `stability_patrol_failure_streak_observed`
   → `sum(increase(..._count[15m])) − sum(increase(..._bucket{le="1"}[15m])) > 0`
   （连续失败 ≥2 的观测数）。两侧 `sum()` 是必需的：`le` 标签不对称会让减法
   恒为空向量（promtool 场景测试实测拦下过这一版）；
3. `StabilityHostHeartbeatTimeout`：`{job=...,status="error"}`
   → `{job_name="session_watchdog",outcome="error"}`。

**门禁（结构层，恒跑）**：`tests/test_prometheus_alerts_contract.py` 解析规则
表达式的选择器，与指标注册表逐条比对——未知指标、未知标签、Histogram/Summary
裸基础名（client 会把基础名一并列出但不是可查询序列）全拦。实现要点：

- 带标签的指标在未实例化子序列时 `collect()` 不产出样本，改用注册表的
  collector→names 映射 + `_labelnames`，未实例化的指标同样可见；
- 直方图 `_bucket` 额外允许 `le`（client 不把它列进 `_labelnames`）；
- 解析器自证用例防止结构层因解析退化为空而假绿。

**门禁接线（PR 路径）**：`ci.yml` pr-agent-tests job 增加一步（与 lock 卫生
测试同理由前移：纯离线 <1s、随 backend lock 依赖即可跑）；`run_gates.py` 新增
`prom-alerts` gate 进 `check:pr`（镜像 CI），并在
`tools/dev/check_governance_surface.py` 的 `GATE_TO_CI_ANCHOR` 登记配对
（S5x 门禁首先抓住了「只加本地不接 CI」的初版，棘轮如期生效）。

**场景层（promtool 可用时）**：`deploy/prometheus/alerts-stability-platform.test.yml`
用真实标签形状样本证明三条规则可触发（`promtool test rules`，含精确
labels/annotations 断言）；runner 无 promtool 时该子项 skip，结构层不受影响。

**文档同步**：`docs/operations/README.md` 可观测性小节标注门禁与场景文件。

## Alternatives

- **只改表达式、不加门禁**——放弃：本单现象就是「表达式错、无人拦、静默不
  触发」，没有注册表比对的机械检查，下次改指标名/标签仍会重演（#518 静默跳过
  教训同族）；
- **在 lint job 里跑结构层**——放弃：lint job 不装 backend 依赖（仅 ruff），
  本检查需要 PyYAML + prometheus_client；放 pr-agent-tests（lock 依赖在场）
  与 lock 卫生测试同理由；
- **promtool 升为硬门禁**——放弃：runner 无 promtool，apt/下载引入未锁定外部
  依赖；改为结构层恒跑 + 场景层可用时跑（本机有 promtool，触发证明留档）；
- **文本级扫 YAML 比对指标名**——放弃：无法正确跳过函数名、标签值与时间窗口；
  选择器级解析（覆盖 `name{labels}[range]` 形态）更准且有自证用例；
- **给每个带标签指标实例化 dummy 子序列让 `collect()` 出样本**——放弃：需要
  为每个指标枚举标签值（写死值即脆弱），读注册表结构更稳。

## Verification

实际运行（worktree `/tmp/stp-1257`，2026-09-11）：

- `pytest tests/test_prometheus_alerts_contract.py -q` → **3 passed**
  （解析器自证 / 结构层 / promtool 场景）；
- **红绿自证**：修复前三条旧表达式过同一结构层 → 分别报
  `未知标签 ['status']`、`基础名不可查询`、`未知标签 ['job','status']`；
- `promtool check rules deploy/prometheus/alerts-stability-platform.yml`
  → SUCCESS: 12 rules found；
- `promtool test rules deploy/prometheus/alerts-stability-platform.test.yml`
  → SUCCESS（三条规则按预期触发，labels/annotations 精确匹配）；
- `ruff check .` → All checks passed；
- `gov-surface`（含 `--self-test`，14 条规则红绿双向）→ 通过；
- `check:full`（本机全量，含 CI 变更所需的本地全量纪律）→ 全部 gate 绿；
  `docker-build` 首次运行被本机 worktree 的 `frontend/node_modules` 软链干扰
  （`COPY frontend/ .` 与镜像内 `npm ci` 目录冲突；`.dockerignore` 的
  `node_modules/` 不匹配软链，属本机产物而非仓库缺陷），移除软链后按 gate 同
  命令复跑 backend / frontend 两个镜像构建均成功。

未完成（pending）：无。

## Revisit

- runner 若未来预装（或锁定下载）promtool，把场景层升为硬门禁；
- 表达式出现解析器不支持的形态（subquery / offset / label_replace 等）时扩展
  `_selectors` 并补自证用例；
- #1258（仪表盘幽灵指标/只有定义没有采集）是同一可观测性面的另一单，不在本单
  范围；其修复涉及生产者补齐或面板撤下的方向选择。
