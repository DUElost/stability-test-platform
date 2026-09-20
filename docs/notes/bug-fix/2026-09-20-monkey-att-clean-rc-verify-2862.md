# monkey_setup v2.3.9：att_clean 按 rc 核验 + 结构化出口（#2862）

Status: implemented
Class: bug-fix

## Decision

`att_clean`（清 AutoTestTool 残留，防 monkey 与 powercycle/sleep 叠加）在 v2.3.8 引入
降级语义时没有配套判据：`_adb.adb_shell` **不看 returncode**，只在超时时抛异常，于是

1. `am kill` rc≠0（设备 offline / 命令被拒）不抛异常 ⇒ 无条件记「已走 am kill 兜底」；
2. `am force-stop` **非超时失败**连 `except` 都不进 ⇒ 连 warning 都没有；
3. 两条都失败仍 `success=True, att_prefs_cleared=True`；prefs `rm` rc≠0 同理判绿
   （docstring 自陈「prefs 真失败值得红」与实现相反）。

**v2.3.9**（新版本目录，v2.3.8 逐字节不动——ADR-0020 已发布版本不可变）：

1. 三条命令改 `adb_shell_quiet`（模块内已导入），**按 rc 定性**，rc≠0 的 stderr 摘要
   进 warnings/errors；
2. 降级结论**结构化**：`metrics.att_stop_issued` / `metrics.att_prefs_cleared`——
   `pipeline_engine` 只读顶层 `success` / `error_message` / `metrics` / `skipped`，
   嵌套 `att_warnings` 只能落 output 文本（64KB 截断），进不了观测面；
3. `att_prefs_cleared` 如实反映结果，失败路径不再返回 `True`；
4. **语义不变**的两条：停止命令失败仍不判败 init（#2777 的意图——AM 卡死窗里清不掉
   ≠ 不能跑 monkey）；prefs 清理失败仍判败（#894 的叠加风险依赖它）。
5. 模块头补一条 v2.3.9 差异说明（v2.3.8 的作者把说明写在 step docstring 里，
   版本日志当时未更新；本单沿用其做法并在两处都写）。

**边界（不主张）**：`am kill` 只杀「可安全杀」的后台进程，**rc=0 不等于进程已死**；
本版本核验的是「命令是否被设备接受」。真机进程判据（`ps` 二次核验）见 Revisit。

## Alternatives

- **原地改 v2.3.8**：弃——`script.content_sha256` 是扫描时冻结的期望值，原地改会
  产生 conflict，引用该版本的 Plan precheck 直接 `script_verify_failed`（ADR-0020）。
- **把停止失败改判 errors**：弃——与 #2777 的裁决相反（.59 六窗因 force-stop 挂满
  超时把整个 init 判败）。
- **让控制面消费 `att_warnings`**：弃——要改 `pipeline_engine` 的输出契约与步骤
  状态语义，超出本单；改用引擎**已经**消费的 `metrics` 出口，成本是零新的契约面。
- **`am kill` 后加 `ps` 核验进程存活**：本单不做——issue 明确「不主张兜底无效」，
  且 monkey 的正确性不依赖它；真机样本到手再议。

## Verification

- **反例优先（先证伪再采信）**：把 `v2.3.9/monkey_setup.py` 临时换回 v2.3.8 的实现后，
  `test_monkey_setup_v239.py` **5/6 红**（余下 1 条是对照锚点，按设计断言旧版行为）；
  恢复后 **11 passed**（v2.3.9 新用例 + v2.3.8 既有回归）。
- 对照锚点 `test_v238_same_rc_scenario_reports_clean_contrast_anchor`：同 rc 场景 v2.3.8
  `success=True, att_prefs_cleared=True` 且**无 metrics**——差异确由本版本引入。
- 用例覆盖：全 rc=0；force-stop rc=1 → kill rc=0（warning + `att_stop_issued=1`）；
  两条 rc=1（**`att_stop_issued=0`**，不再把未核验的兜底记成成功）；prefs rc=1（判败）；
  超时路径（#2777 的原始形态，语义不变）；超时收窄与顺序（10s / force-stop→kill）。
- `env -i PATH=… PYTHONPATH=. python -m pytest backend/agent/tests/ -q` → **2072 passed**
- `pytest backend/tests/services/test_script_catalog_capabilities.py -q` → **8 passed**
- `python tools/dev/check-script-version-immutability.py --base origin/main` →
  **OK：没有已发布版本目录被原地改动**
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (12 gates)**

## Revisit

- **生产接入未做**：新版本要在平台上生效需 scan 登记 + plan_step 重指（与 #2865 同族）。
  本单只交付版本目录与判据，未改任何生产配置或种子迁移。
- **真机判据**：`am kill` rc=0 是否真让 `com.tinno.autotesttool` 进程消失，需一次真机
  样本；若证明 rc=0 也常杀不掉，再考虑 `ps`/`pidof` 二次核验并把它并入 metrics。
- `att_warnings` 仍是嵌套文本（output 64KB 截断）：本单把**关键降级量**（是否下发过
  停止、prefs 是否清掉）放进 metrics；其余自由文本暂不迁移，若将来要进观测面应
  先定义结构化契约。
- v2.3.8 的版本日志缺口（模块头未记 v2.3.8 差异）未回填——已发布版本不可变，
  回填只能等下一次改版或另立文档，本单不追。
