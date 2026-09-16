# 时区三面同源：声明 / 控制面 / Agent（#2265）

Status: implemented
Class: bug-fix

## Decision

把「声明 = 控制面 = Agent」的时区一致性做成**可判定**的两件事，并去掉 Agent 侧的硬编码：

- **S1 新增一致性检查**（`install.s1.timezone`）：`ops.timezone()` 读出控制面主机 IANA 时区，
  与 `site.timezone` 比较——
  - 一致 → PASS `timezone_aligned`；
  - 不一致 → **FAIL `install_timezone`**，message 同时给出实际值与声明值，remediation 给
    `timedatectl set-timezone <site.timezone>`（fail-closed：后续阶段不再执行）；
  - 读不到（`/etc/timezone` 与 `timedatectl` 都没有）→ **BLOCKED `timezone_unknown`**，不猜也不假装一致。
- **`Ops.timezone()`**：`/etc/timezone`（Debian/Ubuntu 规范位）→ `timedatectl show -p Timezone --value`
  → 空串。与既有 `os_release()` / `machine()` 同一「注入式主机事实」模式，测试可替身。
- **Agent 侧去硬编码**：`set_timezone.yml` 的 `tz_target` 改为
  `agent_timezone（站点声明，经 install_options 下发）→ 控制面本机时区 → 历史默认` 的派生链；
  `tz_expected_offset` 由目标时区现算（不再写死 `+0800`）。
- **选项通道**：`agent_timezone` 进 install_options（工具侧 `_install_options` 带上
  `ctx.config.site.timezone`；后端 `normalize_install_options` 新增**非路径类白名单**
  `_INSTALL_OPTION_PATTERNS`，按 IANA 名的正则校验——既有的键仍然只接受绝对路径）。
- **preflight 尽早暴露**：NTP 检查的消息里带上主机时区（NTP 同步只说明「时钟准」，不说明「时区对」）。

## Alternatives

- **由安装器直接 `timedatectl set-timezone`**：主机时区是运维属性，安装器擅自改会掩盖「这台机器
  本来在别的时区」的事实（而且改完还得重启后端才生效）。选择 fail-closed + 给修复命令。
- **只改 Agent 侧不查控制面**：那正是本次现场的样子——Agent 被设成 Asia/Shanghai，而控制面在 PDT、
  声明写 UTC，三者谁也不知道谁错。必须有一处把「声明 vs 控制面」判死。
- **`agent_timezone` 也走绝对路径校验**：时区名不是路径，硬塞进路径校验要么放行任意字符串
  （`../etc` 也能过），要么永远拒绝。改为独立白名单正则（`UTC` 与多段名都覆盖）。
- **把期望偏移也做成配置项**：偏移是时区的函数，配置它只会多一处漂移（换时区忘改偏移）。
  由 `TZ=<target> date +%z` 现算。

## Verification

- 工具侧 `tests/` **1096 passed**，新增/加固 4 条：
  - `test_timezone_mismatch_fails_closed_before_writes`：不一致 → FAIL `install_timezone`、
    message 同时含实际值与声明值、且**部署根未落地**（后续阶段没跑）；
  - `test_timezone_unknown_is_blocked_not_passed`：读不到 → BLOCKED `timezone_unknown`；
  - `test_timezone_aligned_passes`：一致 → PASS `timezone_aligned`；
  - 既有 happy-path 用例的 install_options 断言补 `agent_timezone`（与声明同值）。
- 后端 `backend/tests/services/test_agent_installer.py` 6 passed：`Asia/Shanghai` / `UTC` /
  多段名放行；`../etc`、含 `; rm -rf /`、含空格、`$TZ` 全部拒绝；空值按既有语义跳过。
- 新增 `tests/test_ansible_timezone_2265.py` 3 条守卫：`tz_target` 必须引用 `agent_timezone`
  且是带缺省的派生表达式；期望偏移必须由 `tz_target` 现算；控制面时区只作为 fallback 来源。
- **现场（238，同日）**：控制面 `America/Los_Angeles` → `Asia/Shanghai`（`timedatectl` +
  重启站点后端），`site.yaml` 声明同步为 `Asia/Shanghai`（`validate` 通过）；控制面与两台 Agent
  现同秒同区（`11:55:06 CST` / `11:55:07 CST`）。

## Revisit

- **迁移期**：本次修复让「声明 vs 控制面」fail-closed。存量站点（如 238）在此前建站的声明可能是
  `init` 当天从主机读到的任意值（238 是 `UTC`）——重跑安装前要先把声明与主机对齐（现场已做）。
  若要更省事，可让 `init` 在生成时就以主机实际时区为准并在报告里回显（#2265 里列为可选项，未做）。
- **多时区站点**：当前模型假设「站点一个时区」（`site.timezone` 单值，Agent 全部跟随）。若将来
  出现跨时区机队，需要按 host 声明时区（模型与下传通道都要扩）。
- **Agent 长驻进程**：`set_timezone.yml` 改时区后需要重启 Agent 服务才生效，而重启默认关闭
  （`tz_restart_agent`，避免打断活跃作业）。安装链里 Agent 尚未启动，因此安装路径不需要它；
  手工对存量主机改时区时仍需显式开启。
