# 同一台设备被两台 host 抢：成因要看得见，夹具要能造出多 host（#2569 + #2570）

Status: implemented
Class: bug-fix

## Decision

两单是**同一件事的两半**，一起修才有意义：

- **#2569（产品侧）**：一台设备的归属被另一台 host 改写时，日志里只有两种情况看得见
  ——恰好压着 ACTIVE 租约（`device_host_reassignment_blocked`，会阻断）与派发时
  `device_host_drift` 整批 fatal（后果）。中间那段「无租约窗口里的改绑」**只有占位
  serial** 才留日志（#1356 的处置），非占位值跨 host 漂移彻底静默。生产代价记在
  `backend/core/device_serial.py`：**2026-09-11 run 360/362，1 台设备阻断 368 台压测**。
- **#2570（dev 侧）**：假 Agent 夹具的 serial 写死 `DEVFIX###`，不带 host 维度。
  `device.serial` 全局唯一 + 心跳按 serial 认设备 ⇒ 两台夹具**必然**互相抢行：
  第二台 `claim` 恒 `[]`，被抢的那台每拍打一条 blocked。多 host 形状（host 级 abort
  扇出 #1880/#2050、跨 host 双驱动 #799、`device_host_drift` 整批阻断）在 dev 里
  不可复现，#2402 的验收目标在这一维仍不成立。

没有 #2570，#2569 的日志只能等真机偶发；没有 #2569，夹具修好后仍然查不到争用成因——
本轮的 dev 实测就是先撞坑 2、再发现坑 1（`#2569` 正文里那 16 次采样翻 4 次，
正是 `DEVFIX002/003` 在两台夹具间来回改绑）。

改法：

1. **产品侧只加日志、不改语义**：`heartbeat.py` 的改绑分支拆成两条事件名——
   占位值继续 `placeholder_serial_host_drift`（#1356），非占位值新增
   `device_host_reassigned serial= device= <from>-><to>`。两条路径都**不阻断**：
   归属仍按最新心跳，阻断只由租约决定。事件名不合并，因为**运维动作不同**
   （占位值→刷机/换线；非占位值→查为什么两台 agent 在抢同一台真机）。
2. **夹具侧把 host 维度做成缺省行为**：`default_serial_prefix(host_ip)` 取 IP 尾段
   （`192.0.2.12` → `DEVFIX012`），serial 变成 `<前缀>-<序号>`；`--serial-prefix` /
   env `STP_DEV_SERIAL_PREFIX` 只作为**显式覆盖口**。做成缺省而不是新旋钮的理由：
   缺省撞名的坑会先烧掉测试者一小时，而且**极易被误判成产品缺陷**。
3. **前缀取 IP 尾段**是因为 dev 栈里主机身份就是按 IP 认的（`host_id: "0"`
   自动注册哨兵）。已知边界写在 docstring 与本文 Revisit：跨 `/24` 同尾段的两台
   假 host 仍会同前缀——那种拓扑用显式覆盖口，退化方向是旧行为（互相抢行），不是崩。

## Alternatives

- **非占位改绑只打 `logger.info`**：放弃——这条链的代价是整批派发 fatal，
  且诉求原话是「查不到成因」；info 在现场等于没有（`LOG_LEVEL` 生产是 WARNING 以上时
  整条链就看不见）。
- **加 metric counter 代替日志**：放弃——「漂了几次」今天确实没有读数，但**哪台设备、
  从哪到哪**只有日志能带；先加一只没消费者的 gauge 正是 #2287 刚删过的形状。
  真要做观测面应该是「按设备聚合的漂移次数」，见 Revisit。
- **把阻断条件从「有 ACTIVE 租约」扩到「最近 N 秒被别的 host 报过」**：放弃——那是
  归属语义变更（谁该拥有这台设备），本单只让成因可见。
- **夹具做 `serve --multi-host N` 一条命令起 N 台**（issue 建议 4）：不做——正文自己
  就说了别当门槛；`build_heartbeat_payload()` 已是纯函数，前缀带上 host 之后两条
  命令即可（已写进 docstring 用法与 `local-development.md`）。把进程编排塞进夹具
  还会撞上红线 1（不得引入执行面）。
