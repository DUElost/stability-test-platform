# 步骤停滞判据（#115）——阶段 1：引擎层能力

> **状态**：阶段 1 已落地（2026-08-02，PR 待审）。零行为变更，183 台现有脚本不受影响。
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
- 每收到 `PROGRESS` 行刷新 `last_progress_at`（经 `_update_execution_state`），供阶段 3 的 progress-aware barrier 使用

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
线程发，脚本 hang 住时照常上报，控制面不会回收）。schema 侧 `minimum:1` 的门
也保持关闭，随本条件一起开。

## 5. 后续阶段（未实施）

- **阶段 2**：脚本按 ADR-0020 新建版本接入 `PROGRESS` 打戳（`flash_firmware`
  打阶段序号，阶段推进时 `seq+1`；`monkey_setup` fill/push 用 `dd status=progress`
  或轮询 `stat -c %s`）；随后逐个 PlanStep 打开 `stall_seconds`
- **阶段 3**（#117 治本）：progress-aware barrier —— 等待方看 peer 的
  `last_progress_at` 是否在推进，推进则续期，全体停滞才启动超时钟。数据已就绪
  （coordinator `job_entries` 有 `execution_state` + `last_progress_at`）。
  前置：#117 需补 job→PRH 映射
- **阶段 4**：脚本内层钟（`_adb.py` 等）定位收窄为「防 adb 客户端挂死 +
  细粒度诊断」，缺省值校验 ≥ 外层配置
