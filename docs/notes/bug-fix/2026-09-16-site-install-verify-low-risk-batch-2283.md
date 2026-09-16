# 站点安装/验收低危批：判据不成立却报成功 + 输入被静默丢弃 6 处（#2283）

Status: implemented
Class: bug-fix

## Decision

六处，全部落在「**判据不成立却报成功**」或「**输入被静默丢弃**」两类：

**1. S1 export 准备的 `chown`/`chmod` rc 被丢弃.** 两条命令的返回值此前不看，只读文件系
统 / root-squash 介质上失败时 S1 仍 PASS（检查文本还断言「export 根对约定身份可写」），
直到 Agent 侧写入才以 `storage_unwritable` / Errno-13 暴露。改为逐条判 rc，失败即
`_safe(... check_id="install.s1.export")`——与同函数上方 `exportfs` 的处理口径一致。

**2. `verify` 的存储探针与安装器挂载判据同源.** `storage_probe` 用 `os.path.ismount`，
而安装器（`LocalOps.is_mount`）读 `/proc/self/mountinfo` 并**明确**在 docstring 里说明
「`os.path.ismount` 认不出同文件系统的 bind 挂载」。于是 `local_mount` + 同设备 bind
的站点：S1 接受、唯一存储验收判据报 `shared_storage_not_mounted`，文档还把操作员指向
一个**已经挂着**的共享。改为复用 `LocalOps().is_mount`，并留可注入形参供单测。

**3. `handover` 把 BLOCKED 判成 FAIL（且因此不写任何文件）.** `verify` 只在 FAIL 时失败，
BLOCKED（如无 ONLINE 设备）是**正常**结果；但 `_resolve` 凡非 PASS 即进 `failed` →
条目 FAIL → `if not any(FAIL)` 不成立 → **不写 `handover.json`**，而 `/site/` 导航页
有指向该文件的固定链接（404）。改为三态：BLOCKED 证据 → BLOCKED 条目（`evidence_blocked`），
**不阻断写文件**（它是正常验收结果）；只有 FAIL 阻断，且在报告 summary 里显式写
「No handover file was written because at least one item FAILED.」。

同单另两处**空集 PASS** 一并收口：`agents.py` 零 Agent 时 `install.s5` 由 PASS 改
**BLOCKED**（`agents_pending`；install 的状态聚合只看 FAIL，故不破坏「先装控制面、
Agent 随后接入」这条合法流程）；`verify.check_hosts` 零 Agent 时不再产出
「All 0 declared Agents are ONLINE」的 PASS，改 BLOCKED（`no_agents_declared`）。

**4. inventory 的 `ansible_port` 解析后被丢弃.** `_port` 被 pop 掉、`_create_payload`
硬编码 `ssh_port: 22`——sshd 不在 22 的主机会连错服务，而文档一直把该键列为受支持的
逐主机覆盖。改为：`_parse_port`（解析期 fail-closed，越界/非数字即
`InventoryError`）→ `Agent.ssh_port`（模型新增，1..65535，默认 22）→ `_create_payload`
/ `reconcile_host` 带端口。

**5. `init --json` 的 stdout 被交互提示污染.** `input()` 的提示写 stdout，而报告也写
stdout：`init --json > site.json` 会产出以提示开头的非法 JSON。提示改走 **stderr**
（stdin 语义不变），不改「`--json` 隐含非交互」以免静默吞掉待答问题。

**6. 接管既有 Prometheus 的规则去向.** 本站渲染的 `prometheus.yml` **不含 `rule_files`**，
而接管后 `--config.file` 指向它——主机原有的抓取作业与告警规则**静默失效**，安装仍报
`monitoring_ready`。新增 `_prometheus_running_config`（读 `systemctl show -p ExecStart
prometheus`，取实际生效的 `--config.file`）：**非本站形态**时记一条 BLOCKED
（`takeover_needs_rule_migration`）说明「原有规则需人工迁移或另置主机」。
形态一致时不报——所以重跑（本站配置已生效）自动清掉该项，不产生常驻 BLOCKED。

## Alternatives

- **1-a 改成「以映射身份实际试写」的探针**：更好但更重（要在 S1 就引入写动作与清理）；
  本次取 rc 判据（最小面），试写探针记入 Revisit。
- **3-a BLOCKED 也阻断写文件、只在输出里说明「未写文件」**：否决。`handover.json` 是
  站点的交接记录，BLOCKED 是合法状态；阻断会让 `/site/` 链接长期 404（正是本单症状）。
  **3-b 给零 Agent 的两处保留 PASS、只在文案里注明**：否决——「零元素」证明不了任何
  事，PASS 会被下游当成证据。
- **4-a 不支持 `ansible_port`，改成显式拒绝该键**：否决（issue 允许二选一）。文档与
  注释一直宣称支持，实现支持它比删功能更符合预期；非法值在解析期拒绝已足够 fail-closed。
- **5-a `--json` 隐含非交互**：否决。会把「有待答问题」变成静默取默认值，操作员拿不到
  提示；提示走 stderr 后 stdout 已经纯净。
- **6-a 静态文案写进 monitoring PASS 的 message**：否决（常驻噪声、且无从知道是否真发生
  接管）。**6-b 自动迁移规则**：否决——搬什么规则是运维判断，本单只做「显式记录」。

## Verification

- 六处各自**红绿双向**：把 `tools/site_config/*.py` 还原到 `HEAD` 后，本单新增的 9 条
  用例**全部红**（其中 1 条为「本站形态不误报」的守卫，旧实现下平凡通过）；恢复后
  `tests/ -k "site or inventory"` **483 passed**。
- 新增用例清单：export rc → FAIL；监控接管 BLOCKED / 本站形态不误报；S5 零 Agent →
  BLOCKED；`check_hosts` 零 Agent → BLOCKED 且不调 API；`_create_payload` 端口透传；
  inventory 端口保留 + 非法端口解析期拒绝；handover BLOCKED 证据 → 条目 BLOCKED 且
  仍写文件；`_ask` 提示只进 stderr。
- `ruff check backend/ tools/ scripts/` 全绿；`check:quick` → **OK (10 gates)**。
- **未跑**：真机站点安装/验收（需要站点主机与设备）；本单是判据与管道改动，CI
  required checks 复核。
- **未做**：`verify` 的 `check_hosts` / `stage_s5_agents` 的 BLOCKED 在 `handover`
  ACCEPTANCE_ITEMS 里的映射面未逐一复核（monitoring 不在映射内）；如需把「零 Agent」
  反映到 MS 条目，另议。

## Revisit

- **1 的更强形态**：S1 目前只判 rc；真正的判据是「以映射身份写一次」——若现场再出现
  「rc=0 但仍写不进」（如 NFS 服务端自身的导出表未生效），应升级为写读探针。
- **6 的探测面**：`systemctl show` 解析的是 systemd 展开后的 argv；若发行版换成别的
  启动方式（非 EnvironmentFile），该函数返回空 → 静默不报（fail-open 方向）。可考虑
  在「prometheus 单元存在但解析不出 --config.file」时也记一条提示。
- **3 的三态收敛**：`handover` 的 BLOCKED 是否应影响**站点验收结论**（而非仅条目标注）
  仍未定义；当前口径是「BLOCKED 不影响写文件、不影响 status=PASS」。
- **4 的配置面**：`ssh_port` 现随 Host 行建立；改端口只影响**新建**行，已存在的 Host
  不会被更新（reconcile 只匹配 IP）——需要改端口时应走 Host 编辑接口，属既有语义。
