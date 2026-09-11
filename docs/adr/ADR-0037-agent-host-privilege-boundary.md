# ADR-0037：Agent 主机提权边界（Privilege Boundary Wrapper）

- 状态：**Proposed**
- 版本记录：v0.1（2026-09-11 初版，R14-F04 #1250 触发）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-11
- 决策者：平台研发组（R02 安全联审）
- 标签：安全, 提权, sudoers, 热更新, Agent 主机
- 关联：R14 台账 [#1266](https://github.com/DUElost/stability-test-platform/issues/1266)（R14-F04 [#1250](https://github.com/DUElost/stability-test-platform/issues/1250)）；R02 安全审查（联审项）；ADR-0035（主机身份与凭据方向，wrapper 鉴权面待其落地后重审）；#960（维护窗口）；#1247/#1248（热更新工件与主机本地资源保护）

## 1. 背景

### 1.1 问题定性：不是命令名单过长，而是没有提权边界

`install_agent.sh` 为 Agent 用户（`android`）落 NOPASSWD sudoers，其中
`rsync` / `cp` / `chmod` / `chown` / `ln` 均**无参数限制**：拿到 Agent 用户
执行能力即可通过 `sudo rsync` 等对任意 root 文件提权写，非 root Agent 的
权限边界不成立（R14 二次核验属实）。

### 1.2 事实核验（2026-09-11，静态盘点 + 容器实证）

旧规则的**真实消费方只有 UI 热更新远端脚本**（`host_updater.py`）：代码同步
（rsync）、schema 安装（mkdir/install）、VERSION 与依赖标记（tee）、`.env`
写入（root python heredoc）、属主回收（chown）、服务重启（systemctl）。盘点
结果：

- `cp` / `ln` / `stat` / `chmod` 无任何运行时消费方（仅运维文档中人工使用
  密码 sudo）；
- Ansible 安装/更新链用 `become`（inventory 密码作 sudo 密码）完成 root
  操作，**不依赖** NOPASSWD 规则；
- `flash_preflight v1.0.x`（已发布不可变）期望 `sudo -n sh -c <任意命令>` 做
  apt/usermod/udev 修复，而现行规则本就不含 `sh`/`apt-get`/`usermod`——该
  修复分支现状已不可用，本次收窄不构成回归（恢复能力需新版本脚本，见 §5）。

### 1.3 约束

免密热更新是产品既有能力（UI 按钮、批量脚本）；`android` 只应获得「升级
自身」所需的固定操作，不应获得任意 root 文件写或任意命令执行。

## 2. 决策

- **D1 单一提权入口**：新增 root:root 0755 的 wrapper
  `/usr/local/sbin/stp-agent-priv`（**不得**位于 Agent 可写的安装目录内）；
  sudoers 只授两类命令：该 wrapper 单命令 + 固定服务名的 `systemctl`
  子命令。任意 `rsync/cp/chmod/chown/ln/stat` 免密规则全部删除。
- **D2 子命令白名单 + 内部校验**：wrapper 仅接受固定子命令
  （`bootstrap/selftest/apply-code/install-schema/write-version/sync-env/
  deps-marker/fix-ownership/restart`），且内部强制：
  所有目标路径固定（`$INSTALL_DIR` 及固定子路径，组件级前缀校验）、
  源文件/暂存目录必须为调用者属主、schema 必须通过 JSON 结构校验、
  版本/SHA 走字符集正则、`chown -R -h`（symlink 不 deref）、rsync 使用
  `--safe-links` 与固定 excludes（含 `resources/mtbf/` 的
  exclude+protect，#1248 语义）。
- **D3 存量迁移**：install 链（`install_agent.sh` 内 bootstrap）与
  `update_agent.yml`（Ansible `become`）都部署 wrapper 并生成/重写
  conf 与 sudoers（visudo 校验后原子替换，失败即中止）。迁移期热更新检测
  wrapper：缺失则回退 legacy 旧 sudo 面并输出 `STP_PRIV_FALLBACK=legacy`
  哨兵（控制面 `priv_mode` 审计可见）；sudoers 重写完成的主机自动走
  wrapper。legacy 分支在 fleet 迁移完成后删除（见 §5 Revisit）。
- **D4 显式不做**：不动 Ansible 自身的密码 become 通道（与 NOPASSWD 面
  无关）；不引入 per-host 凭据（ADR-0035 实施面）；wrapper 不放进安装
  目录，也不提供任何「任意目标路径」参数。

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

- **不变量**：Agent 用户的 NOPASSWD 面 ⊆ {固定 systemctl, wrapper}；
  wrapper 的所有写目标固定在安装目录内；wrapper 文件与 conf 必须
  root 属主且非 group/other 可写（`selftest` 校验）。
- **失败模式**：wrapper/conf 缺失 → 热更新走 legacy + 哨兵（可观测，
  sudoers 重写后自动消失）；bootstrap 失败 → 安装/更新中止，不产出半迁移
  状态；控制面不可达与维护窗口语义见 #960/#1249，不受本 ADR 影响。
- **运维代价**：wrapper 本体更新需要 root（Ansible 更新或手工），不能走
  热更新自身——这是提权边界的设计代价，已写入 runbook。
- **回滚**：迁移期 legacy 分支即回滚路径；sudoers 由 Ansible 重跑重写。

## 5. 验收与 Revisit

- **验收**（#1250）：① 无法对安装目录外任意路径提权写（容器冒烟 + 校验
  单测覆盖拒绝面）；② 热更新仍可用（wrapper 路径 + legacy 回退）；③ R02
  安全联审（独立于实现，需评审一稿）。
- **Revisit**：
  1. fleet 全部出现 `priv_mode=wrapper` 后，删除 legacy 分支与哨兵解析；
  2. ADR-0035 的 per-host 凭据落地后，重审 wrapper 的授权主体（从共享
     agent secret 切到主机身份）；
  3. `flash_preflight` 若需恢复修复能力，出**新版本**脚本改调 wrapper
     子命令（不可原地改已发布版本）；
  4. 若出现「wrapper 缺陷导致热更新不可用」的事故，优先修 wrapper 并发新
     版本，不回退到宽 sudoers。
