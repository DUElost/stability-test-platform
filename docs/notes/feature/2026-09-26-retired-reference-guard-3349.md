# manifest retired 登记的 plan_step 引用守卫（#3349，ADR-0023 D6 源头守卫）

Status: implemented
Class: feature

关联：[#3349](https://github.com/DUElost/stability-test-platform/issues/3349)（前置裁决
[#3347](https://github.com/DUElost/stability-test-platform/issues/3347) 2026-09-26 三项
确认：D6 改「源头守卫」维持）、ADR-0023 D6、ADR-0051 D2/D5、#3285（scan 具名响应模型，
本单在其上扩键）、#735。

## Decision

- **登记路径堵上「Plan 引用已停用版本」的最后一个入口**：`sync_scripts_from_manifest`
  遇 `retired:true` 条目时先查 `plan_step` 引用（查询形态与 `_referencing_plan_ids`
  同构）——仍被引用则**不翻转** `is_active`，计入新报告面 `retire_blocked`（计数）/
  `retire_blocked_versions`（`name`/`version`/`plan_ids`，与 409 `SCRIPT_STILL_REFERENCED`
  响应体同信息）；零引用照常翻转（行为不变）。
- **报告不拒绝**：与 ADR-0051 D2「注册只报告」取向一致——scan 整体不失败，人工重指
  plan_step 后重扫即翻转；被引用阻断只挡生命周期翻转，**不挡** #3222 的包身份补齐
  （两事正交，测试钉住）。
- **类型三面同步**：`ScriptScanOut` 扩至 12 键，新明细用具名内层模型
  `ScriptScanRetireBlocked`（`plan_ids: List[int]` 非字符串，不适配既有
  `Dict[str, str]` 内层约定）；`types.ts` 同步 `ScriptScanRetireBlocked` 接口；
  键集对拍（`test_script_scan_response_model_matches_service_keys`）自动覆盖新键。
- 文档两处同步（验收标准④）：`script-versioning.md` scan 段与退役段、
  `script-version-lifecycle` SKILL §B 新增第 5 步「重扫核对 retire_blocked」。

## Alternatives

- **登记时直接 4xx/跳过整个 scan**：弃——scan 是批量对账入口，单条引用问题不应
  中断其余登记；报告面 + 人工重指是 D2 裁决的既有取向。
- **复用 `script_retirement.classify` 判据**：弃——那侧是「零引用退役候选」判据，
  本单只需要「仍被引用」的存在性判断，且登记路径在事务中间，引整套判据过度。
- **plan_ids 压平成字符串进 `Dict[str, str]`**：弃——信息降形；具名内层模型成本
  三个字段，#3285 的类型收口先例就是往具名方向走。

## Verification

- `pytest backend/tests/services/test_script_catalog_sync.py` → **18 passed**（16 既有
  + 2 新增：被引用保持 active + 报告 plan_ids、阻断不挡包身份补齐；既有绿路径补
  `retire_blocked == 0` 断言——验收标准②③的红绿双向）；
- `pytest backend/tests/api/test_scripts.py tests/test_api_response_shape_contract.py`
  → 43 passed（键集对拍与形状契约含新键全绿）；
- `python scripts/run_gates.py check:quick` → 16 gates 全绿（含 eslint——`types.ts`
  改动面）；`check_governance_surface.py --check` 全绿。

## Revisit

- `retire_blocked` 目前只在 scan 响应与日志可见；UI 是否展示属产品决策（同 #3285 的
  三个报告键先例——类型面对齐先行，展示另议）。
- 退役工具 `manifest_retire_from_db`（#3386 修）在侧已拒被引用条目，与服务侧本守卫
  构成双保险；若未来出现第三条退役写入路径，须同挂 `_ensure_script_can_be_deactivated`
  语义（script-versioning.md 退役段已写明唯一入口）。
