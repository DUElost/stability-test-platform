# pipeline 模板钉到磁盘最新版（check_device / monkey_setup）

Status: implemented
Class: bug-fix

## Decision

#2865：`check_device` v1.0.2 与 `monkey_setup` v2.3.9 已合入主线，但
`backend/schemas/pipeline_templates/*.json` 仍钉 `1.0.0`，仓库侧零引用。
脚本执行按精确版本解析、无 latest 兜底 ⇒ 从模板新建的 Plan 带不到修复。

本 PR 只做仓库侧收尾：

1. 8 个模板的 `script:check_device` → `1.0.2`；`monkey.json` /
   `monkey_watcher_patrol.json` 的 `script:monkey_setup` → `2.3.9`
   （钉值取自磁盘最新目录，与当时 `origin/main` 一致）；
2. 守卫 `tests/test_pipeline_template_script_pins_2865.py`：上述两族
   「模板 pin == 磁盘最新」对拍，防止再出现「版本合入、模板未钉」；
3. `docs/development/script-versioning.md` 增「新版本上线收尾」：把模板钉钉
   写成仓库侧必做，并把 scan / plan_step 重指标为控制面运维步骤。

**明确不做**：`POST /scripts/scan` 与存量 `plan_step` 重指——生产写操作，
需运维授权；未做前周期链上的既有 Plan 仍跑旧版（与 issue 边界一致）。

#2802（`check_device` v1.0.2）与 #2862（`monkey_setup` v2.3.9）已合入 main；
本 PR 在追 main 后由守卫逼出同批再钉——证明耦合有效。

## Alternatives

- **只写 SOP、不改模板**：新建 Plan 继续钉旧版，#2865 的仓库侧主张不成立。
- **种子迁移批量改 plan_step**：会静默改生产编排，且与「重指须运维授权」冲突；
  放弃。
- **守卫覆盖全部模板脚本**：会误伤故意钉旧稳定版的步骤；先锁复发过的两族，
  再按同形审计扩名单。

## Verification

- `pytest tests/test_pipeline_template_script_pins_2865.py -q`
- `pytest backend/tests/api/test_pipeline_templates_stages.py -q`
- 人工：`python -c` 遍历模板，确认两族 pin 与 `backend/agent/scripts/*/v*` 最新一致

## Revisit

- 控制面完成 scan + 周期链 `plan_step` 重指后，方可关闭 #2865（代码合入不够）。
- #2802 / #2862 合入若未带模板钉钉，本守卫应变红——那时扩钉即可，不必重开本单。
- 若再出现第三族「合入未钉模板」，把脚本名加入 `PINNED_SCRIPTS` 名单。
