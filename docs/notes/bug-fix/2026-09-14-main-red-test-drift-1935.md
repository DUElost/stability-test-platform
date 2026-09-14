# main 红灯修复：三处测试漂移（#1935）

Status: implemented
Class: bug-fix

## Decision

2026-09-14 main 后备 CI 红灯（告警单 #1935，红灯 job：`backend-test` / `frontend-check`，
疑似区间 `27cc338668...172c032666`）。三处失败全部是**测试断言漂移**——生产代码
语义未变，不做实现改动，按「断言意图不变、抗后续漂移」修正：

- `backend/tests/migration/test_host_retirement_roundtrip_1800.py`
  - `test_single_head_offline`：原断言「`RETIRE_REV` == head」。#1890/#1907 在
    其上续接迁移后 head 前移即误红。改为离线读 Alembic `ScriptDirectory`：
    仍断言单 head，另断言 `RETIRE_REV` **在 head 的祖先链上**（该测试的本意
    是「本迁移仍在线」，head 前移是正常演进）。
  - `test_retirement_columns_roundtrip`：原用 `downgrade -1` 撤列，同样隐含
    「本迁移 == head」。改为显式 `downgrade a3b2c1d0e9f8`（本迁移的父
    revision；已发布 revision 不可变，父版本稳定，不随 head 前移失效）。
- `backend/tests/services/test_chain_trigger_offline_filter.py`
  - 原断言 `src.count('if status == "ONLINE"') == 2`——绑定字面量出现次数。
    #1822 把过滤抽成共享 helper `_select_chain_devices`（判据同时扩展为
    ONLINE + 心跳窗口内瞬时 OFFLINE）后计数变化即误红。改为 AST 结构断言：
    两个触发路径（`trigger_next_plan` async / `trigger_next_plan_sync`）都调用
    该 helper，且 helper 内保留 ONLINE 判据。
- `frontend/src/pages/devices/DevicesPage.test.tsx`
  - #709 把 `AssignProjectDialog` 的项目查询从 `api.projects.list()` 换成
    `listActive()`（选择器排除归档），页面测试的 mock 只覆盖 `list` → 弹窗
    拿不到项目、下拉无选项，批量归入用例红。补 mock `listActive`。

## Alternatives

- 只把断言改绿（head 改成当前值 `4c84155b7e59`、字面量计数改成 1）：下一位
  迁移 / 下一次重构会再红一次——原断言绑定的就是易变事实，弃。
- 让 helper 的 `if st == "ONLINE"` 改回字面量以迁就旧断言：为测试改实现方向，
  且 #1822 的瞬时 OFFLINE 判据是刻意扩展（父段结束瞬间抖动不应永久静默缺席），弃。
- 把 `AssignProjectDialog` 改回 `list()`：与 #709「选择器排除归档」语义冲突，弃。
- 顺手修本机 7 个 `test_health_saq.py` 环境失败：非本单范围（纯净 origin/main
  检出同样复现，CI 侧不红），见 Revisit。

## Verification

- `pytest backend/tests/migration/test_host_retirement_roundtrip_1800.py
  backend/tests/services/test_chain_trigger_offline_filter.py -q` → **5 passed**
  （迁移往返真起 postgres:16 一次性容器，3.3s）
- `pytest backend/tests/ -q` → **7 failed, 2630 passed**（8m47s）。7 个失败全部
  为 `test_health_saq.py` 本机环境噪声——在纯净 `origin/main` 检出上复现同样
  7 个（对照实验，改动前即红），与本次改动无关
- `npx vitest run`（frontend 全量）→ **106 files / 794 tests passed**（改动前
  CI 为 1 failed / 791 passed）
- `npm run type-check` → 通过
- 前端对照：CI 失败用例 `DevicesPage.test.tsx > admin can bulk-assign...`
  单跑通过（4 tests passed）

## Revisit

- 本机 7 个 `test_health_saq.py` 环境失败未收口（`TESTING=0` 下 `/health` 的
  DB 探针在本机 testcontainer 路径连不上）；如需本地全量可信，单开一单修。
- `frontend-check`（vitest）在 ci.yml 里是 `if: github.event_name != 'pull_request'`
  —— PR 阶段不跑，此类前端测试漂移只能在 main 兜底发现（#709 即如此）。是否
  给 PR 路径前移 vitest 子集需独立裁决（注意力预算 vs 拦截位置）。
