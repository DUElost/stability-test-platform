# 步骤停滞判据（#115）——阶段 1：引擎层能力

> **状态**：阶段 1 已落地（2026-08-02）；**阶段 2 大面积接入**、**阶段 3 Part 1 已落地**
> （#872，2026-09-13）——逐阶段实况与判据变更见 §5（2026-09-18 回写，基线 `3aa0638e`）。
> 阶段 1 零行为变更，现有脚本不受影响。
> **关联**：#115（本提案）、#114（内层钟可配）、#117（progress-aware barrier 治本）。
> **配套**：`backend/agent/pipeline_engine.py` 的 `_resolve_step_stall_seconds` / `_pump_process`。

---

## 1. 为什么停滞钟必须缺省关闭

「任意输出 = 活」这条判据在当前脚本集上**不成立**：

全部 17 个脚本的 `adb_shell` / `adb_push` / `subprocess.run` 都用 `capture_output=True`，
子进程输出被脚本吞掉、从不转发。实测 **14 个脚本全程零输出**，另 3 个的唯一
`print` 就是末尾的 `output_result`。

如果给一个默认 120s 的停滞钟，`push`(预算 600s)、`fill`(预算 300s) 以及将来的
刷机步骤都会**在 120s 被杀**，183 台同时 —— 比 2026-08-01 那次 `fill` 事故
（只死 22%）严重得多。

所以：**`stall_seconds` 缺省 0 = 关闭**，只能逐个 PlanStep 显式打开，且前提是
该步骤的脚本已接入 `PROGRESS` 打戳（阶段 2）。

例外只有一个：`STP_STEP_STALL_SECONDS` 环境变量会**全机（fleet）启用**，作用于
所有未显式配置的步骤，绕过逐个 PlanStep 的灰度闸门。它是**灰度后期开关**——
必须等全部相关脚本接入打戳后才能设置，否则等于 183 台一起误杀。

## 2. 阶段 1 交付物

引擎能力 + 测试，**零行为变更**：

- `_resolve_step_stall_seconds(step)`：`PlanStep.stall_seconds` → `STP_STEP_STALL_SECONDS` → `0`（关闭）。沿用 `_resolve_step_wall_clock` 的解析链模式
- `_pump_process`：双 reader 线程 + 主线程双钟
  - reader A（stdout）：**全量保留**，不识别 `PROGRESS` —— stdout 整份要过 `json.loads`，是既有结果契约
  - reader B（stderr）：识别 `PROGRESS ` 前缀 → **丢弃**并刷 `last_progress`；普通输出只进缓冲、不刷钟
  - 主线程：`poll()` 轮询（间隔 1s），判总时长钟与停滞钟；触发后 `_terminate_process_tree` → `wait` → `join` 两个 reader
- `_run_script_action` 接入：超时文案区分钟 —— `script timeout after Ns`（总时长）vs `script stalled after Ns of no progress`（停滞）
- 每收到 `PROGRESS` 行刷新 `last_progress_at`（经 `_update_execution_state`），供阶段 3 的
  progress-aware barrier 使用 —— **该用途已被 #872 修订**：barrier 不再以戳新鲜度为判据，
  `last_progress_at` 降级为诊断信号（超时日志里的 peer 快照），详见 §5 阶段 3 行

### 实现细节（都是规模上才会暴露的坑）

