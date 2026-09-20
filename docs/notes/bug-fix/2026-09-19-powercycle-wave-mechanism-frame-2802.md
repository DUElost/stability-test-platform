# 开关机链 init 失败波：机制判别与修法方向框定（#2802）

Status: proposed
Class: bug-fix

## Decision

**结论一：观测口径先要校正——「T+5~10min 一发区」是观察脚本的桶标签 off-by-one，真实窗口是 T+0~3min。**

- `plan_run.started_at = 15:20:50.659`（r441），173 条 init FAILED 全部落在 `15:20:53 ~ 15:23:00`（T+0.04~2.2min）；
- 原脚本 `/tmp/verify_2802_window.py` 用 `width_bucket(分钟, 0, 40, 8)`，**bucket=1 的真实区间是 [0,5)min，但打印成 `~5-10min`**；实测 r441：`bucket=1 count=173`、其余桶为 0 ⇒ 显示为「5-10min 一发区」；
- 原单据此推出的因果叙述（「第一批发设备做 powercycle 切换、第二批设备整批撞进风暴」）**失去前提**：全部 535 台在 T+0 同时开始 init，首批 `powercycle_setup` 也在 T+3s 才开始，不存在「第二批后置撞入」。

**结论二：失败是 host 慢性（跨 4 窗稳定），不是窗口随机、不是设备随机、也不是全 fleet 共享资源饱和。**

- 40 台 host 中 6 台零失败，且**四窗全零**（`.58` 12 台、`.60` 19 台、`.61` 20 台、`.88` 16 台）；脏 host 四窗都脏（`.67`: 17/10/11/9；`.64`: 8/10/10/11；`.71`: 13/9/12/10，分母 15~23）；
- 若为控制面/全 fleet 风暴，失败应均匀分布而非「6 台 0%、其余 50-75%」；若为 device 慢性，则慢性设备在干净 host 上不应通过——实测 182 台跨窗慢性设备里 r441 只失败 87 台，分布跨 32 host ⇒ 决定性变量在 **host 侧**。

**结论三：排除「自伤循环」——不是上一窗 PowerCycle 循环残留在重启中。**

- 98 台 `check_device` 失败的设备，其本人最近一次 `powercycle_setup` COMPLETED 均在 15min 以前（0 台 <15min；对照通过组同口径）；
- 失败步骤本身是**第一条 adb 命令**（`adb shell "echo test"`，10s 超时），失败时间落在 run 开始后 1~10s 内。

**结论四（诊断盲区）：`check_device` 失败报文丢掉了唯一可用证据。** `backend/agent/scripts/check_device/v1.0.0` 只写 `unexpected output`，stdout/stderr/exit code 全不入库，导致「adb 报 device offline / unauthorized」与「shell 返回乱码」两类成因在库内不可区分。

**修法方向（按「先补证据、再定修法」排序）：**

1. **D0 诊断版本（`check_device` v1.0.1，已合入 main）**：失败时在 `error_message` 里保留有界长度（单字段 ≤200 字符）的 stdout/stderr/exit code 与 `adb get-state` 摘要，字段顺序固定 `rc/stdout/stderr/adb_state` 便于 grep 聚合；判定语义不变（先例：`powercycle_setup` v1.2.0 为吸收 install 风暴保留了 push/pm 输出）。上线后每个窗自动产出可归类证据，替代人工窗内抓取。

   **D0 扩展（本 PR）：`ensure_root` v1.0.1** —— 同款证据字段（`adb_root rc=/stdout=/stderr=/exc=` + `id_u=` 实测读数 + `adb_state=`），判定语义不变。动因：2026-09-20 五窗复盘发现 `ensure_root` 也是失败大户（r453 25 台），而 v1.0.0 的报文只有 `Root access not granted after N attempts`，无法区分两类成因——实测 147 台失败设备中 **146 台是瞬时扰动**（`ro.debuggable=1` 可 root；失败全在 T+0~3min 波内，且同 job 的 `check_device` 均已通过 ⇒ 与本单同源），唯一确定性台是 `ro.debuggable=0` 的坏固件批次（#2753，`AYCGNX0000000001` @ `.87`，5/5 窗全败）。该版本把这两类在库内一次分开。