- **前缀取自 `--host-id` 而非 `--host-ip`**：放弃——`--host-id` 在哨兵路径上是自由值，
  IP 才是 dev 栈里真正决定「哪台主机行」的键。

## Verification

- `backend/tests/api/test_heartbeat.py` 新增 3 条（该文件 28 → **31 passed**）：
  ① 非占位 serial 跨 host 改绑必留**一条** `device_host_reassigned`，带 from/to，
  **同一台 host 重复心跳不得刷**（否则每条日志变成每拍噪声），且归属确实按最新心跳
  改（钉住「只加日志不改语义」）；② 占位值仍走自己的事件名——顺带说明：
  **#1356 那条路径此前没有任何测试钉子**，本单补上；③ 有 ACTIVE 租约时先被 block，
  不得再落一条「改绑成功」。
- `tests/test_dev_fake_agent.py` 新增 4 个函数 = **8 项**（该文件 27 → **35 passed**，
  其中 1 条是 5 参的边界表）：两台 host 的 serial
  集合不相交（#2570 判据 3，不连服务）；同 host 前缀**不随 seq 漂移**；前缀仍一眼是
  夹具（`DEVFIX` 基准）；`serial_prefix` 显式覆盖；`default_serial_prefix` 五个边界；
  以及 **CLI→payload 的接线**——本文件参数默认值走 `SUPPRESS` + `_global_defaults()`
  （docstring 记着丢参数的旧坑），少传一处 `serial_prefix=` 不会让任何纯函数用例变红，
  所以单独钉一条含 env 的通路。
- 变异自证 5 条全部 on-target：`M1` 删掉非占位日志分支→改绑用例红；
  `M2` 把日志条件从「归属真的变了」改成无条件→同 host 那半红（每拍噪声）；
  `M3` 两个事件名互换→改绑用例红；`M4` 前缀不带 host 维度（回到 `DEVFIX###`）→
  不相交用例红；`M5` 忽略显式 `serial_prefix`→覆盖用例红。
- 文档：`tools/dev/fake_agent.py` 的坑清单 4 → 5 条并补多 host 用法示例；
  `docs/development/local-development.md` 的「两个已知差异」→「三个」+ 两条命令的
  多 host 形状（host 级 abort 扇出 / 跨 host 双驱动 / drift 整批阻断从此可造）。
- 全量 `backend/tests/api/` + `tests/test_dev_fake_agent.py` → 见本 PR 的验证评论
  （`test_artifact_download.py` 的 collection error 是 main 既有缺陷，另单跟踪：
  已在 #2568 补证据，不属本单范围）。
- **未完成（pending）**：dev 隔离栈里真起两台夹具做端到端多 host 回归（本轮只到
  「形状可造」这一层）；`check:quick` 结果见 PR 评论。

## Revisit

- **#2569 只回答「看得见」，没回答「该信谁」**。今天的答案仍是「信最新心跳 + 派发侧
  整批 fatal」。若现场 `device_host_reassigned` 高频出现，需要的是**仲裁策略**
  （粘住首次上报的 host？按租约/最近活跃窗口？），那是行为变更，须单独评审——
  口径参照 #1356（当年也选择了「不阻断，交给派发侧」）。
- **日志没有去重/限频，且是故意的**：一台被抢的设备每拍一条（夹具 5s 心跳 ⇒
  ≈12 条/分钟/设备），因为诉求就是「漂了几次」——数条数即答案。真成噪声时应加的是
  限频或「上次漂移至今累计 N 次」的聚合行，**不是**删这条日志。
- **UI 面仍盲**：`DeviceOut.serial_suspect` 只标占位值，非占位的跨 host 争用在设备
  卡片上看起来是干净的单归属。要不要把「最近被改绑过」做成可见状态，属观测面设计。
- **#2570 判据 4（`--multi-host`）未做**；多 host 的端到端回归也还没固化成脚本，
  本轮只把「做不出来」变成「两条命令能造出来」。#2402 的验收在这一维要从
  「不可测」改判为「可测但尚无固定脚本」。
