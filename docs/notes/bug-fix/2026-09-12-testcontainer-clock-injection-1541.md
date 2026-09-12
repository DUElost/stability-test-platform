# #1541 修复：testcontainer 巡检工具时钟可注入

Status: implemented
Class: bug-fix

## Decision

`tools/dev/check_test_containers.py` 的 `main()` 增加 `now` 关键字参数并透传给
`plan_targets()` 与 `age_minutes()`；`tests/test_check_test_containers.py` 的
`TestMainFlow` 六处 `main()` 调用全部显式传 `now=NOW`。

**不放宽阈值、不改判定语义**——错的是时钟来源，不是 120 分钟阈值。CLI 行为不变：
`now` 缺省 `None` → `age_minutes()` 仍取 `datetime.now(timezone.utc)`，且**刻意不暴露
为 `--now` 命令行参数**（巡检工具不应允许使用者伪造「现在」来绕过残留判定）。

修复前的失配链：

1. `tests/test_check_test_containers.py:22` — `NOW = datetime(2026, 9, 11, 12, 0, tzinfo=utc)` 冻结常量；
2. `_c()`（:25-30）以 `created=NOW - timedelta(minutes=age)` 构造容器；
3. `Container.age_minutes()`（`check_test_containers.py:43-45`）默认 `ref = datetime.now(timezone.utc)`；
4. `main()`（原 :131-133）调 `plan_targets(...)` **不传 `now`** → 走真实时钟。

于是 `_c(5)`（本意「5 分钟前」）的真实判龄 = 冻结点与真实 now 的 skew + 5min。
2026-09-12 08:59 UTC 实测 **skew=1259.98 分钟**，与失败输出 `[残留 1265m] recent1`
吻合——任何 `min_age_minutes`（默认 120）都必然把它判成残留。

`plan_targets` 本身早有 `now` 参数且 `TestPlanTargets` 正确传参，缺口**只在
`main()` 未把时钟往下传**，使 `main` 级测试无法注入时钟。

## Alternatives

- **放宽 `--min-age-minutes` 到大于 skew** → 否决：阈值是正确行为，改它就是拿
  产品语义迁就测试缺陷；且 skew 随时间单调增长，任何常数都只是推迟复发。
- **测试改为动态时钟（用真实 now 构造容器）** → 否决：测试便不再确定，判龄
  逻辑的边界（119/120/121 分钟）无法稳定断言，退化为 flaky。
- **只在测试里 monkeypatch `datetime`** → 否决：给被测模块打全局补丁比显式传参
  更脆（`from datetime import datetime` 的绑定形态下 patch 目标易错），且把可测性
  藏进测试而非实现契约。
- **`main()` 内读环境变量时钟** → 否决：隐式全局状态，同 monkeypatch 之弊。
- **暴露 `--now` CLI 参数** → 否决：见 Decision——巡检工具允许伪造当前时间会
  削弱残留判定本身。

## Verification

- `python -m pytest tests/test_check_test_containers.py -q` → **12 passed**
  （修复前 `2 failed, 10 passed`；已在 `origin/main` cfe3d520 干净检出复现同样 2 例红，
  确认非本单引入）；
- `python -m pytest tests/ -q` → **178 passed**（修复前该目录恒红）；
- 时钟解耦独立验证：同一组容器分别以 `now=2026-09-11` 与 `now=2040-01-01` 调
  `plan_targets`，前者 `stale=[stale1] recent=[recent1]`、后者两者皆 stale——
  证明结果只随传入时钟变化，与真实时间无关；缺省调用仍取真实时钟（CLI 行为不变）；
- CLI 回归：`python tools/dev/check_test_containers.py` dry-run 正常、`--help` 未
  新增参数；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿；
- `ruff check tools/dev/check_test_containers.py tests/test_check_test_containers.py`
  → All checks passed；
- 同类模式排查：`tools/dev/queue_head_telemetry.py:312` 亦用 `datetime.now`，但
  `now` 仅作 `observed_at` 时间戳标签、不参与年龄比较，其测试
  （`tests/test_automerge_queue_alerts.py` 6 passed）不受影响——**不扩大改动范围**。

## Revisit

- **CI 覆盖缺口（本单暴露，未在本单修）**：`pr-agent-tests`（required check）只跑
  `backend/agent/tests/` 加两个 lock 卫生文件与 Prometheus 契约
  （`.github/workflows/ci.yml:231-270`），**`tests/` 其余文件不在 PR 路径上**——本单
  的红测试因此随 #1482 合入 main 而无人拦截。是否把纯离线、<1s 的
  `tests/test_check_test_containers.py` 与 `tests/test_container_lifecycle.py`
  一并前移（同 lock 卫生测试的先例与理由），或改为按 diff 触发，需独立裁决：
  与 `tests/` 目录的迁移/部署耦合边界有关，不随本单顺手扩大。
- 冻结时钟常量 `NOW` 仍是**手工维护的固定日期**：当前实现已与之解耦，故不再随
  真实时间漂移；但若日后新增以 `datetime.now()` 判龄的调用点，须同样接受注入，
  否则本类缺陷会以新形态复发。
