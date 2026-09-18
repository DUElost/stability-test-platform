# #2638 upgrade-gate 的 `except` 元组漏了 `HostRetiredError`：写好的 409 分支不可达

Status: implemented
Class: bug-fix

日期：2026-09-18 ｜ Harness：codex ｜ 分支：`fix/2638-upgrade-gate-retired-409`

## Decision

`backend/services/host_upgrade_gate.py:259` 在**取维护窗口之前**对退役主机抛 `HostRetiredError`
（ADR-0038 D5：拒绝路径不得占用窗口），`backend/services/agent_upgrade_gate.py:80` 也为它写好了
`409 {"code": "HOST_RETIRED"}` 分支——但唯一入参路径 `acquire_agent_upgrade_gate` 的 `except` 元组没有它，
于是异常原样上抛（同函数尾部 `raise exc` 兜底）⇒ 框架 **500**，409 分支永不执行。
外部入口（Ansible 升级脚本 / 批量热更新）对退役主机拿到 500 + 通用错误体，按 500 处理会重试或误判。

修法一行：把 `HostRetiredError` 加进元组（`agent_upgrade_gate.py:140`）。**不改映射、不改语义**——
映射本来就在，只是没人接。

真正要留的是**为什么再加一条契约用例**：这类缺陷的形状是「映射与抛出点分处两个函数，中间靠一份手工维护的元组连接」，
它与 #2059（准入泵同形）是同一族。所以补的是「**每条领域异常都必须变成带 code 的 HTTP 错误**」的
参数化契约，而不是只给 `HOST_RETIRED` 打一个补丁式断言。契约的覆盖面取**运行时子类集合**
（`HostUpgradeGateError.__subclasses__()`）而不是源码字面量——#2639 那一族「锚点漂移 ⇒ 否定断言恒真」
的失败形态正是源扫描型守卫造出来的。

## Alternatives

- **只加一行、不补用例**：关掉本单，但下一个新增的 `HostUpgradeGateError` 子类会以同样的方式再漏一次。
- **给 `HostUpgradeGateError` 注册应用级 `exception_handler`**（异常 → HTTP 一处收口）：方向上更干净
  （元组这个「手工连接件」根本可以消失），但它改变的是**全仓异常映射形态**，且 `raise_upgrade_gate_http`
  里每条分支的 message 都带 `host_id` 与运维指引（退役提示「unretire it before upgrade」），
  搬到 handler 会丢这些上下文。属另立单的重构，不塞进一个 P3 修复。
- **用源码文本断言元组与 isinstance 分支一致**：能做到「零漏项」，但正是 #2639 描述的那类脆弱守卫
  （函数搬家即恒真）。运行时子类集合没有这个问题。
- **`except Exception` + 映射函数内判定**：会把非领域异常（DB 故障、编程错误）一起咽成 4xx，
  比原缺陷更糟。不做。

## Verification

- 实跑（本机、隔离库，`env -u DATABASE_URL` 走 testcontainers）：
  `backend/tests/api/test_upgrade_gate_api.py` + `backend/tests/services/test_agent_upgrade_gate.py`
  → **22 passed**；再加上 `backend/tests/services/test_host_upgrade_gate.py` 三文件合跑 → **37 passed**。
- 用例增量：服务层 4 → 12（+6 参数化契约 +1 族覆盖双向断言 +1 退役专项）、API 层 +1
  （`backend/tests/api/test_upgrade_gate_api.py:77`，真实端点、不 mock 映射函数）。
- 变异自证 **6 条全部 on-target**（每次只改一处、跑完即还原）：
  `R0` 真实端点退回原 bug（元组拿掉 `HostRetiredError`）→ 端点用例红（500≠409）；
  `R1` 同处在服务层 → 2 条红；`R2` 元组拿掉 `HostMaintenanceConflict` → 该参数化项红；
  `R3` 映射 code 改名 → 2 红；`R4` 映射状态码 409→400 → 2 红；
  `R5` **新增**一个 `HostUpgradeGateError` 子类但不进契约表 → **恰好 1 红**，
  报的是「族与契约表漂移（用例已过期）」而不是行为回归——两类失败可区分，这正是 #2639 建议 1 要的判据。
- 过程坑（记下来因为它会让变异结论失真）：第一轮变异里 `409→400` 是**等长改动**、还原时 `shutil.move`
  带回了旧 mtime ⇒ `.pyc` 被判有效 ⇒ 「基线」跑到过期字节码上，假红 2 条。
  复跑统一加 `PYTHONDONTWRITEBYTECODE=1` 并在还原后 `os.utime` 刷新，基线才回到 12 passed。
- 门禁：`python scripts/run_gates.py check:quick` 与 `check:pr` 在**已提交的树**上跑（本轮不把文件写进运行中的门禁）。

## Revisit

- `raise_upgrade_gate_http` 的 6 条分支、`acquire_agent_upgrade_gate` 的元组、UI 热更新入口
  （`backend/api/routes/hosts.py` 的 hot-update）是三处并行维护同一族映射的地方。若再来第三例同形缺陷，
  终态应是把「领域异常 → HTTP」收进一个 exception handler（见 Alternatives 第 2 条），
  元组与分支表合成一份；本单不预判其裁决。
- 契约表里 `HostMaintenanceConflict` 不继承 `HostUpgradeGateError`（来自 `host_maintenance`，且没有 `.code`），
  所以双向断言对它做了豁免。若将来把它并入基类，豁免要去掉——留着会让「元组少一项」在它的分支上失去信号。
