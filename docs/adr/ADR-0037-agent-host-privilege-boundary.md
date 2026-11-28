# ADR-0037：Agent 主机提权边界（Privilege Boundary Wrapper）

- 状态：**Accepted（v0.9）**
- 版本记录：v0.9（2026-11-28 裁决采纳，Owner 依据 PR #4213 四轮独立复核通过（`fd3b30640`）裁定接受：§2 新增 D10 USB 反向供网受限通道——六子命令白名单 `usbnet-{ensure-bridge,attach,detach,revoke,teardown,status}`、禁用全拒与 `usbnet-status` 暴露撤销屏障、仅按 `device_serial` 索引单调水位与按出口 `min_allowed_iat_by_egress` 屏障、可执行 sysfs 身份校验、隔离前置两阶段提交、`usbnet-detach` 完备三分支、`usbnet-teardown` 零残留关闭与三层隔离，#4212 / [ADR-0066](./ADR-0066-unified-network-supply-and-usb-net.md) D6；§4 不变量第 2、3 条按 D10.7 随 U3 实现 PR 合入同批转正，§5 Revisit #7 登记试点灰度前置）；v0.8（2026-11-23 生效，Owner 裁定采纳、PR #4114 独立复核通过、PR #4120 落地实施与强控制测试：§2 新增 D9——xHCI 主控自动恢复窄子命令 `xhci-rebind`、sysfs 写面语义、退出码、`boot_id` 持久化熔断、`claim_loop` 互斥与 L3 `adb offline` 同构边界，#2972；§4 不变量第 2、3 条同批转正强控制与测试清单，§5 Revisit 登记生产灰度前置）；v0.7（2026-11-10：§2 D8 USB 切电子命令 `usb-power` 随 [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md) 转 Accepted 一并生效（#3892；阶段 A、B 真机验收通过），§4 不变量第 2、3 条同批补上该子命令的强控制与测试文件）；v0.6（2026-09-26：§2 新增 D7——内核日志只读子命令 `read-kernel-log`（#2957：Agent 侧内核 USB 判据的提权读面，两个整数参数 + 固定 argv + 输出上限/超时/截断非零退出），§4 强控制与测试清单同步）；v0.5（2026-09-16：§2 D2 事实勘误——`selftest` 只证 wrapper **自洽**，不证它具备热更新脚本将要调用的子命令；脚本头改为「selftest + 能力集合比对」双前置判据，wrapper 新增 `capabilities` 子命令，#2319）；v0.1（2026-09-11 初版，R14-F04 #1250 触发）；v0.2（2026-09-15：§1.2 事实勘误、§2 新增 D5、§4 偏差记录、§5 退役前置修订，#2133）；v0.3（2026-09-15：§5 Revisit #1 执行完毕——legacy 分支与哨兵删除、失败模式改 fail-closed、§4 回滚路径更新，#2180）；v0.4（2026-09-15：**转 Accepted**——R02 安全联审一稿已交付（#2206 / PR #2209，结论「建议接受、无阻断项」）；采纳 S1/O1/O2：§2 新增 D6 边界承担声明、D3 补信任边界措辞、§4 不变量补既有强控制与测试清单、§5 ③ 记评审交付与 S2/S3/O4 处置）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-11（v0.2 / v0.3 修订 2026-09-15；v0.4 修订 2026-09-15；v0.5 勘误 2026-09-16；v0.6 增补 2026-09-26；v0.7 增补 2026-11-10；v0.8 增补并生效 2026-11-23；v0.9 增补并裁决采纳 2026-11-28）
- 决策者：平台研发组（R02 安全联审；2026-09-15 依据评审一稿裁决转 Accepted；v0.6 的 D7 由 owner 于 #2957 裁决，实施规格含评审材料与逐项核验；v0.7 的 D8 由 Owner 随 ADR-0063 于 #3892 裁决；v0.8 的 D9 由 Owner 于 2026-11-23 依据独立安全复核结论于 #2972（PR #4114 / #4120）裁决生效；v0.9 的 D10 由 Owner 于 2026-11-28 依据 PR #4213 四轮独立复核结论于 #4212 裁决采纳，§4 不变量随 U3 实现合入转正）
- 标签：安全, 提权, sudoers, 热更新, Agent 主机
- 归属域：semantic-ownership host-privilege-wrapper
- 关联：R14 台账 [#1266](https://github.com/DUElost/stability-test-platform/issues/1266)（R14-F04 [#1250](https://github.com/DUElost/stability-test-platform/issues/1250)）；R02 安全审查（联审项）；ADR-0035（主机身份与凭据方向，wrapper 鉴权面待其落地后重审）；#960（维护窗口）；#1247/#1248（热更新工件与主机本地资源保护）；[#2133](https://github.com/DUElost/stability-test-platform/issues/2133)（flash 链运行时提权收口，v0.2 新增）；[#2134](https://github.com/DUElost/stability-test-platform/issues/2134)（宽文件清除与 legacy 退役）；[#2180](https://github.com/DUElost/stability-test-platform/issues/2180)（legacy 分支与哨兵删除，v0.3）；[#2957](https://github.com/DUElost/stability-test-platform/issues/2957)（内核日志只读通道，v0.6 新增）；[#2972](https://github.com/DUElost/stability-test-platform/issues/2972)（xHCI 主控门控自动恢复与熔断，v0.8 新增）；[ADR-0066](./ADR-0066-unified-network-supply-and-usb-net.md) 与 [#4212](https://github.com/DUElost/stability-test-platform/issues/4212)（批次 B13 统一网络供给与 USB 反向供网，v0.9 D10 新增）

## 1. 背景

### 1.1 问题定性：不是命令名单过长，而是没有提权边界

`install_agent.sh` 为 Agent 用户（`android`）落 NOPASSWD sudoers，其中
`rsync` / `cp` / `chmod` / `chown` / `ln` 均**无参数限制**：拿到 Agent 用户
执行能力即可通过 `sudo rsync` 等对任意 root 文件提权写，非 root Agent 的
权限边界不成立（R14 二次核验属实）。

### 1.2 事实核验（2026-09-11 静态盘点 + 容器实证；2026-09-15 勘误与补验，见 v0.2）

旧规则的**真实消费方只有 UI 热更新远端脚本**（`host_updater.py`）：代码同步
（rsync）、schema 安装（mkdir/install）、VERSION 与依赖标记（tee）、`.env`
写入（root python heredoc）、属主回收（chown）、服务重启（systemctl）。盘点
结果：

- `cp` / `ln` / `stat` / `chmod` 无任何运行时消费方（仅运维文档中人工使用
  密码 sudo）；
- Ansible 安装/更新链用 `become`（inventory 密码作 sudo 密码）完成 root
  操作，**不依赖** NOPASSWD 规则；
- **〔v0.2 勘误〕** 初版盘点漏记一个存量文件：`/etc/sudoers.d/android`
  （内容 `android ALL=(ALL) NOPASSWD: ALL`），install/update 链均不清理它。
  因此 `flash_preflight`/`flash_firmware` 的 `sudo -n sh -c` 修复与 sysfs
  `authorized` 门控分支**并非不可用，而是在用**——2026-09-15 实测：36 次
  preflight 的 `usermod` 修复 rc=0（2026-09-12，刷机主机）、487 次 flash 中
  280 次门控经 sudo 回落成功（agent 以 `android` 运行、目标 sysfs 文件 644
  root:root，直写不可能）；反例实验（仅收窄 sudoers、无宽文件的 I4 容器）
  则 `sudo -n true` rc=1、preflight 直接 `success:false`。故「本次收窄不构成
  回归」在**收窄动作**上仍成立（收窄只重写
  `/etc/sudoers.d/stability-test-agent`，flash 链仍走宽文件），但宽文件是
  既定退役对象，其删除必须以 flash 链收敛为前提（§2 D5、§5）。

### 1.3 约束

免密热更新是产品既有能力（UI 按钮、批量脚本）；`android` 只应获得「升级
自身」所需的固定操作，不应获得任意 root 文件写或任意命令执行。

## 2. 决策

- **D1 单一提权入口**：新增 root:root 0755 的 wrapper
  `/usr/local/sbin/stp-agent-priv`（**不得**位于 Agent 可写的安装目录内）；
  sudoers 只授两类命令：该 wrapper 单命令 + 固定服务名的 `systemctl`
  子命令。任意 `rsync/cp/chmod/chown/ln/stat` 免密规则全部删除。
- **D2 子命令白名单 + 内部校验**：wrapper 仅接受固定子命令
  （`bootstrap/selftest/apply-code/install-schema/write-version/write-digest/
  sync-env/deps-marker/fix-ownership/restart`；`write-digest` 为 ADR-0040 D2
  落地时的同族扩展，#1907 按 §7-5 同 PR 回填；`ensure-udev-rule` /
  `usb-authorized` 为 D5（flash 链）扩展，#2133），且内部强制：
  所有目标路径固定（`$INSTALL_DIR` 及固定子路径，组件级前缀校验）、
  源文件/暂存目录必须为调用者属主、schema 必须通过 JSON 结构校验、
  版本/SHA 走字符集正则、`chown -R -h`（symlink 不 deref）、rsync 使用
  `--safe-links` 与固定 excludes（含 `resources/mtbf/` 的
  exclude+protect，#1248 语义）；`PROTECT_ONLY_PATHS`（当前
  `resources/`，ADR-0040 §4.3 P2 前置，#1950 按 §7-5 同 PR 回填）仅追加
  `--filter=protect` 不 exclude——防 `--delete` 清掉大件的同时保持分发
  照旧，P2 载荷收缩后自然停发。（v0.3 / #2180：legacy 路径的对称过滤面已随
  迁移期结束删除，filter 只此一处。）（2026-09-26 回填，ADR-0040 §7-5 / D8 R4：
  ADR-0040 P2-B（#1975）曾新增 `apply-resources` 子命令与 `write-digest --kind resources`
  （写第二身份文件 `ARTIFACT_DIGEST_RESOURCES`），**未按 §7-5 回填本白名单**；host-resources
  层退役后二者一并删除，白名单与实现重新一致。`resources/` 的 protect-only 与
  `ARTIFACT_DIGEST_RESOURCES` 的元数据保护保留——退役不做主机清理。）
- **D3 存量迁移**：install 链（`install_agent.sh` 内 bootstrap）与
  `update_agent.yml`（Ansible `become`）都部署 wrapper 并生成/重写
  conf 与 sudoers（visudo 校验后原子替换，失败即中止）。迁移期热更新检测
  wrapper：缺失则回退 legacy 旧 sudo 面并输出 `STP_PRIV_FALLBACK=legacy`
  哨兵（控制面 `priv_mode` 审计可见）；sudoers 重写完成的主机自动走
  wrapper。legacy 分支与存量宽文件在 fleet 迁移完成**且 flash 链验收通过**
  后删除/清除（判据见 §5 Revisit #1，v0.2 收严）。
  **（v0.3 / #2180：迁移期结束，已执行。）** 48/48 台 `priv_mode=wrapper`、
  宽文件 48/48 清除、flash 链在无宽文件主机验收通过后：远端脚本删除
  `USE_PRIV_WRAPPER` 回退分支与全部裸 sudo 命令面，开头以
  `sudo -n stp-agent-priv selftest` **fail-closed**——失败即带 `update_agent.yml`
  指引退出、不执行任何动作。**（v0.5 / #2319 勘误：`selftest` 的「子命令契约校验」是
  *自指*的**——它断言「parser ↔ 本机契约表」两两一致，对「本机 wrapper 是否具备脚本将要
  调用的子命令」没有判别力（缺一子命令但自洽的旧 wrapper 同样 exit 0）。脚本头因此改为
  **双前置判据**：`selftest` + `capabilities`（warpper 打印支持的子命令）与脚本的
  **期望集合**比对，缺失即 fail-closed；`host_updater._REQUIRED_PRIV_SUBCOMMANDS` 与
  脚本里的 `$PRIV` 调用由守卫测试钉住。`STP_PRIV_FALLBACK` /
  `STP_RESOURCES_PRIV_FALLBACK` 哨兵与 `resources_priv_fallback` 审计字段
  一并退役（`priv_mode` 只剩 `wrapper`/`unknown` 两态）。
  **（v0.4 / O1：信任边界是 conf 冻结，不是调用者身份校验。）** `bootstrap` 的可调用主体
  不构成信任边界——锚点漂移守卫（`conf` 一旦存在，`INSTALL_DIR`/`AGENT_USER`/`AGENT_GROUP`/
  `SERVICE_NAME` 不得再变）才是；守卫**刻意不校验 `SUDO_UID`**（Ansible `become: true`
  以 `ansible_user=android` 执行时会设置它，加了会打断正常部署）。合法迁移安装点须先由
  root 删除 `conf` 再 bootstrap。本条由实现 docstring 上升为 ADR 明文（评审 O1）。
- **D4 显式不做**：不动 Ansible 自身的密码 become 通道（与 NOPASSWD 面
  无关）；不引入 per-host 凭据（ADR-0035 实施面）；wrapper 不放进安装
  目录，也不提供任何「任意目标路径」参数。
- **D5 flash 链运行时提权收敛**（v0.2 新增，#2133）：`flash_preflight` /
  `flash_firmware` 的运行时 root 需求按「provisioning 归位 + 固定面兜底」
  收敛——apt 包集合、dialout 成员、udev 规则由 install/update 链在 root 期
  保证（运行期不装包）；wrapper 扩展两个窄子命令：`ensure-udev-rule`
  （固定路径与内容，无参数面）与 `usb-authorized`（`--port` 走字符集校验、
  `--value ∈ {0,1}`，写 sysfs `authorized`）。脚本以**新版本**落地（已发布
  版本不可原地改）。在本项验收（无宽文件主机上 flash 链通过）完成前，
  `/etc/sudoers.d/android` 不得删除。
- **D6 边界承担声明（v0.4 / 评审 S1）**：sudoers 对 wrapper 的授权行是**不带参数模式的
  单命令**（`<user> ALL=(root) NOPASSWD: /usr/local/sbin/stp-agent-priv`）。按 sudoers 语义，
  这等于**允许任意参数**——因此 D1「单一入口」**并没有在 sudoers 层约束参数空间**，
  全部 containment 由 **wrapper 进程内校验**承担。这一事实是本节其余约束的前提：
  `bootstrap` 对 Agent 用户可达（`_require_root()` 在经 sudo 调用时必然通过），其安全性
  依赖 `realpath` + `_validate_install_dir` + `_reject_anchor_drift` 三道守卫；其余子命令同理。
  因此 §4 把 **parser/边界测试**与强控制清单一起列为不变量（改动即需同步测试），
  并在 `selftest` 中做子命令契约校验。
- **D7 内核日志只读通道（v0.6 新增，#2957）**：Agent 以 `User=android` 运行，内核
  USB 判据（#2900）在机队上恒不可读（`dmesg_restrict=1`、`/dev/kmsg` EPERM、非特权
  `journalctl -k` 输出与「内核干净」同形）。在**不新增组权限、不新增 sudoers 行**的
  前提下，wrapper 扩展只读窄子命令 `read-kernel-log`：

  ```
  read-kernel-log (--boot | --since-epoch <int>) [--lines <int>]
  ```

  固定 argv = `journalctl -k --no-pager -o cat` + 窗口 + `--lines`；约束全部在
  wrapper 内校验：`--since-epoch` ∈ [0, now]、`--lines` ∈ [1, 5000]（`allow_abbrev=False`
  拒绝缩写），**不接受**任何路径/单元/匹配表达式（`--file`/`-D`/`-M`/`-u`/`--grep` 等
  在解析层就不可达）；子进程环境清空后只留 `LC_ALL=C` 与 `PATH=/usr/bin:/bin`、
  30s 超时、stdout 8 MiB 上限；**超限/超时/非零退出一律 exit 3 + `STP_READ_KERNEL_LOG_*`
  标记，不做部分投递**（#2957 实测某主机 `--boot` 373,600 行，截断后的偏小计数会
  冒充完整结果）。解析仍在 Agent 侧（`kernel_usb_faults`），wrapper 只搬运原始输出。

  Agent 侧只在 `capabilities` 含该子命令时经 `sudo -n` 调用，否则维持非特权路径
  （结果仍为 `unavailable`），两态混跑安全；wrapper 不随热更新下发（D3 同款代价），
  灰度与生效见 §5。采集侧随之把**首扫从整段 boot 改为最近 1 小时**——boot 截断问题
  从结构上消除；代价是「进程启动前 1 小时以外的死亡」不再进 L1 latch，该形态由
  结果层 `usb_tree_empty`（#2902 + #2967 账实合取）承接。

- **D8 USB 切电通道（v0.7 生效，#3892；随 ADR-0063 v1.0 裁定）**：wrapper 新增子命令
  `usb-power --serial <serial> --expect-slot <hub_location>:<port> --action {on|off|cycle} [--delay <int>]`。规格以
  [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md) D1、D2 为准：槽位只从 root 属主的
  `/etc/stp-agent/usb-power-slots.json` 按 serial 查得，wrapper 不得接受用来指定切电目标的 Hub 位置或端口参数（`--expect-slot` 只用于切电前比对，不一致即拒绝）；固定 argv 调用
  `/usr/sbin/uhubctl`，不经 shell，环境清空，60s 超时，成功时回显实际槽位。安装链不得新增或修改 sudoers 行（D6）。
  §4 不变量第 2、3 条已同批补上该子命令的强控制与测试文件（v0.7）。
- **D9 xHCI 主控自动恢复通道（v0.8 生效，#2972；Owner 2026-11-23 裁定，PR #4120 落地）**：
  1. **窄子命令接口**：wrapper 必须仅提供无目标参数子命令 `xhci-rebind`。wrapper 必须在参数解析层启用 `allow_abbrev=False`。wrapper 不得接受调用方传入的路径或 PCI 设备号参数。
  2. **sysfs 枚举与写面语义**：wrapper 必须逐级以 `O_NOFOLLOW | O_DIRECTORY` 打开固定驱动目录 `/sys/bus/pci/drivers/xhci_hcd`。wrapper 必须只在该驱动目录下枚举名称匹配 PCI BDF 正则（`^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]\Z`）的符号链接条目。wrapper 必须校验每个枚举条目经 `realpath` 解析后的目标位于 `/sys/devices/` 之下且末段文件名等于该 BDF（内核 PCI 项为符号链接，与 D5 `usb-authorized` 同形）。wrapper 必须只以 `O_WRONLY | O_NOFOLLOW` 打开该驱动目录下的普通属性文件 `unbind` 与 `bind`。wrapper 必须对每个通过校验的 BDF 按「向 `unbind` 写入该 BDF 名、等待固定间隔、再向 `bind` 写入该 BDF 名」的顺序执行。
  3. **进程隔离、超时与爆炸半径**：wrapper 不得经 shell 执行外部命令。wrapper 必须校验调用进程为 root，并在非 root 时以退出码 2 拒绝。wrapper 必须为单次 `xhci-rebind` 执行设置总超时上限，且超时预算覆盖枚举、间隔等待以及内核同步 `unbind`/`bind` probe 阻塞。单次执行超过总超时上限时，wrapper 必须中止后续目标并以退出码 3 返回。门控通过后，wrapper 会全量枚举并重绑该驱动下的全部已绑定 BDF，其运行期防误伤依赖整树门控（含 `other_usb_nodes == 0`）。运维人员在灰度选型时必须排除连接关键非目标 USB 外设的主机。
  4. **退出码与部分成功 / `probe -110` 契约**：全部枚举到的有效 BDF 均完成 `unbind`/`bind`、重绑后驱动目录下重新出现对应 BDF 链接、且以 `O_RDONLY | O_NOFOLLOW` 读取该 BDF 已校验 `/sys/devices/` 真实目录下的 `enable` 属性文件值不为 `0` 时，wrapper 必须以退出码 0 返回。参数校验失败、非 root 或路径/`O_NOFOLLOW` 校验失败时，wrapper 必须以退出码 2 返回并在 stderr 输出 `STP_AGENT_PRIV_ERROR`。未枚举到任何有效 BDF、任一 BDF 的 `unbind`/`bind` 写入失败、部分 BDF 成功但其余失败、总超时或重绑后出现无 BDF 链接 / `enable == 0`（对应 `incident-2026-07-29` §6.1 `probe -110` 态）时，wrapper 必须以退出码 3 返回并在 stderr 输出带 `STP_XHCI_REBIND_*` 标记的逐 BDF 结构化摘要。
  5. **sudoers 边界与门控/`claim_loop` 并发互斥**：安装链与部署脚本不得因 D9 新增或修改任何 sudoers 行（沿用 D6 单一入口）。Agent 必须只在 `capabilities` 包含 `xhci-rebind` 且同时满足 `backend/agent/xhci_auto_rebind.py` 全部门控条件（`STP_XHCI_AUTO_REBIND=1`、主机命中白名单、`is_usb_tree_verifiably_empty`、台账设备行非空、连续空树拍数达标、`active_jobs == 0`、`active_devices == 0`、非维护窗）时，才可以进入触发流程。进入触发流程前，Agent 必须通过进程内互斥门闸暂停 `claim_loop` 的新任务认领（同时覆盖普通 `effective_slots` 与 `vbus_slots`）。持有暂停门闸后，Agent 必须复核 `occupancy.job_ids`、`occupancy.device_ids` 与 `local_db` 活跃作业均为 0、门控快照鲜度未超过 1 个心跳周期且主机未进入维护窗。复核任一项不满足时，Agent 必须立即放弃本次 `xhci-rebind` 并释放暂停门闸。`xhci-rebind` 执行完成并结束重枚举冷却前，Agent 不得恢复 `claim_loop` 认领。
  6. **`RebindFuse` 防洗白持久化与熔断升级**：Agent 必须将 `RebindFuse` 状态（含 `startup_identity.read_boot_id()` 读取的当前 `boot_id`、`boot_attempts`、`window_attempts` 48 小时时间戳、`episode_failures` 与 `open_reason`）原子持久化至热更新代码同步目录之外的本地状态文件。Agent 重启或热更新后加载该状态文件时，若记录的 `boot_id` 与当前主机一致，Agent 必须完整恢复全部计数与熔断状态。若 `boot_id` 已变更（主机已重启），Agent 可以清零 `boot_attempts`、`episode_failures` 与 `boot` 级熔断。即使 `boot_id` 已变更，Agent 也必须按当前时间裁剪并保留未满 48 小时的 `window_attempts` 记录。本地状态文件损坏、不可读或权限异常时，Agent 必须 fail-closed 拒绝自动 `xhci-rebind` 并向控制面上报 `need_human`。Agent 必须在调用 `stp-agent-priv xhci-rebind` 之前先执行 `record_attempt` 并落盘，使同 boot 与 48 小时各自最多 2 次的配额在崩溃、超时或部分成功时均被计入。wrapper 返回 `probe` 失败标记（重绑后无 BDF 链接或 `enable == 0`）时，Agent 必须立即将熔断置为 `hardware_suspect` 并停止本 boot 后续自动恢复。wrapper 返回其他退出码 3 失败、或返回退出码 0 且重枚举窗口后 USB 树仍为空时，Agent 必须调用 `record_still_empty` 累计失败次数。单次故障失败次数超过重试上限（1 次）时，Agent 必须将熔断置为 `hardware_suspect` 并停止自动恢复。仅当重枚举窗口后 USB 树恢复非空时，Agent 才可以调用 `record_recovered` 清零 `episode_failures`。调用 `record_recovered` 时，Agent 不得清零同 boot 与 48 小时累计计数。熔断开启或恢复失败时，Agent 必须通过心跳 `self_heal` 状态向控制面上报熔断原因（`hardware_suspect` 或 `need_human`）。控制面收到熔断状态后，必须在 `/hosts` 展示该熔断原因并向通知中心发出升级告警。
  7. **L3 非提权恢复同构约束**：L3 批量 `adb offline` 空闲窗受限 `adb reconnect offline` 不涉及 root 提权，实施者不得将其纳入 wrapper。Agent 实施 L3 恢复时，必须遵守与第 5、6 条同构的空闲窗互斥门控与持久化熔断升级规则。
  8. **不可逆操作边界**：任何执行者不得把 `sysfs remove`（摘除失效 SuperSpeed Hub 上行分支，write-once 不可逆）纳入 wrapper 自动子命令。
- **D10 USB 反向供网受限通道（v0.9，#4212 / ADR-0066 v1.1 D6；Owner 已于 2026-11-28 依据 PR #4213 四轮独立复核裁决采纳，§4 不变量随 U3 实现合入转正）**：
  1. **窄子命令接口与参数解析边界**：wrapper 必须仅新增六个受控子命令：`usbnet-ensure-bridge`、`usbnet-attach`、`usbnet-detach`、`usbnet-revoke`、`usbnet-teardown` 与 `usbnet-status`。wrapper 必须在全部 D10 子命令解析器中启用 `allow_abbrev=False`。wrapper 必须校验调用进程有效 UID 为 `0`（root），在非 root 或参数校验失败时必须以退出码 `2` 拒绝并在 stderr 输出 `STP_AGENT_PRIV_ERROR`。wrapper 不得接受调用方传入任意网桥名、子网 CIDR、宿主上联网卡名、上游 DNS 列表或任意 `iptables`/`nft` 规则表达式。
  2. **Root 属主授权配置锚点（`/etc/stp-usbnet.conf`）与禁用态 fail-closed**：wrapper 必须从固定路径 `/etc/stp-usbnet.conf` 读取主机授权与拓扑配置，并校验其父目录 `/etc` 为 root 属主真实目录且非 group/other 可写、配置文件本身经 `O_NOFOLLOW` 打开为普通文件、属主为 `root:root`、非 group/other 可写、体积不超过 `64 KiB` 且 JSON schema 合法。配置文件必须包含：`usbnet_enabled`（布尔，缺省 `false`）、`host_id`、`control_plane_verify_pubkey`（控制面 Ed25519 公钥，私钥不得落于 Agent host）、`allowed_usb_ports`（物理 USB 端口拓扑白名单，排除宿主管理 USB 网卡与非测试口）、`allowed_egresses`（Phase 1 仅限 `["direct"]`）、`egress_profiles`（受控网桥名必须匹配 `^br-stpusb-(dir|vpn)\Z`、保留子网 CIDR、上游 DNS 白名单与可选固定上联网卡名）及 `usbnet_max_devices`（整数 `1–32`，缺省 `8`）。当配置文件缺失、校验失败或 `usbnet_enabled != true` 时，wrapper 必须对**全部** D10 子命令（包含 `usbnet-status`）一律以退出码 `2` fail-closed 拒绝，不得修改任何系统状态，并在 stderr 输出结构化标记（`usbnet_enabled != true` 输出 `STP_USBNET_DISABLED`，配置缺失或校验失败输出 `STP_AGENT_PRIV_ERROR`）。仅当 `usbnet_enabled == true` 且全部只读自检通过时，`usbnet-status` 才可以返回退出码 `0` 并只读输出当前授权状态、单机硬上限、受控网桥、`active_bindings`（含未收敛 `attaching` 条目）、`revocation_state` 摘要（至少包含 `min_allowed_iat_by_egress` 与 `min_allowed_generation_by_serial`，缺席键默认视为 `0`）与 `dnsmasq` 健康态；Agent 必须将 `usbnet-status` 返回退出码 `0` 作为心跳上报 `"usb_net"` 能力的硬前置，且必须读取 `min_allowed_iat_by_egress` 随心跳及 `grant-renew` 上报控制面并在 `host_now <= min_allowed_iat_by_egress[egress]` 冷却窗内暂缓该出口挂桥与自愈。
  3. **Root 属主成员与撤销台账（`/run/stp-agent-priv/usbnet-bindings.json`）与代次比较域**：wrapper 必须在固定目录 `/run/stp-agent-priv`（root 属主、`0700`、非 symlink）下维护状态文件 `usbnet-bindings.json`（root 属主、`0600`、非 symlink），全部读写必须在 `flock` 排他锁内执行并经同目录临时文件 `fsync` + `os.replace` 原子落盘。台账必须持久化两张表：① `active_bindings`（按 `(session_id, generation)` 索引 `{state, egress, device_serial, iface, ifindex, usb_devpath, mac, attached_at}`，其中 `state ∈ {"attaching", "attached"}`，并对任意活跃条目强制维护 `usb_devpath` 与 `iface` 的单口唯一占用约束）；② `revocation_state`（精确已撤销集合 `revoked_sessions`，每条记录 `{session_id, generation, device_serial, revoked_at}`；**仅按 `device_serial` 索引**的单调水位 `min_allowed_generation_by_serial`；以及按 `egress` 索引的单调关闭签发时间屏障 `min_allowed_iat_by_egress`，两张映射表的缺席键均默认视为 `0`）。因 `lease_generation` 按设备单调递增、不能跨设备比较，wrapper 不得维护按 `usb_devpath` 索引的代次水位；同一物理 USB 口换入新设备时，只要该口在 `active_bindings` 中无活跃占用，新设备即使自身代次小于该口前一台设备的代次也必须可以正常准入。wrapper 对 `revoked_sessions` 执行有界裁剪时，仅当某条目的 `generation < min_allowed_generation_by_serial[device_serial]` 或 `now - revoked_at > 3600` 秒（超过最大 `usbnet_grant` 有效期 `1800` 秒的 2 倍）时才可以移除该条目。当台账目录或文件出现损坏、symlink 或属主/权限异常时，wrapper 必须以退出码 `2` fail-closed 拒绝写操作。
  4. **`usbnet-attach` 验签、可执行 sysfs 身份校验、隔离前置与两阶段提交**：`usbnet-attach` 必须只接受固定参数 `--grant <token> --iface <ifname> --expect-devpath <bus-port>`。wrapper 必须使用 root 属主 `control_plane_verify_pubkey` 核验 `--grant` 的 Ed25519 签名、完整载荷字段（`session_id`、`generation`、`host_id`、`device_serial`、`usb_devpath`、`egress`、`iat`、`exp`）、`now < exp`、`grant.iat <= now + 30` 秒（拒绝超前时钟签发）与 `exp - iat` 落于 `[300, 1800]` 秒内（默认 `900` 秒，对齐 ADR-0066 D6.3），并核验 `grant.host_id == conf.host_id` 且 `grant.egress` 位于 `conf.allowed_egresses`。持有台账锁后，wrapper 必须核验三项撤销屏障（两张水位映射表缺席键均默认视为 `0`）：① `(grant.session_id, grant.generation)` 不在 `revocation_state.revoked_sessions` 中；② `grant.generation >= min_allowed_generation_by_serial[grant.device_serial]`；③ `grant.iat > min_allowed_iat_by_egress[grant.egress]`（阻断上次 `usbnet-teardown` 之前已签发但尚未挂桥的旧授权）。命中任一撤销屏障时，即使签名有效且尚未到 `exp`，wrapper 也必须以退出码 `3` 并在 stderr 输出 `STP_USBNET_GRANT_REVOKED` 拒绝。随后 wrapper 必须按以下可执行 sysfs 判据核验目标网卡（与既有 `usb-authorized` 模式同构）：`--iface` 必须匹配 `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,14}\Z` 且不得为宿主默认路由上联口、`lo`、`docker0` 或任意网桥；wrapper 仅可以对三处内核 sysfs 符号链接调用 `realpath` 并强制校验解析目标——① `/sys/class/net/<iface>` 解析结果必须位于 `/sys/devices/` 下、末段匹配 `/net/<iface>` 且不得位于 `/sys/devices/virtual/`；② `<net_real>/device` 解析结果必须位于 `/sys/devices/` 下、处于 USB 总线路径且末段匹配合法 USB 接口目录名（`<bus>-<port>:<config>.<intf>`），其父目录名必须匹配既有 `_USB_PORT_RE` 并作为内核实读 `usb_devpath`；③ `<usb_intf_real>/driver` 解析结果必须位于 `/sys/bus/usb/drivers/` 下且末段基名必须属于 `{rndis_host, cdc_ncm}`。wrapper 必须以 `O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC` 打开已校验的真实目录 `<net_real>` 与 `<usb_dev_real>`，并通过 `openat(dir_fd, attr, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)` 且校验 `S_ISREG` 有界读取全部叶子属性（`ifindex`、`address`、`serial`、`idVendor`）。内核实读 `usb_devpath` 必须落于 `conf.allowed_usb_ports` 且同时全等于 `--expect-devpath` 与 `grant.usb_devpath`，内核实读 `serial` 必须全等于 `grant.device_serial`。wrapper 必须定义 `other_bindings` 为 `active_bindings` 中键不等于 `(grant.session_id, grant.generation)` 的全部活跃条目（含 `attaching` 与 `attached`），校验 `len(other_bindings) < conf.usbnet_max_devices` 且 `usb_devpath` 与 `iface` 未被 `other_bindings` 占用；若 `(grant.session_id, grant.generation)` 已存在于 `active_bindings` 中且其 `usb_devpath`、`device_serial` 与 `egress` 与本次 `grant` 一致，wrapper 不得将其重复计入容量上限（满额时同样可以幂等重入收敛），若核心身份字段冲突则必须以退出码 `2` fail-closed 拒绝。为确保「L2 隔离未就绪时端口不得转发」，wrapper 必须严格按以下顺序执行两阶段提交：① 先将本条目以 `state="attaching"` 原子落盘至 `active_bindings`；② 先执行 `ip link set dev <iface> down` 将接口置于管理关闭态；③ 在 `DOWN` 态执行挂桥 `ip link set dev <iface> master <bridge>`；④ 执行逐口 L2 隔离 `bridge link set dev <iface> isolated on`，并经 `<net_real>/brport/isolated`（`O_NOFOLLOW` + `S_ISREG`）回读核验其值全等于 `"1"`（在回读核验为 `"1"` 之前，wrapper 不得将 `<iface>` 置为 `UP`）；⑤ 复核 `ifindex` 未变且 `brport/isolated == "1"` 后，才执行 `ip link set dev <iface> up`，并将台账条目原子更新为 `state="attached"` 同时将 `min_allowed_generation_by_serial[grant.device_serial]` 单调推进至 `grant.generation`。若步骤 ②–⑤ 任一步失败，wrapper 必须首先执行 `ip link set dev <iface> down` 强制维持端口不得转发，再尽力执行摘桥 `ip link set dev <iface> nomaster`：摘桥成功时删除该 `attaching` 条目并返回退出码 `3`（`STP_USBNET_ATTACH_ROLLBACK`）；摘桥失败（回滚未完成）时端口必须保持 `DOWN` 态不得转发，保留该 `attaching` 条目供后续 `usbnet-detach` 或重入收敛并返回退出码 `3`。同一 `(session_id, generation)` 在 `attaching` 态重入 `usbnet-attach` 时，也必须完整走完步骤 ②–⑤ 并完成 `brport/isolated == "1"` 回读核验后才可以置 `UP`。
  5. **`usbnet-detach`、`usbnet-revoke` 与 `usbnet-teardown`（防接口名复用、半态收敛与零残留关闭）**：`usbnet-detach` 必须只接受 `--session-id <id> --generation <gen> [--expect-iface <ifname>] [--expect-devpath <bus-port>]`，且不得依赖短期 `usbnet_grant` 有效期。持有台账锁后，wrapper 必须按 `(session_id, generation)` 查询 `active_bindings`（同时覆盖 `attached` 与未收敛 `attaching` 条目）；未命中时，wrapper 必须补记 `revoked_sessions` 并以退出码 `0` 幂等返回，不得盲操作任意网卡。调用方提供 `--expect-iface` 或 `--expect-devpath` 且与台账记录不符时，wrapper 必须以退出码 `2` 与 `STP_USBNET_BINDING_MISMATCH` 拒绝。命中台账时，wrapper 必须按 D10.4 同构可执行 sysfs 判据（`realpath` 解析 `/sys/class/net/<bound_iface>` 与 `device` 校验 `/sys/devices/` 真实目录，叶子属性以 `O_NOFOLLOW` 读取）比对 `ifindex`、`usb_devpath` 与 `mac`，并按以下完备三分支收敛：① **接口消失或身份不符**：若接口已消失或 `ifindex`/`usb_devpath`/`mac` 任一项与台账不符（表明接口名已被新设备或新会话复用），wrapper 不得操作当前同名新接口，必须仅删除旧 `(session_id, generation)` 台账记录、写入 `revoked_sessions`、推进 `min_allowed_generation_by_serial[device_serial]` 至不小于 `generation` 并以退出码 `0` 返回 `already_gone_or_reused`；② **身份匹配但未挂入本受控网桥（含「预写 `attaching` 后、挂桥前中断」场景）**：若 `ifindex`/`usb_devpath`/`mac` 全部匹配且接口当前 `master` 为空（未入任何网桥），wrapper 必须先确保执行 `ip link set dev <bound_iface> down`（不执行无效摘桥），随后删除该台账记录、写入 `revoked_sessions`、推进 `min_allowed_generation_by_serial[device_serial]` 至不小于 `generation` 并以退出码 `0` 返回 `detached_unbridged`（若接口意外挂在非本受控网桥上，wrapper 不得操作他桥成员，必须仅清理自身无效台账、写入撤销并推进水位，返回 `already_gone_or_reused`）；③ **身份与本受控网桥成员关系全部匹配**：wrapper 必须先执行 `ip link set dev <bound_iface> down` 与摘桥 `ip link set dev <bound_iface> nomaster`，随后删除台账记录、写入 `revoked_sessions`、推进 `min_allowed_generation_by_serial[device_serial]` 至不小于 `generation` 并以退出码 `0` 返回 `detached`。`usbnet-revoke --session-id <id> --generation <gen> --serial <serial>` 必须持锁将 `(session_id, generation)` 写入 `revoked_sessions` 并单调推进 `min_allowed_generation_by_serial[serial]` 至不小于 `generation`，且不得删除 `active_bindings` 中尚未摘除的既有挂桥记录。`usbnet-teardown --egress <direct|vpn> [--force]` 必须持锁检查该出口的 `active_bindings`：若存在活跃绑定且未传入 `--force`，wrapper 必须以退出码 `3`（`STP_USBNET_ACTIVE_BINDINGS`）fail-closed 拒绝；若无活跃绑定或显式传入 `--force`，wrapper 必须按以下顺序执行关闭：① **无条件先持久化撤销屏障**：无论该出口当前 `active_bindings` 是否为空，均先将 `revocation_state.min_allowed_iat_by_egress[egress]` 单调推进至 `max(cur, now + 30)`（阻断关闭前已签发但尚未挂桥的全部旧授权，同时覆盖 `<= 30s` 时钟偏移），并将该出口全部 `active_bindings` 的 `(session_id, generation)` 写入 `revoked_sessions`、推进对应 `min_allowed_generation_by_serial[device_serial]`，一次性原子落盘（确保即使后续清理步骤中途失败或后续重建网桥，关闭前已签发的已挂桥与待挂桥旧授权均持续失效）；② 逐条按本款三分支对台账内该出口绑定执行 `down` + 安全摘桥（或清理未入桥 `attaching`）并移除该出口 `active_bindings` 记录；③ 停止该出口 `dnsmasq` 进程并清理其 `/run/stp-agent-priv/` 下 PID 与租约文件；④ 摘除并删除该出口专属规则链；⑤ 删除受控网桥 `br-stpusb-*`；⑥ 执行零残留复核（网桥接口不存在、该出口 `dnsmasq` 进程已退出、专属规则链已清空且 `active_bindings` 该出口条目数为 `0`）后返回退出码 `0`；步骤 ②–⑥ 任一步失败或复核发现残留时，wrapper 必须保留已落盘的 `revocation_state`（含 `min_allowed_iat_by_egress`）并以退出码 `3`（`STP_USBNET_TEARDOWN_INCOMPLETE`）返回，支持幂等重跑 `usbnet-teardown --egress <egress> --force` 收敛剩余状态。
  6. **外部命令收口与三层默认拒绝隔离**：wrapper 执行 `ip`、`bridge`、`iptables`、`nmcli`、`dnsmasq` 时必须使用固定绝对路径与固定 argv、清空环境变量（只留 `LC_ALL=C` 与固定 `PATH`）、设置单命令不超过 `30s` 超时，且不得经 shell 执行。`usbnet-ensure-bridge` 启动的 `dnsmasq` 必须强制携带 `--port=0 --bind-interfaces --interface=<bridge>` 并将 PID 与租约文件落在 `/run/stp-agent-priv/` 下，不得监听宿主 `53` 端口；主机部署配置必须确保 NetworkManager 等外部网络管理器不自动接管受控 USB 网卡与受控网桥（避免在隔离就绪前擅自拉起接口）。`usbnet-ensure-bridge` 必须通过该出口专属规则链（`STP_USBNET_IN_<egress>`、`STP_USBNET_FWD_<egress>`、`STP_USBNET_NAT_<egress>`）安装三层默认拒绝隔离规则（便于 `usbnet-teardown` 精确清理而不误伤宿主机既有规则）：① `INPUT` 链对源自 `br-stpusb-*` 流量默认 `DROP`，仅放行 DHCP（`UDP 67/68`）、ICMP echo-request 与（若配置显式启用本地 DNS 转发）绑定该网桥 IP 的 `UDP/TCP 53`，必须阻断测试机访问宿主机 SSH、控制面 HTTP、PostgreSQL、Redis 及全部其他本机监听端口；② `FORWARD` / `DOCKER-USER` 链必须阻断 `br-stpusb-*` 向宿主内网管理网段、其他 Docker 网桥及 RFC1918 / link-local 转发（仅白名单放行 `conf` 登记的上游 DNS `UDP/TCP 53` 与 Phase 2 授权出口网关），其余仅放行发往公网的出站流量与 `RELATED,ESTABLISHED` 回包；③ `usbnet-attach` 必须按 D10.4 在 `DOWN` 态完成挂桥与 `bridge link set dev <iface> isolated on` 并回读 `brport/isolated == "1"` 后才可以置 `UP`，确保隔离未就绪时端口不得转发任何流量。
  7. **sudoers 边界、能力标记与不变量转正**：安装链与部署脚本不得因 D10 新增或修改任何 sudoers 行（沿用 D6 单一入口）。wrapper 必须在 `capabilities` 输出中声明 `usbnet` 与参数级标记 `usbnet/grant-v1`；Agent 与脚本必须通过 `selftest` + `capabilities` 双前置判据校验后才可以调用 D10 子命令。D10 已由 Owner 依据 PR #4213 独立复核裁决采纳；随 U3（#4212）实现合入落地时，实施者必须将上述强控制清单与测试文件（`tests/test_agent_priv_usbnet.py`、`tests/test_agent_priv_parser_contract.py`）同步转正写入 §4 不变量第 2、3 条。

## 3. 备选与否决

- **缩短 sudoers 命令名单（参数级白名单）**：否决——`rsync`/`cp` 等工具的
  参数空间可组合出任意路径写与命令执行，sudoers 参数匹配不可靠（issue
  亦明确「不能只缩短命令名单」）。
- **保留宽规则 + 审计告警**：否决——不满足验收「无法对安装目录外提权写」。
- **控制面 CLI 直连 DB 代理升级操作**：与本议题正交（解决的是控制面入口
  而非主机提权面）。
- **把 wrapper 放在安装目录内**：否决——Agent 用户可写，等于把 root 执行
  权交回 Agent 用户。

## 4. 影响与不变量

- **不变量（目标态，v0.4 / O2 + S1 补全）**：
  1. Agent 用户的 NOPASSWD 面 ⊆ {固定 systemctl, wrapper}；wrapper 的所有写目标固定在安装目录内；
     wrapper 文件与 conf 必须 root 属主且非 group/other 可写（`selftest` 校验）；
  2. **参数面校验在进程内**（D6）：sudoers 不约束 wrapper 参数 ⇒ 以下强控制按「不可回退」对待——
     `--staged` 必须在 `INSTALL_DIR` 之外**且属主为调用者**（`fstat` 校验）、目标目录以 **fd 基**打开、
     rsync 以 agent 身份（`setuid`）执行并带 `--safe-links` 与固定 excludes（含 `resources/mtbf/`
     exclude+protect）、`usb-authorized` 走「`/sys/devices` 下与端口同名的真实目录」判定 +
     `O_NOFOLLOW` 属性读写、`fix-ownership` 仅遍历 `INSTALL_DIR` 且 `follow_symlinks=False`、
     `write-digest` 校验 `sha256:<64 hex>`、`bootstrap` 的 `INSTALL_DIR` 在 `realpath` 后
     与系统关键目录不得互相包含、`read-kernel-log` 的窗口/行数取值校验（互斥必选 +
     整数范围 + `allow_abbrev=False`）与子进程三收口（环境清空 / 30s 超时 / 8 MiB 上限，
     超限即非零退出、不部分投递）、`usb-power` 的映射文件整份校验（非 symlink、root 属主、非 group/other 可写、
     ≤ 64 KiB、父目录为 root 属主的真实目录且 group/other 不可写、schema / 字段 / `port` 1–16 合法、serial 与槽位无重复）、按 serial 查槽位且不接受
     指定切电目标的位置或端口参数、`--expect-slot` 在调用 `uhubctl` 之前比对（不一致即拒绝）、`--delay` 只用于 `cycle` 且有上限、
     `allow_abbrev=False`、`uhubctl` 为固定路径且 root 属主非 group/other 可写、`hub_location` 必须在内核 sysfs 树内，
     以及子进程收口（固定 argv / 环境只留 `LC_ALL` 与 `PATH` / 60s 超时 / 非 root 拒绝 / 成功回显实际槽位）（v0.7 / D8）、
     `xhci-rebind` 的无目标参数与 `allow_abbrev=False`、固定驱动目录 `/sys/bus/pci/drivers/xhci_hcd` 逐级 `O_NOFOLLOW | O_DIRECTORY`、
     BDF 正则与 `realpath` 落 `/sys/devices/` 校验、属性文件 `unbind`/`bind` 以 `O_WRONLY | O_NOFOLLOW` + `S_ISREG` 写入 BDF 名
     （`unbind→bind` 间隔 `2s`、单次总超时 `30s` 含 `SIGALRM` 覆盖同步 probe 阻塞、不经 shell、非 root 退出码 2）、
     重绑后驱动目录 BDF 链接与真实 PCI 目录下 `enable != 0`（`O_RDONLY | O_NOFOLLOW`）核验、
     失败/部分失败/超时/`probe` 失败退出码 3 + 五词标记（`STP_XHCI_REBIND_NO_PCI_IDS` / `STP_XHCI_REBIND_WRITE_FAILED` / `STP_XHCI_REBIND_PARTIAL` / `STP_XHCI_REBIND_TIMEOUT` / `STP_XHCI_REBIND_PROBE_FAILED`）逐 BDF 摘要，
     以及 Agent 侧 `RebindFuse` 按 `boot_id` 原子持久化至热更新同步目录之外的 `BASE_DIR` `0600` 文件（先 `record_attempt` 落盘再调用动作、损坏/symlink/权限异常 fail-closed）与 `ClaimPauseGate` 暂停 `claim_loop`（覆盖 `effective_slots` 与 `vbus_slots`、持续至 `15s` 重枚举冷却结束）（v0.8 / D9，PR #4120）；
  3. **测试是不变量的一部分**：上述控制由
     `tests/test_agent_priv_parser_contract.py`、`tests/test_agent_priv_boundary.py`、
     `tests/test_agent_priv_read_kernel_log.py`、`tests/test_agent_priv_usb_power.py`（v0.7）、
     `tests/test_agent_priv_xhci_rebind.py` 与 `backend/agent/tests/test_self_heal_coordinator_s4.py`（v0.8 / D9）、
     `tests/test_agent_priv_apply_code_protection.py`、
     `tests/test_remote_script_privilege_paths.py`
     锁定；修改这些控制必须同步这些测试（回归时不得无声削弱）；
  4. **信任边界**（v0.4 / O1）：bootstrap 的可调用主体不构成信任边界——conf 冻结（锚点不可变）才是。
- **迁移期实测偏差（2026-09-15）**：`/etc/sudoers.d/android` 宽规则仍存于
  48/48 台（含全部 wrapper 主机）——目标态不变量在清除动作完成前不成立；
  清除的前置与验收见 §5（D5 / #2133）与执行单 #2134。
- **失败模式**：wrapper/conf 缺失或旧版 → 热更新 **fail-closed**（v0.3 /
  #2180：selftest 前置失败即带 `update_agent.yml` 指引退出，`priv_mode=unknown`
  可观测，不再回退裸 sudo）；bootstrap 失败 → 安装/更新中止，不产出半迁移
  状态；控制面不可达与维护窗口语义见 #960/#1249，不受本 ADR 影响。
- **运维代价**：wrapper 本体更新需要 root（Ansible 更新或手工），不能走
  热更新自身——这是提权边界的设计代价，已写入 runbook。
- **回滚**（v0.3 / #2180 更新）：迁移期以 legacy 分支为回滚路径；迁移期结束后
  回滚通道 = ① wrapper 缺陷按 Revisit #4 修 wrapper 发新版本；② Ansible
  重跑重写 sudoers（`update_agent.yml`，走密码 become）；热更新本身不再有
  降级面。

## 5. 验收与 Revisit

- **验收**（#1250）：① 无法对安装目录外任意路径提权写（容器冒烟 + 校验
  单测覆盖拒绝面）；② 热更新仍可用（迁移期 = wrapper 路径 + legacy 回退；
  v0.3 / #2180 起 = wrapper 单路径 fail-closed，实测 48/48 收敛）；③ R02
  安全联审（独立于实现，需评审一稿）。
  **（v0.4：已交付。）** ③ 的评审稿 =
  [`docs/reviews/REVIEW_ADR0037_SECURITY_2026-09-15_3e833f.md`](../reviews/REVIEW_ADR0037_SECURITY_2026-09-15_3e833f.md)
  （issue #2206 / PR #2209），结论「建议接受、无阻断项」。**建议处置（v0.4 状态）**：
  S1（边界承担显式化 + 测试列为不变量）、O1（信任边界措辞）、O2（强控制入不变量）**本版已采纳**；
  S2（主机侧特权调用审计）**待排期**——属行为变更，需 ADR 增补 + runbook + 机队部署与观测；
  S3（宽根 `INSTALL_DIR` 纵深防御，现网 conf 全在 ⇒ 不可达）**待排期**——需守卫收紧 + 测试；
  O4（wrapper 版本标识，回滚/对账目前靠 sha 人工比对）**待排期**。
- **Revisit**：
  1. fleet 全部出现 `priv_mode=wrapper` **且 flash 链在无宽文件主机验收通过
     （D5 / #2133）**后：删除 legacy 分支与哨兵解析，并清除存量宽文件
     `/etc/sudoers.d/android`（visudo 校验、canary 先行；执行单 #2134）。
     **（2026-09-15 已执行，v0.3）** 判据达成：B 步 48/48 wrapper
     selftest 通过、C 步 48/48 宽文件清除 + 终审全绿；legacy 删除见
     [#2180](https://github.com/DUElost/stability-test-platform/issues/2180)
     （`docs/notes/bug-fix/2026-09-15-retire-legacy-priv-face-2180.md`）。
     残留观察项：#2134 的「异常设备 offline 处置」仍待尽（不在本 ADR 判据内）；
  2. ADR-0035 的 per-host 凭据落地后，重审 wrapper 的授权主体（从共享
     agent secret 切到主机身份）；
  3. flash 链运行时提权收敛（#2133）：`flash_preflight` / `flash_firmware`
     出**新版本**脚本改调 D5 窄子命令（不可原地改已发布版本）。本条 v0.1
     时为条件项（「若需恢复修复能力」）；v0.2 起因实证该能力在用而改为
     **下线前置**——宽文件删除前必须先完成收敛；
  4. 若出现「wrapper 缺陷导致热更新不可用」的事故，优先修 wrapper 并发新
     版本，不回退到宽 sudoers。
  5. **D7 灰度与生效（v0.6 / #2957）**：wrapper 不随热更新下发，需逐台跑
     `tools/ansible/playbooks/update_agent.yml`（或随下一次重装）。灰度 1 → 5 → 全量，
     每级观察 `stability_host_kernel_log_channel{state="unavailable"}` 回落
     （#2957 上线前实测 46/46 台为 `unavailable`）。**若首扫 1 小时窗被实证漏判**
     「agent 重启前已死且结果层未覆盖」的形态，评估给首扫加一次**有界**的 boot
     摘要路径（届时须同时保留截断→不可用判据）；C 方案实施 PR 未含独立安全评审，
     若团队要求，评审意见与本 ADR 冲突时以评审意见为准并回写 #2957。
  6. **D9 裁决落地与生产灰度验收（v0.8 / #2972）**：Owner 已于 2026-11-23 裁定采纳 D9
     （PR #4114 独立复核通过），PR #4120 已合入 `xhci-rebind` 子命令、`boot_id` 持久化熔断、
     `ClaimPauseGate`、L3 `adb offline` 同构自愈及配套测试（默认开关 `STP_XHCI_AUTO_REBIND=0`，
     合入不改变现网行为）。运维人员开启生产自动恢复前，必须先经 `update_agent.yml` 逐台下发新版
     `stp-agent-priv`。运维人员启用自动恢复时，必须以 `STP_XHCI_AUTO_REBIND_HOSTS` 执行
     1 → 5 → 全量灰度。
  7. **D10 落地实施与试点灰度验收（v0.9 / #4212 / ADR-0066）**：Owner 已于 2026-11-28 裁定采纳 D10
     （PR #4213 四轮独立复核通过），批次 B13（#4212）U3 实施者必须按 D10.1–D10.7 落地 `stp-agent-priv` 的
     `usbnet-*` 子命令与强控制测试并同批转正 §4 不变量第 2、3 条。运维人员在生产主机启用 USB 反向供网前，必须先经
     `tools/ansible/playbooks/update_agent.yml`（或部署流程）逐台下发新版 `stp-agent-priv`、
     root-owned `/etc/stp-usbnet.conf`（含 `allowed_usb_ports` 白名单）及控制面签发公钥，
     并按批次 B13（#4212 §2/§5）经 Owner 裁决的 `1 → 2 → 按需扩展` 试点方案（首台试点机 `usbnet_max_devices=4`，仅对有联网专项需求的主机按需开启）完成三层隔离红队测试与真机验收。