2. **D1 吸收（若证据属瞬时类）**：init 步骤（`check_device`/`ensure_root`）对 adb 瞬时失败做 wait-for-device + 有界重试/退避（先例同上），并把重试次数写进 metrics——**必须有界**，否则把真设备故障掩盖成恢复。
3. **D2 host 侧治理（若证据指向 host-local USB/adb）**：对脏 host 做定向排查（USB 控制器/集线器、内核日志、adb server 版本与并发），必要时下调该 host 的并发操作上限；不做全 fleet 全局并发闸。
4. **D3 计划侧（暂不采用）**：原单建议的「powercycle_setup 全局并发闸 / 开关机链分波派发」**不予采纳**——前提（T+5~10min 风暴）已被推翻，且 host 慢性特征与「全 fleet 无节流并发」不符；仅当 D0 证据重新显示 install 风暴耦合时才回到此选项。

**今晚 21:22 窗的正确抓取窗口是 T+0~3min（约 21:22:00–21:25:00），不是原评论写的 21:27–21:32。** 抓取点应取 1 台脏 host（`.67`/`.71`/`.72`/`.64`）与 1 台干净 host（`.60`/`.61`）做对照。

## Alternatives

- **按原假说直接上「powercycle_setup 全局并发闸」**：否决。该修法的触发条件（T+5~10min 与切换时段重合）经复核不存在；对 host 慢性型失败也无效（干净 host 同样跑满 powercycle 却零失败）。
- **直接给 init 步骤加重试、不先补证据**：可行但会掩盖真故障（例如 #2753 的非调试固件、USB 死态设备），且无法区分「该重试」与「该修设备」；先上 D0 的成本更低。
- **只做 host 定向排查、不改脚本**：无法闭环——host 侧证据（USB/内核日志）在窗内不易留存，且需要与设备侧原始输出互证。
- **等 r442 数据出来再动作**：不必——D0 是纯增量诊断，不改变任何判定语义，先上线可以让今晚的窗口直接产出可归类的证据。

## Verification

只读生产库（psycopg，`application_name=analyze-2802`；未打印 DSN），口径与结果：

- **时间线**：`step_trace`（r441，`stage='init'`）——`check_device` STARTED 535 台（15:20:51 起）、FAILED 98 条（15:20:52~15:22:29）；`ensure_root` FAILED 40；`powercycle_setup` FAILED 35；失败总数 173，与单内数字一致；
- **桶值复算**：`width_bucket(extract(epoch FROM (created_at - run.started_at))/60, 0, 40, 8)` ⇒ 仅 `bucket=1` 有 173 条（真实 [0,5)min），脚本标签为 `~5-10min`；
- **host 对照**：`r431/r433/r437/r441` 四窗按 host 聚合 init 失败台数（表见 Decision 结论二）；
- **自伤检查**：98 台失败设备与 437 台通过设备分别左连「最近一次 powercycle COMPLETED（不含本窗）」，失败组 0 台 <15min；
- **D0 扩展验证（ensure_root v1.0.1）**：`backend/agent/tests/test_ensure_root_scripts.py` 7 例全绿（已 root→skip 语义不变 / adb root 成功路径 / 失败带 rc+stdout+stderr+id_u+state / 异常路径带 exc / 截断有界 / `max_attempts` 生效 / `get-state` 异常不破坏判定）；**变异检查**：移除报文 `id_u=` 字段 → 诊断用例立刻红（1 failed, 6 passed），恢复后全绿；`tools/dev/check-script-version-immutability.py --base origin/main` → OK。
- **D0 落地验证**：`backend/agent/tests/test_check_device_scripts.py` 8 例全绿（成功路径语义不变 / unexpected output 带 rc+stdout+stderr+adb_state / 乱码可读 / 超时带部分输出 / 长输出有界且含省略号 / `get-state` 自身异常不破坏判定 / expect_root 两分支不变）；**变异检查**：移除报文中 `rc=` 字段 → 诊断用例立刻红（1 failed, 7 passed），恢复后全绿；`tools/dev/check-script-version-immutability.py --base origin/main` → OK（v1.0.0 未被原地改动）；
- **未做**：窗内原始字节抓取（需真机窗，今晚 21:22）；host 侧 USB/内核取证（需登录 host，未授权范围）；v1.0.1 上机分发与 plan_step 重指（按脚本版本上线五步另起单执行）。

## Revisit

- 今晚 21:22 窗后：按 D0 证据把失败归类为「adb 传输类（offline/unauthorized/closed）」「设备状态类（乱码/banner）」「host 资源类（超时）」，据此在 D1/D2 间定修法；
- D0 上线前若窗口先到，人工抓取按上文校正窗口（T+0~3min）执行，脏/净 host 各一台；
- r427（唯一低失败的开关机窗）异常：先按「host 慢性 + 该窗脏 host 参与度」重算，若仍不能解释再单独立项。