| 坑 | 解法 |
|---|---|
| `communicate()` 期间无法观测存活 | 双 reader 线程；`selectors` 不支持 Windows 管道，顺序 `readline` 会因另一管道写满而双向死锁 |
| 管道不 EOF | 脚本调 `adb`，其常驻 server 可能继承管道写端。POSIX reader 走非阻塞 + `select`，主线程在 1s 宽限后 `stop` 打断它（~0.2s 内退出，不留线程）；Windows 无 selectable pipe，退化为阻塞 `readline()` + join 超时放弃。宁可丢几行输出，不能挂住主线程（permit 还握在手里） |
| `PROGRESS` 行污染输出 | 仅 stderr reader 识别即丢弃，不进任何缓冲。12h 步骤每 5s 一戳 = 8640 行，会把真正的报错挤出 64KiB 截断窗口 |
| stdout JSON 契约 | stdout/stderr **分开缓冲**，stdout 全量重组 → `json.loads` 不受影响；8MiB 捕获兜底对两流都生效（超限丢弃、继续读），64KiB 展示截断作用于合并输出（`error_message` / `output`） |
| `last_progress` 跨线程 | reader 写、主线程读。安全的前提是**单次属性赋值**（CPython 下原子）；不许写成读-改-写的复合操作 |
| 测试自身被误杀 | 测试 spawn 必须带 `_popen_isolation_kwargs()`，否则 `killpg` 会把 pytest 自己 SIGTERM（实测 exit 143） |

判定精度：墙钟与停滞钟都受主线程 1s 轮询影响，触发时间 ≈ 阈值 ±1s（原
`communicate(timeout=…)` 的墙钟是准点触发，换成轮询后边界上最多晚 ~1s）。

### #147 收口说明（2026-08-05）

- **PROGRESS 行首约束**：实现按 `line.lstrip()` 后匹配前缀，脚本带前导空白
  （缩进 / 日志前缀）也能刷新停滞钟；协议仍建议从行首输出，不带前导空白。
- **8MiB 捕获上限**：已有集成测试覆盖——单流输出超限后截断、继续读管道不
  阻塞、子进程正常退出，并打 `step_output_capture_limit_reached` 告警。
- **Windows 残留 daemon reader**：Windows 管道不可 `select`，阻塞
  `readline` reader 在 join 超时后被放弃，daemon 线程随进程退出消亡。生产
  Agent 跑 Linux，风险低；如需 Windows 联调，应另行评估非阻塞 reader 或
  进程级隔离。

## 3. `PROGRESS` 打戳协议（阶段 2 启用，阶段 1 已解析）

脚本在长耗时操作期间自愿往 **stderr** 打：

```
PROGRESS {"seq": N, "step": "fill", "written_kb": 12345, ...}
```

- **`seq` 单调递增是唯一判据**；语义字段仅供人读诊断
- 重复打同一句话时 `seq` 不涨 → 被判停滞。这是**诚实的**：那证明的是"进程还活着"，不是"还在推进"
- 放 stderr 而非 stdout：stdout 整份要过 `json.loads`，是既有结果契约
- 不识别该协议的脚本行为与今天完全一致

## 4. 两层钟与 `0=不限` 的开门条件

| 钟 | 解析 | 缺省 | 语义 |
|---|---|---|---|
| 总时长钟 | `PlanStep.timeout_seconds` → `STP_STEP_WALL_CLOCK_SECONDS` → 300 | 300 | 安全网，**不**是完成判据 |
| 停滞钟 | `PlanStep.stall_seconds` → `STP_STEP_STALL_SECONDS` → **0(关闭)** | 关闭 | 多久无推进算卡死 |

**`timeout_seconds=0`(不限) 的开门条件是按步骤的**：只有「该步骤脚本已接入
`PROGRESS` 打戳 且 该步骤显式开了 `stall_seconds`」时它才安全。没开停滞钟的
步骤配 `0`，依然等于"卡死永远占住一个 permit"（执行心跳由 coordinator 独立
线程发，脚本 hang 住时照常上报，控制面不会回收）。schema 侧 step 级
`timeout_seconds` 已于 2026-08-04 放宽到 `minimum: 0`——但 `pipeline_schema`
同时强制：`timeout_seconds` 恰为 `0` 时必须显式配 `stall_seconds >= 1`
（stall 联动门，`pipeline_engine.py` docstring 为准），即「0 只对已接打戳 +
显式开停滞钟的步骤表达」——与上文开门条件一致，不再是「待开」。

## 5. 阶段进度（2026-09-18 回写 · 基线 `3aa0638e`）

