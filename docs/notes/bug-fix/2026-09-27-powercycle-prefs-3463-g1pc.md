# powercycle 三族 v1.2.7 / 1.0.9 / 1.0.7：prefs 判据收口 + 宽容解码（#3088 #3174 / B1-G1-pc）

Status: implemented
Class: bug-fix

关联：[#3088](https://github.com/DUElost/stability-test-platform/issues/3088)（F1 主案）、
[#3174](https://github.com/DUElost/stability-test-platform/issues/3174)（powercycle_setup F1）、
[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 规划方案 §3 G1 / §4 G1）、
#2979（setup 侧参照判据）、#3069（F2 形态）、gpu_setup v1.2.3（F2 参照实现）。
本 note 覆盖 G1 的 powercycle 子组（G1-sleep 子组另行交付）。

## Decision

按 #3463 §3 G1「修复内容」逐族同构 port，不重新设计：

1. **F1 `repair_prefs_ownership` 收紧（check / finish 两族 port）**：把
   powercycle_setup 参照实现的 `_root_read_prefs()`（单调用同源五态证据）与收紧后的
   `repair_prefs_ownership()`（只有「文件存在 + 读取成功 + 内容为空」才 `rm -f`）port 进
   `powercycle_check/_lib.py` 与 `powercycle_finish/_lib.py`。两族旧拷贝的判据是
   「run-as 读空 + 可 root ⇒ rm」——AutoTestTool 是 platform 签名 shared-uid 包，run-as 恒拒
   ⇒ 每轮 repair 都删健康 prefs（#3088 主案：续跑 current_count 归零、auto_resume 断链）。
2. **F1 `set_stop_flags` 收紧（三族）**：整写最小 map 只认 `_root_read_prefs` 的 `absent`
   证据；`transient`/`denied`（含 repair 后仍 `empty`）保留文件并 raise——步骤转红可重试，
   不再把健康 prefs 降成两标志最小图。非 root 下 run-as 读空同样不作 absent 证据，raise
   暴露可重试失败（该包 run-as 恒拒，旧代码走到这里也必然写失败，raise 只是提前且报文更真）。
3. **F2 `adb()` 宽容解码（三族）**：port gpu_setup v1.2.3 参照——`decode_device_output()`
   （bytes + `errors="replace"`）+ `adb()` 显式收字节，去掉 `text=True`（#3069 形态）。
4. **模板 pin 同批追平**：`backend/schemas/pipeline_templates/powercycle.json` 三步 pin →
   1.2.7 / 1.0.9 / 1.0.7（#2865 纪律「模板 pin 必须等于磁盘 head」，与 G2 行 gpu.json 同款；
   不登记 EXCEPTIONS 滞后项——整批 §5 的 publish + scan 紧随部署）。
5. 登记 `powercycle_setup` **1.2.7**（树内已含未激活的 1.2.6 内容，激活重指时跳过 1.2.6）、
   `powercycle_check` **1.0.9**、`powercycle_finish` **1.0.7**（manifest append-only）。

## Alternatives

- **顺带把 check/finish 的 `get_prefs_xml` 也升级为 root 优先版**：#3463 §1 只列
  `_root_read_prefs` + 收紧的 `repair_prefs_ownership` + `set_stop_flags` 三面；§6 明确不做
  「除 port 参照实现外的 prefs 逻辑重构」。未列面不触碰（`resume_task`/`start_task` 的读路径
  维持现状，属后续单）。
- **非 root 下给 run-as 增设同形探测以区分 absent**：新增参照实现不存在的函数，超出「逐族
  同构 port」；且该包 run-as 恒拒，非 root 路径本来就写不进去，raise 语义等价。
- **`set_stop_flags` 对 `empty` 也整写**：repair 已先行删空文件，probe 仍 `empty` 只说明删除
  未生效（如 SELinux 拒绝）——这是未知态而非损坏证据，按 ② 的可重试失败处理。

## Verification

- `python -m pytest backend/agent/tests/test_powercycle_prefs_3463_g1pc.py -q` → **25 passed**
  （①root 可读不 rm 且不走 run-as、②transient/denied 不整写+保留文件+可重试失败、③仅
  absent 整写最小 map、非 root 读空不整写、finish `stop_task` 透传可重试失败、可读时原 map
  改标志不降级——各 ×3 族；F2 坏字节 ×3 族）。
- **反例（变异验证）**：把 `powercycle_check/_lib.py` 的 repair 临时改回「凡非 ok 即删」、
  `set_stop_flags` 改回「读空即整写」后复跑 → `test_transient_read_keeps_file_and_fails_retryable`
  / `test_denied_read_keeps_file_and_fails_retryable` / `test_absent_writes_minimal_map` 三条转红；
  恢复后 25 passed，`check_script_packages` 门禁仍绿。
- `python -m pytest backend/agent/tests/ -q -k "powercycle"` → **114 passed, 2119 deselected**。
- `python -m pytest tests/test_pipeline_template_script_pins_2865.py -q` → **7 passed**
  （钉 pin 前该测试红：三条 pin 落后磁盘 head；钉后绿）。
- `python tools/dev/check_script_packages.py` → 绿（35 个族树与最新登记等价）。
- `python tools/dev/check_tool_manifest.py --base origin/main` → 绿（40 族 / 225 条目，
  append-only；基线已含并行 G2 的 gpu_finish 1.0.8，本分支已 rebase 到 fffb0159）。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。
- declare 侧：`--force` 留痕（issue #3463/#3088 与并行兄弟单元相交——批次 §3「各组族目录
  互不重叠，可并行」的预期边界）；scope 后补 `powercycle.json` 模板（#2865 门禁的必需随附物）。

## Revisit

- **生效未做**：按 #3463 §5 由 Owner 窗口统一 publish + scan + 重指（powercycle_setup 从现
  引用版本直接重指 1.2.7、跳过 1.2.6；powercycle.json 已 pin 新版本，部署窗内 scan 前由模板
  新建 Plan 会 422——与 G2/G4 同一取舍，窗口 = 部署后到 scan 完成的连续步骤）。
- **check/finish 的 `get_prefs_xml` 仍是 run-as-only 旧拷贝**（`resume_task`/`start_task`
  读路径 root 下依赖 run-as 可用）：不在 #3463 §3 G1 清单内，未动；若 Owner 对 §2 裁决维持
  现状（C），下次改这两族时同构 port root 优先版。
- **helper 副本模型**：三族 `_lib.py` 仍是三份拷贝，本批逐族 port 即 #3463 §2 所述模型级
  问题的又一实例（A/B/C 待 Owner 裁决）。
