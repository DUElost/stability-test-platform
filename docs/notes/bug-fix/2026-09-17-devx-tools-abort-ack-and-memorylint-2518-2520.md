# devx 工具批：假 Agent 回报 abort 终态 + memory_lint 退出码分层/未索引降级（#2518 / #2520）

Status: implemented
Class: bug-fix

## Decision

**#2518 假 Agent 收到 `control(abort)` 必须回报终态（不能只回 ack）。**

- `push_ack` 对 `control` 改走新的 `control_ack`：从服务端扇出的载荷里取
  `payload.job_ids`（形状取自 `plan_run_abort.py` 的实际 emit：`{"command": "abort",
  "payload": {"plan_run_id", "job_ids", "reason"}}`），逐 job 走
  `POST /api/v1/agent/jobs/{id}/complete {status: ABORTED}`——**只回报，不执行**
  （红线 1 不变）。
- `control` 之外的事件与 `control` 的非 abort 命令保持原样（只回 ack，零副作用）。
- `complete` 子命令的 `--status` 放开 **`ABORTED`**（state machine 允许
  `RUNNING → ABORTED`；它是中止链上唯一正确的终态值，此前 CLI 连手工模拟都做不到）。
- HTTP 调用抽成 `report_job_status()`，`cmd_complete` 与 `control_ack` 共用（同一份
  fencing token 取用与 payload 形状）。

**效果**：dev 上中止一个正在执行的 job 从「abort_reaper 宽限 → UNKNOWN → 再等 300s
（实测 6 分 15 秒收口）」变为**走产品侧快路径**（Agent 回报 ABORTED → 即时释放租约，
~4s），于是「中止→即时释放」这条边第一次进入 dev 可回归范围。

**#2520 memory_lint 两处信号缺陷。**

1. **退出码分层**：`--budget` 模式下**以预算为退出码主判据**——此前错误门在前，
   「索引在软触发以内」却因断链/未索引 `exit 1`，闸恒红且无法从退出码区分两种状态。
   现在该模式额外打印机器可读结论行 `budget=ok|over-soft|over-hard`；错误/警告仍在
   stdout 全量打印。**不带 `--budget`（或加 `--strict`）时错误照旧致红**——分层不是放水。
2. **未索引文件降级为 WARN**：store 政策有三类去处（判据进索引 / **流水不进索引** /
   台账进索引原地改写），工具不做类别识别就无从判定哪些「该进」——一律 ERROR 让本仓
   永不可能全绿，还会把真信号（正文断链）淹掉。告警文案里写明政策依据；
   `--strict` 仍把 WARN 计入退出码，需要硬门时可用。

**政策没有改**（issue 要求二选一）：选「工具降级」而非「政策改成必须索引」——理由是我
不能从本仓看到 `memory-store-governance` 的全文（它不在本仓库），而「流水不进索引」是
issue 转述的既有政策；把政策改严会连带要求所有流水/快照回填索引，代价与收益都不在本单
可验证的范围内。若 store 侧政策文档的表述与本单读数不同，应在那一侧对齐（见 Revisit）。

## Alternatives

- **#2518 A. 让夹具在收到 abort 后**kill** 本地进程**：不适用——夹具从不执行脚本（红线 1），
  没有进程可杀；回报终态就是「守约 Agent」的全部行为。
- **#2518 B. 只在 `complete` 里放开 ABORTED、不改 push_ack**：否决。那只能手工模拟，
  dev 的自动回归仍走 6 分钟慢路径（issue 的一半诉求）。
- **#2520 A. 把预算改成唯一退出码（不带 --budget 也只看预算）**：否决。那会让断链不再
  致红——错误门被削弱而非分层。
- **#2520 B. 在工具里加「流水/快照」白名单（按文件名模式）**：否决。实测的 4 个孤儿命名
  各异（`*-standup-recap`、`claimable-issue-set-*`），模式匹配既盖不全也容易误伤；
  降级为 WARN + 政策提示更诚实，且 `--strict` 保留了收紧手段。

## Verification

- **#2518**：`tests/test_dev_fake_agent.py` 27 passed——新增 5 例（abort 逐 job 回报 ABORTED、
  非 abort 命令零副作用、`push_ack` 路由到 `control_ack`、CLI 接受 `--status ABORTED`、
  CLI 真把状态发进 patch 体）；原「入站 handler 只回 ack」用例按新契约拆分（control 不再
  属于「只回 ack」）。
- **#2520**：`tests/test_memory_lint.py` 45 passed——未索引文件改为断言 WARN + 政策文案；
  新增 `TestBudgetExitCode` 三例（预算内+有错误 → 0 且打印 `budget=ok`；越软触发 → 1 且
  `budget=over-soft`；不带 `--budget` → 错误仍致红）。**红绿差分**：三个新/改用例在基线
  工具上全红，换回新实现全绿。
- 两个套件合计 72 passed；`ruff check`、`check:quick`（10 gates）通过。
- **未做**：#2518 未在真实 dev 栈上跑完整中止（需要用假 Agent 起一次 plan run）；改动本身
  由单测覆盖（回报路径的 HTTP 与状态值都断言到了）。

## Revisit

- **#2518 的端到端验证**：本单只测了回报逻辑；「dev 上中止一个 RUNNING job 在数秒内收口」
  需要一次带真实 plan run 的演练（假 Agent + dev 栈）。若下次演练仍走 300s 慢路径，先看
  `control` 的载荷里 `job_ids` 是否为空（服务端扇出按 host 分组）。
- **#2520 的政策侧**：`memory-store-governance` 不在本仓库，本单按其转述的「流水不进索引」
  实现；若那份文档其实要求「全部索引」，应改政策并同步把这里的 WARN 收回 ERROR。
- **`--budget` 的语义变更**：它与 `--strict` 同时使用时以预算为准。若将来要「预算与错误
  都要拦」，应引入显式的 `--gate=budget|errors|both` 而不是复用两个布尔。