> **为什么要回写**：本节原题为「后续阶段（**未实施**）」，而阶段 3 的 Part 1 已于 2026-09-13
> 落地（`15a6bb45`，#872），阶段 2 也已大面积接入。更糟的是原文对阶段 3 判据的描述与 tip
> 代码**相反**——「文档说未实施 + 代码已改判据」同时成立时，按文档行事的人会去修一个已经
> 不存在的缺陷。逐条状态如下，每条附 `file:line`；计数一律给出**可重算的口径**，不抄手。

| 阶段 | 状态 | 现状与落点 | 重算口径 |
|---|---|---|---|
| **1** 引擎层能力（双 reader + 停滞钟解析） | ✅ 已实施 | `_resolve_step_stall_seconds` / `_pump_process`（本文 §2） | — |
| **2** 脚本接入 `PROGRESS` 打戳 → 逐步骤开 `stall_seconds` | 🟡 **打戳侧 24/34 族已接入**；`stall_seconds` 仍默认关闭 | 发射器在各族的 `_adb.py` / `_lib.py` 里：`sys.stderr.write(f"PROGRESS {json.dumps(payload, ...)}")`（如 `clean_env/v1.1.0/_adb.py:75`、`gpu_check/v1.0.9/_lib.py:131`、`sleep_setup/v1.0.2/_lib.py:102`；`flash_firmware/v1.3.16/flash_firmware.py:691` 用 `_PROGRESS_PREFIX` 常量形态） | 族数：`grep -rl 'PROGRESS ' backend/agent/scripts --include=*.py \| sed -E 's\|backend/agent/scripts/([^/]+)/.*\|\1\|' \| sort -u \| wc -l` → 24；分母：`ls -d backend/agent/scripts/*/ \| wc -l` → 34。**判据必须写明**：换成更窄的 needle（只认 `_PROGRESS_PREFIX =`）会得出 4——那是假阴性，同一族可有两种发射器写法 |
| **3** progress-aware barrier（#117 治本） | 🟡 **Part 1 已落地**（#872），且**判据与原设想不同** | `pipeline_engine.py:1382-1412` `_peers_are_progressing`：`WAITING_EXECUTION_SLOT` 与 **`EXECUTING_STEP` 执行态本身**都算活性证据；原文的「看 peer 的 `last_progress_at` 是否在推进，推进则续期」**已作废**——戳新鲜度降级为超时日志里的诊断快照。理由写在 docstring 里：脚本打戳覆盖率不齐（长步骤如装包/刷机未必刷新戳），旧判据会把合法长步骤当停滞、**误杀早完成者（run 338 实证）**。信任执行态必须有兜底：`_DEFAULT_BARRIER_MAX_WAIT_SECONDS`（`:1445-1456` 应用、`:226`/`:235` 定义），旋钮 `STP_BARRIER_MAX_WAIT_SECONDS=1800`（`:226`），Plan 显式配置优先，0/负值 = 不设上限（保留 #174 调试语义） | 判据变更史：`git show 15a6bb45 --stat`；旋钮登记：`docs/development/environment-variables.md:326` |
| **4** 脚本内层钟（`_adb.py` 等）定位收窄 | ⬜ 未实施 | 仍是「防 adb 客户端挂死 + 细粒度诊断」之外的原语义；缺省值 ≥ 外层配置的校验未做 | — |

**阶段 3 未做完的部分**（保持本单可继续跟踪，不宣称收口）：

- 「全体停滞才启动超时钟」这一原始设想，在 #872 之后**语义已变**——现判据是「只要有一个
  peer 处于执行态/排队态就续期，硬顶到点终止」。是否还需要按原设想收紧，属 #117 的裁决，
  不由本设计稿单方面宣布；
- `stall_seconds` 的逐步骤开启（阶段 2 的后半）仍未推进：`PlanStep.stall_seconds`
  （`backend/models/plan.py:109`）可空、缺省关闭，缺省值链见 `environment-variables.md:402`。
