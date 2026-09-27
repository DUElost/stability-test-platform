# monkey_setup v2.3.12：心跳 seq 进程级化 + 残余参数注入面收口（#3173 / B1-G4）

Status: implemented
Class: bug-fix

关联：[#3173](https://github.com/DUElost/stability-test-platform/issues/3173)（本单）、
[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 规划方案
§3 G4 / §4 G4）、#3107（log_dirs 判据来源，v2.3.11 已落）、clean_env `_next_progress_seq`
与 gpu_setup `decode_device_output`（参照实现）、#138/#139/#140（心跳保护动机）。

## Decision

按 #3463 §3 G4 执行，不重新设计。三个修复面：

1. **F4**：`_make_progress` 的 seq 由 per-closure 闭包计数（``state={"seq": 0}``）改为取
   `_adb._next_progress_seq()`（进程级 + 锁，port clean_env/v1.1.0 先例）。缺陷机理：
   init 闭包发到 seq N 后 push/fill 各闭包从 1 重启，引擎停滞钟只认进程内单调 seq
   （`pipeline_engine.py` `seq > last_seq`），后阶段心跳被当乱序戳**静默丢弃**——
   sha256/tar/adb-push/dd 这些 #138/#139/#140 要保护的慢而健康阶段照旧被杀。
2. **F3**（判据同 G3）：五处计划参数插值先校验再进 root 设备 shell，非法值整步转红——
   `fill_path` 限 `/data/local/tmp/` 下普通文件名（port clear_recents `validated_dump_path`
   判据 `^/data/local/tmp/[A-Za-z0-9._-]{1,64}$`，空串显式回落默认值）；
   `push.files[].remote` 限 `/data/`、`/sdcard/` 下绝对路径（判据同 `validated_log_dirs`）；
   `chmod` 限 `^[0-7]{3,4}$`；`pm uninstall` 包名与 `setprop` 键限 `^[A-Za-z0-9._-]+$`，
   setprop 值经 `shlex.quote`。step_push 的校验在任何 push 之前整单进行（不做一半再红）。
3. **F2**：`_adb.py` 全部 5 处 `subprocess.run` 改收字节 + `decode_device_output`
   （`errors="replace"`，port gpu_setup/_lib.py 先例）；`adb_shell_quiet`/`adb_shell_progress`
   的返回契约不变（CompletedProcess 仍持 str 字段）。

配套登记：`--register monkey_setup 2.3.12`（manifest append-only，2.3.12 未被占用，sha
`23798255cdae`）；模板 `monkey.json` / `monkey_watcher_patrol.json` 的 `script:monkey_setup`
pin 由 2.3.10 **直升 2.3.12（跳过 2.3.11）**；`tests/test_pipeline_template_script_pins_2865.py`
的 monkey_setup EXCEPTIONS 条目按其自书删除条件（pin 追平即删）移除，清单回到空。
既有 `test_script_progress_stamps.py` 三处 seq **精确值断言**（`== [1, 2]`）随 F4 语义改为
严格递增断言——进程级计数器下精确值与测试执行顺序耦合，严格递增才是 #3173 之后的真契约。

白名单合法性核查（§7 退回条件③）：生产库 `plan_step` 全部 8 条 `script:monkey_setup`
（pin 2.3.9/2.3.4）`params` 均为 `{}`，两模板该步骤 `params` 亦为 `{}`——fill_path/remote/
chmod/包名/setprop 无任何现网实值，白名单不可能拒绝既有合法参数；默认值
（`/data/local/tmp/fill.bin` 等）全部过校验（有反例测试钉住）。

## Alternatives

- **F4 在引擎侧放宽判据（seq 回退也认）**：会放过真正的乱序/重放戳，动摇停滞钟语义；
  且参照先例 clean_env 已是进程级计数器，port 成本最低。
- **F3 只修 `fill_path`、放掉 chmod/remote/包名/setprop**：#3463 §1 F3 行明确这五处同形
  （#3173 现象 1 的修复面）；漏一处即同形态复发。
- **顺带修 `step_root` / `step_install` 在 `monkey_setup.py` 体内的直连 `subprocess.run(text=True)`**：
  G4 行的 F2 范围是「`_adb.py` 解码」（helper 层），脚本体内的两处不在方案面内（§8 第 4 条
  统一登记为证据），不顺手扩面。
- **EXCEPTIONS 保留 + pin 停 2.3.10**：与 #3463 §3 G4「pin 直升 2.3.12」直接矛盾；守卫
  判据是机检的（pin==head 即须删例外），保留必然红。

## Verification

- `python -m pytest backend/agent/tests/test_monkey_setup_3463_g4.py -q` → **60 passed**
  （新增：F4 三闭包交替 seq `[1..6]` 严格单调 / 两闭包 seq 无重复 / main() 全局单调；
  F3 五个校验器合法/非法参数化 + step 级反例（非法值整步转红且零 adb 调用、shlex.quote
  生效、默认值过白名单）；F2 坏字节解码；两模板 pin = 2.3.12）。
- **反例（变异验证）**：把 `_make_progress` 临时回退为 per-closure `itertools.count` 原形后
  复跑 → `test_seq_monotonic_across_interleaved_closures` 与
  `test_two_closures_never_share_a_seq_value` 双红（seq 回退/重复被抓住）；恢复修复后
  60 passed。
- `python -m pytest backend/agent/tests/ -q -k "monkey"` → **132 passed, 2136 deselected**。
- `python -m pytest backend/agent/tests/test_script_progress_stamps.py
  tests/test_pipeline_template_script_pins_2865.py -q` → **23 passed**（更新后的 seq 契约 +
  pin 守卫，EXCEPTIONS 清空下牙测试仍绿）。
- `python tools/dev/check_script_packages.py` → 绿（35 个族树与最新登记等价）。
- `python tools/dev/check_tool_manifest.py --base origin/main` → 绿（40 族 / 223 条目，
  append-only）。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。
- 分支开发中途 G2（#3464）/G6（#3465）合入 main：`git merge origin/main` 干净自动合并
  （tool_manifest 异族块），合并后上述门禁全部复跑仍绿。

## Revisit

- **生效未做**：按 #3463 §5 由 Owner 窗口统一部署 + publish + scan + 重指；本单合入不等于
  设备端已用上 2.3.12。plan_step 存量 pin（2.3.9/2.3.4）随版本治理批次刷新，不在本单。
- **pin 先于注册的窗口**：模板已 pin 2.3.12，`script` 表注册（scan）发生在部署窗口内——
  该窗口内由 monkey 模板新建 Plan 会在 prepare 的 `_validate_script_refs` 422。窗口 =
  合入后到 Owner 窗口 scan 完成；与 G2 的 gpu_finish 同一取舍（#3463 §5 整批一次激活）。
- **脚本体内两处直连 `text=True`**（`step_root` 的 adb root、`step_install` 的 dumpsys/pm
  install）：G4 行 F2 范围外，按 §8 第 4 条随其余约 20 族登记为证据，等 #3463 §2 裁决。
- **helper 副本模型**：`_adb.py` 每族一份的副本仍是模型级问题（#3463 §2，A/B/C 待
  Owner 裁决）；若走 A（机械守卫），本次 port 的 `_next_progress_seq` /
  `decode_device_output` 与 clean_env/gpu_setup 的字节级差异需先对齐。
