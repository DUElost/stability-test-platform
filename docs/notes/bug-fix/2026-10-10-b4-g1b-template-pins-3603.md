# B4 G1b：三个模板 setup pin 追平阶段 A head

Status: implemented
Class: bug-fix

## Decision

按 [#3601](https://github.com/DUElost/stability-test-platform/issues/3601) 正文 v1.3 §3 G1b 行、§4.4 阶段 B 仓库证据、§5 第 6 步，以及 2026-10-10 Owner 裁定（阶段 A 退出，评论 6092925007）执行。开工时 `origin/main`（`4f5d2041`）上 `tool_manifest.json` 与阶段 A release `89b9dc85` 字节相同；`gpu_setup` 1.2.4、`powercycle_setup` 1.2.9、`sleep_setup` 1.0.6 仍是未退役 head，包 sha 未变。

三个模板只改 setup pin：`gpu.json` 1.2.3→1.2.4，`powercycle.json` 1.2.8→1.2.9，`sleep.json` 1.0.5→1.0.6。同 PR 清空 `tests/test_pipeline_template_script_pins_2865.py` 的三条临时 `EXCEPTIONS`，并删除只为这三条存在的 `test_b4_g1a_pending_activation_exceptions_have_teeth` 与其专用对拍函数。通用守卫保留：pin 追磁盘 head、例外必须是真实滞后且有理由、谓词牙测试、版本真实存在牙测试。

不改族树、manifest、checker，不登记新版本，不 publish，不 scan。代码合入不改运行模板；生效要等部署 B 的单独授权。G1a 记录见 `docs/notes/bug-fix/2026-10-03-setup-resource-authority-3320.md`。

## Alternatives

- 把追 pin 和删例外拆成两个 PR：§3 要求同 PR 完成。只删例外时旧 pin 会被守卫判红；只追 pin 时旧例外仍把期望版本钉在旧 pin 上，守卫同样判红。
- 顺手追 check/finish 或其它步骤：§3 只授权三个 setup pin，其余字段保持原样。
- 再登记一个脚本版本：三个 head 与阶段 A 条目一致，没有被占用，§7 第 10 条不允许改追其它版本。

## Verification

- 漂移：`89b9dc85:tool_manifest.json` 与 `origin/main:tool_manifest.json` blob 相同；四目标族（含 `mtbf_setup` 1.4.2）条目逐条相同。开放 PR 未改这三个模板或 pin 守卫文件。
- 负向（`/tmp/b4-g1b-neg`，`origin/main` 的模板、manifest 与守卫；项目解释器 `python -m pytest`，因为 `run_pytest.py` 会切回 checkout。每次变异后与快照逐字节恢复，`restore_ok`）：
  - 保留旧 pin、清空 `EXCEPTIONS`：`test_pinned_template_scripts_track_latest_on_disk` 失败，三条均为旧 pin 对新 head（1.2.3/1.2.4、1.2.8/1.2.9、1.0.5/1.0.6）。
  - 追到新 head、保留旧例外：同一守卫失败，期望值仍是例外里的旧 pin。
- active 前置的仓库侧：隔离测试库调用真实 `_validate_script_refs`（`env -u TEST_DATABASE_URL -u DATABASE_URL`，未连生产库）。三个模板共 33 步、16 个精确引用；新 head 未注册与 `is_active=False` 均 422，`missing` 恰为 `gpu_setup:1.2.4`、`powercycle_setup:1.2.9`、`sleep_setup:1.0.6`；三行改为 active 后通过。部署 B 前的生产只读回查不在本 PR。
- `./scripts/project_python.sh scripts/run_pytest.py tests/test_pipeline_template_script_pins_2865.py -q`：7 passed。
- `./scripts/project_python.sh tools/dev/check_script_packages.py`：35 个族树与 manifest 最新登记等价。
- `./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main`：40 族 / 243 版本，相对 origin/main append-only。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：16 gates 通过。`schema-at-head` 因 `DATABASE_URL` 未配置跳过对齐检查（WARN），其余项绿。
- `./scripts/project_python.sh scripts/run_pytest.py tests/ -q`：2365 passed，18 skipped。

## Revisit

部署 B 之前按 §5 第 7 步回查四目标仍 active、三个新模板的全部精确引用有效。若合入前 main 上这三个 head、包 sha 或族树相对阶段 A 发生变化，按 §7 第 10 条退回，不改追其它版本。存量 Plan 重指另要 Owner 授权，不在本单元。
