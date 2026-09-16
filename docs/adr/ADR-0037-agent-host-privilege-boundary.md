# ADR-0037：Agent 主机提权边界（Privilege Boundary Wrapper）

- 状态：**Accepted（v0.5）**
- 版本记录：v0.5（2026-09-16：§2 D2 事实勘误——`selftest` 只证 wrapper **自洽**，不证它具备热更新脚本将要调用的子命令；脚本头改为「selftest + 能力集合比对」双前置判据，wrapper 新增 `capabilities` 子命令，#2319）；v0.1（2026-09-11 初版，R14-F04 #1250 触发）；v0.2（2026-09-15：§1.2 事实勘误、§2 新增 D5、§4 偏差记录、§5 退役前置修订，#2133）；v0.3（2026-09-15：§5 Revisit #1 执行完毕——legacy 分支与哨兵删除、失败模式改 fail-closed、§4 回滚路径更新，#2180）；v0.4（2026-09-15：**转 Accepted**——R02 安全联审一稿已交付（#2206 / PR #2209，结论「建议接受、无阻断项」）；采纳 S1/O1/O2：§2 新增 D6 边界承担声明、D3 补信任边界措辞、§4 不变量补既有强控制与测试清单、§5 ③ 记评审交付与 S2/S3/O4 处置）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-11（v0.2 / v0.3 修订 2026-09-15；v0.4 修订 2026-09-15；v0.5 勘误 2026-09-16）
- 决策者：平台研发组（R02 安全联审；2026-09-15 依据评审一稿裁决转 Accepted）
- 标签：安全, 提权, sudoers, 热更新, Agent 主机
- 关联：R14 台账 [#1266](https://github.com/DUElost/stability-test-platform/issues/1266)（R14-F04 [#1250](https://github.com/DUElost/stability-test-platform/issues/1250)）；R02 安全审查（联审项）；ADR-0035（主机身份与凭据方向，wrapper 鉴权面待其落地后重审）；#960（维护窗口）；#1247/#1248（热更新工件与主机本地资源保护）；[#2133](https://github.com/DUElost/stability-test-platform/issues/2133)（flash 链运行时提权收口，v0.2 新增）；[#2134](https://github.com/DUElost/stability-test-platform/issues/2134)（宽文件清除与 legacy 退役）；[#2180](https://github.com/DUElost/stability-test-platform/issues/2180)（legacy 分支与哨兵删除，v0.3）

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
  迁移期结束删除，filter 只此一处。）
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
     与系统关键目录不得互相包含；
  3. **测试是不变量的一部分**：上述控制由
     `tests/test_agent_priv_parser_contract.py`、`tests/test_agent_priv_boundary.py`、
     `tests/test_agent_priv_apply_code_protection.py`、`tests/test_remote_script_privilege_paths.py`
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
