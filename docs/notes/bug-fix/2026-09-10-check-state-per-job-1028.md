# check 族巡检状态按 Job 隔离（#1028）

Status: implemented
Class: bug-fix

## Decision

#1028（R08-F10）：check 族脚本的跨周期状态文件只含**设备序列号**
（`mtbf_check_{serial}.json` 等），无 Job 身份——`dead_streak` / 周期序号 /
收取窗口标记跨 Job 继承。前一 Job 留下死亡计数后，新 Job 首次巡检若恰遇服务
暂不可见，会继承旧计数提前判死，耗尽本应属于新运行的宽限周期。「服务可见
清零」是既有防护，但只解决恢复，不解决跨运行继承。

修复（四个同型脚本各发新版本——**同一缺陷不在四个目录留三个**）：

| 脚本 | 新版本 | 状态字段 |
|---|---|---|
| mtbf_check | **v1.4.0** | dead_streak / seq |
| gpu_check | **v1.0.6** | dead_streak / seq |
| powercycle_check | **v1.0.7** | seq / last_online / collecting_done_for_window / last_collected / collect_error |
| sleep_check | **v1.0.2** | dead_streak / seq |

- 统一守卫（各 `_run` 在 `_load_state()` 之后）：状态写入 `job_id`
  （Agent 注入的 `STP_JOB_ID`）；`state.job_id != 当前 job_id` 即**整体重置**为
  `{"job_id": ...}` —— 所有状态键本就是「单次运行」语义，整册重置比逐键点名
  更不易漏（powercycle 的收取窗口标记就是逐键方案会漏的例子）；
- 身份缺失（手动直跑）时与残留旧键不等同样重置一次，此后维持既有语义；
- 同 Job 崩溃恢复仍延续计数（验收标准 2）。

与 ADR-0033 的关系：无直接约束——本单在 ADR-0020 版本域内；`STP_JOB_ID` /
fencing 语义关联的是 ADR-0019（lease）。守卫只用 job_id 不用 fencing_token：
巡检是只读观察，不涉及上传授权，fencing 不匹配在这里不是有效信号。

## Alternatives

- 状态文件名含 job_id（`mtbf_check_{serial}_{job}.json`）：隔离彻底但旧文件
  永不清理，随运行次数累积；单文件 + 身份键等价且自清理；
- 只重置 dead_streak 不动 seq：seq 也是单次运行语义（PROGRESS 序号跨 Job 续
  号会误导下游 seq 单调判定），一并重置；
- 只修 mtbf_check：GPU/Sleep/PowerCycle 同病（grep 证实同型 `_state_file`），
  留着等于让同一缺陷按脚本逐个复发。

## Verification

- `pytest backend/agent/tests/test_check_state_per_job.py`：6 passed——mtbf 三例
  （同 Job 计数累加判死 / **新 Job 不继承：streak 2→1 且 seq 重置 1** / 同 Job
  恢复清零）+ gpu/sleep 同型重置 + powercycle 富状态（收取窗口标记不跨 Job）；
- `check-script-version-immutability.py --base origin/main`：OK（四个旧版本
  原地未动）；`backend/agent/tests` 全目录通过；ruff 干净。

## Revisit

- 状态文件仍在 `tempfile.gettempdir()`（Agent 本机盘），本单不迁移位置；
- `STP_JOB_ID` 由 pipeline_engine 注入（env），手动直跑无此 env 时首跑重置、
  后续沿用——若将来手动跑也要隔离，需显式传 job 参数；
- powercycle 的收取窗口与 `_in_collect_window` 时钟逻辑未动——若窗口跨越
  Job 边界的场景需要「新 Job 从下个窗口开始」，另立单（本单只保证状态不继承）。
