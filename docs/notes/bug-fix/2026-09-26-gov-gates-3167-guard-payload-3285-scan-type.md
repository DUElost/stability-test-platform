# 门禁可信度两单：#3167 guard UNKNOWN payload + #3285 scan 响应具名类型

Status: implemented
Class: bug-fix

## Decision

两单同属 2026-09-26 统一裁决批（`docs/notes/architecture/2026-09-26-pending-issue-decisions-sweep.md`），
均为「结论/形状看不见」被读成正常：一条把真未知折成工具坏，一条让后端新键在前端声明面隐形。

### #3167 · `--guard` 判据不可得时先给 payload 再 rc=2

`backend/scripts/check_unreferenced_script_versions.py` 原先在 `--guard` + 使用事实不可得时
只往 stderr 打一行就 `return 2`，stdout 为空。probe（`tools/dev/script_guard_probe.py`）的
`summarize` 以「rc=2 **且** payload 带 `guard` 块」识别 unknown（#2884 判据）——
没有 payload 就落进 broken 分支，**真未知被读成工具自身异常**，`stp_script_guard_unknown`
恒 0、`stp_script_guard_broken` 每天置位。

修法（按裁决「判据侧修」）：该分支不再提前 return，走完与其它路径相同的 payload 出口
（`--json` 下 `guard.status=UNKNOWN` / `violations=0`），最后统一 `return 2`；
probe 的 broken 判据不动（rc=2 无 `guard` 块仍是异常，argparse 用法错误继续不得读成 unknown）。
`--json` 之外的表格与 stderr 行保留，人的读法不变。

### #3285 · scan 响应升具名模型（选项 ②）

`POST /api/v1/scripts/scan` 原为 `response_model=ApiResponse[dict]`，挂在形状契约测试的
dict 盲区豁免里：后端 `to_dict()` 已 10 键（`7008ad5e` / `64d0ef93` 两批扩面），前端
`tools.ts` 内联类型仍 4 键——ADR-0051 三个闸口的报告面（`package_conflicts` /
`package_missing` / `unregistered_active`）在 UI 完全不可见，且**任何门禁拦不住下一次漂移**。

修法：新增 `ScriptScanOut`（10 键，与 `ScriptScanResult.to_dict()` 一一对应；
条目内层保持 `Dict[str, str]`，本单只对齐类型面不窄化内层），端点改
`ApiResponse[ScriptScanOut]`；`_MODEL_PAIRS` 登记双向对拍、`_MODEL_BLINDSPOT` 撤销
`scan_scripts` 豁免；`types.ts` 新增同名接口（含四个条目接口），`tools.ts` 改用具名类型。

**附带一条静默丢键的守卫**（本单修法引入的新风险）：端点升模型后 FastAPI 默认
`extra=ignore`——只往 `to_dict()` 加键而漏改模型，该键会被**静默丢弃**（比原 dict 盲区
更隐蔽）。故在 `backend/tests/api/test_scripts.py` 增键集合相等断言
`set(ScriptScanOut.model_fields) == set(ScriptScanResult().to_dict())`，与
`_MODEL_PAIRS`（模型 ↔ TS）合成「服务 → 模型 → TS」三段闭合。

UI 是否展示新键属产品决定，不在本单（裁决原文）。

## Alternatives

- **#3167 改 probe 判据**（rc=2 无 payload 也算 unknown）：会把 argparse 用法错误读成
  「结论未知」（#2884 已裁定不得如此），且把生产者的缺陷转嫁给消费者；生产者补齐出口才是单点修法。
- **#3167 只把 stderr 行改成可解析 JSON**：stderr 不是契约面（probe 只读 stdout），
  且「所有结论进 payload」是文件既有口径，另开通道会造第二事实源。
- **#3285 选项 ①（只升级 TS 内联类型）**：4→10 键写法变，但 dict 盲区豁免仍在，
  下一次后端扩键同样无门禁可拦——正是本单要消除的形态。
- **#3285 内层条目窄化为嵌套 Pydantic 模型**：各分支键集不同（rebaselined 带两 sha、
  package_missing 带 artifact、package_conflicts 带 reason+两 sha），窄化要么补空键改变线格式、
  要么堆 Optional 丢判据；键集对齐留待有 UI 消费面时按需做。

## Verification

- #3167 反例自证：暂撤判据修复（`git diff` 存 patch → checkout → 跑）⇒
  `test_cli_guard_unknown_emits_payload_for_probe` 红（stdout 空 ⇒ `json.loads` 失败）；
  `git apply` 恢复后绿。
- #3285 反例自证（两处 mutation）：
  - 删模型 `package_missing` 字段 ⇒ `test_script_scan_response_model_matches_service_keys`
    与 `test_ts_fields_are_all_declared_in_model` 双红；
  - 删 TS `unregistered_active` 字段 ⇒ `test_model_fields_are_declared_in_ts` 红；
  - 均恢复后绿。
- 实跑（worktree `.wt/stp-gates-3285-3167`）：
  - `.venv/bin/python -m pytest backend/tests/test_script_retirement_guard.py -q` → 21 passed
  - `.venv/bin/python -m pytest tests/test_script_guard_probe.py -q` → 32 passed
  - `.venv/bin/python -m pytest tests/test_api_response_shape_contract.py backend/tests/api/test_scripts.py -q` → 43 passed
  - `npm run type-check` 通过；`npx eslint src/utils/api/tools.ts src/utils/api/types.ts` 通过
  - `python scripts/run_gates.py check:quick` → 见 PR 描述

## Revisit

- #3167：probe 的 rc=2 分支当前不看 `guard.status`/`violations` 形状；若未来出现
  「payload 在但形状漂移」实例，再在 probe 侧收紧（本单不动消费者判据）。
- #3285：若 UI 开始展示 `package_conflicts` / `package_missing` / `unregistered_active`，
  按来源分支渲染条目可选键（`reason` / `db_sha256` / `manifest_sha256` / `artifact`）；
  届时再评估是否把内层升为嵌套模型。
