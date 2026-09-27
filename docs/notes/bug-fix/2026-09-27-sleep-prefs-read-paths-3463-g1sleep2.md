# sleep 三族 prefs 读路径按 kind 分流（#3463 §9 G1-sleep-2）：读不到不再等于「没有」

Status: implemented
Class: bug-fix

## Decision

按 #3463 v1.2 §9.2「G1-sleep-2」行实施（规划来源 = PR #3471 复核意见 B2–B4，规划者在
§9.1 第 2 条裁定「F1 的范围 = 六族内所有 prefs 读路径」；本单元补齐 sleep 半边，
powercycle 半边由姊妹单 G1-pc-2 交付）。三族（`sleep_setup`/`sleep_check`/`sleep_finish`）
`_lib.py` 逐字同构，参照实现 = 三族既有的 `read_prefs_evidence()`/`_root_read_prefs()`
（#3466 已合入的形态）：

1. **② `set_prefs(reset_count=false)`**：旧形态 `get_prefs_xml()` 把
   `transient/denied/empty/absent` 全折叠成空串，读空即 `current_count=0` 整写
   full map——重启窗的瞬时失败会覆盖续跑计数（与 #2979 误删同判据，只是写的是
   full map）。改为 `read_prefs_evidence()` 分流：`ok` 解析、`absent` 取 0（合法
   部署形态）、其余 raise（可重试文案）；raise 前不产生任何写。
2. **③ `start_task()`**：旧形态「读空跳过置 running=true、照常启动服务」让服务
   带 stop flags 起跑、设备重启后 boot receiver 不再续跑（表面启动、断链）。改为
   证据非 `ok` 即 raise，不启动服务。sleep 三族无 `resume_task`（那是
   powercycle_check 巡检形态），「如存在」分支不适用。
3. **④ `_verify_stop_flags()`**（仅 `sleep_finish` 有）：写后回读改走
   `read_prefs_evidence`——root 写成功时回读走 root 探测，不再因 shared-uid 包
   run-as 恒拒而两轮读空假失败（复核 B4）；真读不到保留既有「重试后 raise」语义
   （`set_stop_flags` 内部的同源判据抛可重试失败）。
4. **⑦ 死代码删除**：`sleep_finish/_lib.py` 曾有两个同名 `_verify_stop_flags`
   定义，首个含 `_verify_stop_flags()` 自递归、被第二个整体遮蔽（main 既有形态，
   #3466 Revisit 已登记）；本单删首个、在生效定义上完成 ④，并以
   `SourceGuard.of_repo_path(...).anchored("def stop_task(")
   .assert_count("def _verify_stop_flags", 1)` 钉住不得复现（#2639 棘轮姿势）。
5. **⑧ 未动**：`sleep_check` 为只读巡检，无 powercycle_check 式「收取窗口吞
   raise」结构（`grep set_stop_flags/start_task sleep_check.py` 零命中），
   §9.1 第一条裁定自然满足。

版本登记（§A）：`sleep_setup@1.0.5`、`sleep_check@1.0.6`、`sleep_finish@1.0.6`
（均 = head+1，无占位顺延）；`backend/schemas/pipeline_templates/sleep.json` 三个
step pin 同批追平（#2865/#2998 守卫，`--register` 后必红，教训见 #3466 返修）。

## Alternatives

- **`set_prefs` 继续用 `get_prefs_xml()`＋`try/except`**：被否——空串是证据折叠后
  的结果，`try/except` 无从区分 `absent`（合法取 0）与 `transient`（不得覆盖），
  正是 §9.1 判据要消灭的形态。
- **`start_task` 不可读时先 push `running=true` 到最小 map 再启动**：被否——
  合成最小 map 覆盖 full prefs 是 #3466 刚收紧掉的同一数据丢失出口；raise 给
  步骤重试即可。
- **`_verify_stop_flags` 在 root 下直接信任 `set_stop_flags` 无异常即通过**：被否——
  #894 的教训正是「写失败静默」，回读验证必须保留，只是证据源换成同源探测。

## Verification

- `python tools/dev/check_script_packages.py` → OK（35 族树与最新登记等价）
- `python tools/dev/check_tool_manifest.py --base origin/main` → OK（40 族 / 232 条目，append-only）
- `python -m pytest backend/agent/tests/ -q -k "sleep"` → **119 passed**
  （91 既有 + `test_sleep_read_paths_3463_g1sleep2.py` 28 条：② transient/denied/
  run-as 恒拒 raise 且无 push、absent 取 0、ok 保计数 ×3 族；③ 非 ok raise 且未发
  `start-foreground-service` ×3 族、ok 绿锚 ×3 族；④ root 写成功+run-as 拒不误判、
  恒不可读仍 raise、⑦ SourceGuard 只出现一次）；`test_sleep_scripts.py` 两条
  set_prefs 旧测改桩 `read_prefs_evidence`（新读路径的直接后果）
- `python scripts/run_gates.py check:quick` → OK（16 gates；worktree 需主树
  node_modules 软链，git 不捡）
- `python -m pytest tests/ -q` → 1927 passed / 1 failed（`test_payload_root_clean_3112`
  红因＝新测试文件当时未 commit，载荷根拒未跟踪文件；commit 后复跑绿）
- 变异自证：回退 ② 为旧「读空取 0」→ 3 条红；塞回遮蔽式重复定义 → SourceGuard
  钉红。测试有牙。

## Revisit

- 生效需 Owner 走 #3463 §5 v1.2 链（第 0 步：§9 全部单元复核通过；重指直接
  1.0.5/1.0.6/1.0.6，跳过中间版本）；本 PR 不激活。
- 姊妹单 G1-pc-2 的 ①（check/finish `get_prefs_xml` root 优先）在 sleep 半边已由
  #3466 覆盖，无事后可补。
- 其余约 20 族 F2 与 helper 副本模型（§2/§8.4）仍待 Owner 裁决，与本单无关。
