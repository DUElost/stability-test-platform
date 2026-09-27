# powercycle 三族 v1.2.8 / 1.0.10 / 1.0.8：prefs 读路径全部 root 优先且区分 kind（#3174 #3346 / B1-G1-pc-2）

Status: implemented
Class: bug-fix

关联：[#3174](https://github.com/DUElost/stability-test-platform/issues/3174)（powercycle_setup F1）、
[#3346](https://github.com/DUElost/stability-test-platform/issues/3346)（生效面），
[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 方案 **v1.2 §9**：§9.1 裁定一、二 + §9.2 G1-pc-2 行）、
#3471（G1-pc 前序 PR）及其复核评论（B1–B4 退回项）、#2979（参照判据）、#813（收取窗口重试语义）。

## Decision

按 #3463 v1.2 §9.2 G1-pc-2 行执行，不重新设计（参照实现：powercycle_setup 的
`_root_read_prefs()` 与 root 优先的 `get_prefs_xml()`）：

1. **① check / finish 的 `get_prefs_xml` 改 root 优先**：port setup 1.2.3+ 同构形态——
   root 下用 `_root_read_prefs` 单调用同源探测，无 root 才回落 run-as。run-as-only
   读路径对 platform 签名 shared-uid 包恒空（#3088 现场判据），是 B3/B4 的共同根。
2. **② 三族 `set_prefs(reset_count=false)` 按 kind 区分**：`ok` 解析 `current_count`；
   `absent` 取 0（合法首跑，整写即 fresh）；`transient`/`denied`/内容空（`empty`）
   raise——不得在读不到时以 0 整写完整 prefs 覆盖健康续跑计数（复核 B2）。非 root
   下 run-as 读空不作 absent 证据，同样 raise。
3. **③ 三族 `start_task` + check `resume_task`**：prefs 不可读（非 `ok`）即 raise，
   不再「跳过 `running=true`/`auto_resume=true` 却照常启动服务」（复核 B3：表面恢复，
   reboot 后不再续跑）。
4. **④ finish `_verify_stop_flags` 回读 root 优先 + kind 区分**：`ok` 且含
   `running=false` ⇒ 通过；root 写成功但回读被拒/超时不再假失败（复核 B4）；
   真读不到（transient/denied）按既有语义重写后仍不 ok 才 raise。
5. **§9.1 裁定一（不改步骤语义）**：`powercycle_check` 收取窗口对 `set_stop_flags`
   raise 的承接**原样保留**——`collect_error` + 下周期重试 + 补偿 resume +
   `success=true` 是 #813 的可重试机制，不升级为步骤失败。补顶层反例钉住该链。
6. 登记 `powercycle_setup` **1.2.8**、`powercycle_check` **1.0.10**、
   `powercycle_finish` **1.0.8**；模板 `powercycle.json` 三步 pin 同批追平（#2865）。

## Alternatives

- **给收取窗口把 prefs 证据不确定类错误分流为步骤失败**（#3471 复核 B1 的修法选项）：
  §9.1 裁定一明确否决——那是 #813 刚修掉的「瞬态 adb 读失败放大为 Plan FAILED」。
- **`set_prefs` 非 root 读空时区分 absent**（run-as 哨兵探测）：新增参照实现不存在的
  helper，超出「同构 port」；该包 run-as 恒拒，非 root 本就写不进去，raise 等价。
- **`start_task` 对 `absent` 放行**（文件不存在时跳过写、照常启动）：§9.2 的判据是
  「非 ok 即 raise」——absent 在 start 时刻意味着 set_prefs 写入被静默回滚，属于异常态，
  按可重试失败处理更安全。
- **顺带把 `powercycle_check.py` 入口的 `_adb_grep`/`_run_finished` 的 `text=True` 一并改掉**：
  F2 只覆盖「本批升版本族的 `_lib.py` adb helper」（#3463 §1/§6），入口脚本调用点不在
  本单元修复清单内，不顺手扩。

## Verification

- `python -m pytest backend/agent/tests/test_powercycle_prefs_read_paths_3463_g1pc2.py -q`
  → **23 passed**（② transient/denied raise×3 族 + ok/absent 正控制、③ start×3 族 +
  resume + 可读正控制、④ verify root 写成功/run-as 被拒不假失败 + transient 重试后
  raise + 真写失败重试 raise、**§9.1 顶层反例**：真实 `_run → pause_task →
  set_stop_flags` 链——prefs 未被覆盖、`collect_error` 带 `kind=transient`、补偿 resume
  已尝试、`success=true`）。
- **反例（变异验证）**：把 check 的 set_prefs/start_task/resume_task 临时退回
  「读空即 0 / 读空即跳过照常启动」、finish 的 `_verify_stop_flags` 退回 run-as-only
  回读后复跑 → 恰好对应的 5 条用例转红；恢复后 23 passed。
- `python -m pytest backend/agent/tests/ -q -k "powercycle"` → **137 passed, 2279 deselected**
  （含既有 `test_set_prefs_reset_count_false_preserves` 夹具跟上：v1.2.8 起 set_prefs
  先判 is_root，该用例补 `is_root→False` stub 走 run-as 分支，断言原意不变）。
- `python tools/dev/check_script_packages.py` → 绿（35 族树与最新登记等价）。
- `python tools/dev/check_tool_manifest.py --base origin/main` → 绿（40 族 / 232 条目，
  append-only）。
- `python -m pytest tests/ -q` → **1928 passed, 18 skipped**（提交前首跑唯一失败为
  `test_real_repo_payload_root_is_clean`——新测试文件未提交所致的载荷根未跟踪文件
  检查；归位提交后复跑通过）。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。

## Revisit

- **生效未做**：按 #3463 §5 由 Owner 窗口统一部署 + publish + scan + 重指
  （1.2.8 / 1.0.10 / 1.0.8）；powercycle.json 已 pin 新版本，部署窗内 scan 前
  由模板新建 Plan 会 422（与 G2/G4/G1-pc 同一取舍）。
- **check 巡检进度读路径行为变化**：`get_prefs_xml` root 优先后，`_read_prefs_progress`
  在 root 下从「恒空 → grep 兜底」变为「真正读到 prefs 权威计数」——这是 F1 范围内的
  修复效果而非语义破坏；patrol 读仍宽容（读不到回落 grep，不 raise），未被 §9.2 触碰。
- **入口脚本的 `text=True` 残留**（check 的 `_adb_grep`/`_run_finished` 等约 20 族同款）：
  仍按 #3463 §8 登记 为证据，等 §2 模型级裁决。
- **`_verify_stop_flags` 的重试预算**：transient 下实际由 `set_stop_flags` 的读取
  raise 提前终止重试环（等效「重写路径失败即 raise」）；若复核者要求保留两次完整
  尝试的旧循环形态，需先修订 §9.2 ④ 的措辞。
