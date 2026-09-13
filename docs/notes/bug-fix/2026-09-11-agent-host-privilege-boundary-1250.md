# Agent 主机提权边界：wrapper 单入口（#1250 / R14-F04）

Status: implemented
Class: bug-fix

## Decision

根因：`install_agent.sh` 给 Agent 用户落无参数限制的免密
`rsync/cp/chmod/chown/ln/stat`——取得 Agent 用户执行能力即可对任意 root 文件
提权写（R14 二次核验属实；`cp/ln/stat/chmod` 甚至没有运行时消费方）。修复
不让命令名单「变短」（工具参数空间仍可组合出任意写/执行），而是建立**单一
提权入口**（ADR-0037，Proposed 待 R02 联审）：

1. 新增 `backend/agent/stp_agent_priv.py`（`#!/usr/bin/python3`，仅 stdlib，
   3.6+）→ 部署为 `/usr/local/sbin/stp-agent-priv`（root:root，**不在 Agent
   可写的安装目录内**）。子命令：`selftest / bootstrap / apply-code /
   install-schema / write-version / sync-env / deps-marker /
   fix-ownership / restart`；内部强制路径前缀、调用者属主、schema JSON
   结构、版本/SHA 正则、`chown -R -h`、rsync `--safe-links` + 固定 excludes
   （含 `resources/mtbf/` 的 exclude+protect，#1248 同源语义）。
2. sudoers 只保留：wrapper 单命令 + **固定服务名**的 systemctl 子命令；
   `rsync/cp/chmod/chown/ln/stat` 免密规则全部删除。conf 与 sudoers 由
   `bootstrap` 生成（visudo 校验后原子替换，失败即中止安装/更新）。
3. 存量迁移（用户裁决：**legacy fallback** 而非立即切断）：`update_agent.yml`
   用 Ansible `become`（密码 sudo，不依赖 NOPASSWD）部署 wrapper 并 bootstrap
   重写 sudoers；热更新远端脚本先探 `selftest`，缺失则走旧规则并输出
   `STP_PRIV_FALLBACK=legacy`，控制面新增 `priv_mode` 字段入审计与响应
   （fleet 迁移进度可观测）。sudoers 重写完成的主机自动走 wrapper——安全面
   由 sudoers 决定，fallback 不延长漏洞窗口。
4. ADR-0021 之外补 ADR-0037 承载提权边界契约（wrapper 接口/不变量/迁移与
   回滚），作为 R02 联审锚点。

顺带发现（记录在 ADR-0037 §1.2）：`flash_preflight v1.0.x`（已发布不可变）
期望 `sudo -n sh -c` 做 apt/usermod/udev 修复，而现行规则本就不含
`sh/apt-get/usermod`——该修复分支现状已不可用，本次收窄不构成回归；恢复
能力需新版本脚本改调 wrapper 子命令（Revisit）。

2026-09-13 目标路径闭环（#1821，最近 7 天审计 F01）：固定字符串与 rsync
`--safe-links` 不能约束 Agent 可替换的接收目录。所有安装树目标改为逐组件
`O_NOFOLLOW` 打开并保持目录描述符；原子写入使用相对描述符的创建与 rename，
仅在尚未发布的文件描述符上设置 mode/owner。schema 只读取并校验一次，复制
已验证的字节；`.env` 不跟随链接且沿用同一次读取取得的属主/权限。
`fix-ownership` 使用不跟随链接的 `fwalk`、描述符相对 `chown` 与 `fchown`，
保持原 `chown -R -h` 的非解引用语义，不再重新解析安装根。

代码传输的源/接收根通过继承的描述符交给 rsync；子进程先切换到配置的
非 root Agent uid/gid/附加组，关闭 owner/group 保留，因此可变后代路径也不能
借 rsync 进行提权写。合法安装链已把安装树交给 Agent；异常 root-only 树
失败关闭，不回退 root rsync。wrapper 自身仍需 Ansible/root 通道更新，
本修复没有在生产主机部署。

## Alternatives

- **缩短 sudoers 命令名单（参数白名单）**——否决：`rsync/cp` 参数可组合出
  任意路径写与执行，sudoers 参数匹配不可靠（issue 亦明确）；
- **fail-closed 立即切换**——否决（用户裁决）：迁移未完成前 UI 热更新不可用，
  且安全窗口由 sudoers 而非 host_updater 决定，fallback 不额外延长风险；
- **控制面 CLI 直连 DB 代理提权**——与本议题正交（主机侧提权面）；
- **wrapper 放安装目录**——否决：Agent 可写 = root 执行权交回 Agent 用户；
- **独立 ops token / per-host 凭据**——ADR-0035 实施面，不引入并行凭据体系；
- **保留宽规则 + 告警**——不满足验收「无法对安装目录外提权写」。
- **只在写前检查 realpath**——#1821 否决：检查后仍可替换目录；必须让实际
  系统调用消费已打开的目录，并移除 rsync 的 root 权限。

## Verification

#1821 本次实际运行：

- `python -m pytest tests/test_agent_priv_boundary.py tests/test_agent_priv_target_paths.py -q`
  → **46 passed**；包括目标/祖先链接、固定目录后替换、临时文件替换、schema
  验证后换源、环境元数据、非解引用递归属主及 Python 3.6 语法。
- `bash tools/dev/stp_agent_priv_smoke.sh /home/debian13/stp-fix-1821` → **ALL_OK**；
  一次性容器内真实 root wrapper + 降权 rsync，验证正常更新和外指接收根拒绝。
- `python -m ruff check backend/agent/stp_agent_priv.py tests/test_agent_priv_boundary.py tests/test_agent_priv_target_paths.py`
  与 `bash -n tools/dev/stp_agent_priv_smoke.sh` → 通过。
- `python scripts/run_gates.py check:quick` → **7 gates 通过**。

此前 #1250 实际运行（历史证据，不代表本次重跑）：

- `pytest tests/ backend/tests/services/test_host_updater.py -q` → **146 passed**
  （合并 #1249 基线后重跑；wrapper/legacy 接线、sudoers 规则面、安装/迁移链、
  路径与正则校验、LF 与冒烟脚本宿主保护）；
- `pytest backend/tests/api/test_upgrade_gate_api.py backend/tests/api/test_plan_run_abort_api.py -q`
  → **26 passed**（本单在 #1249 重构后的热更新路由上补 `priv_mode` 字段，
  冲突已手工解并跑通门禁/软锁回归）；
- **容器冒烟**（隔离 docker，宿主只做编组、payload 仅在容器 root 下执行）：
  `bash tools/dev/stp_agent_priv_smoke.sh .` → ALL_OK：
  bootstrap 后 sudoers 无宽规则；`apply-code` 同步代码/删除陈旧文件/保留
  `resources/mtbf/`，拒绝 `/etc` 与 root 属主暂存目录；`install-schema` 拒绝
  `/etc/passwd`；`write-version` 拒绝注入；`sync-env` 保留 `.env` 属主；
  `fix-ownership` 下 symlink 目标 `/etc/passwd` 保持 root；`restart` 生效；
- `ruff check`（改动文件 + ruff.toml 新增 wrapper 的 T20 豁免）；
- 渲染后的热更新远端脚本 `bash -n` 通过（9 处 wrapper 调用 + legacy 分支）；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- **R02 安全联审**（#1250 验收第 3 条）——独立于实现，需评审一稿；
- **真实主机迁移验证**：隔离主机跑 `update_agent.yml`（wrapper 安装 + sudoers
  重写 + `priv_mode` 观测）——本机为生产控制面宿主，未做跨机破坏性验证；
- 存量主机全部迁移前，`priv_mode=legacy` 仍会出现（预期，见 Revisit ①）。

## Revisit

- fleet 全部 `priv_mode=wrapper` 后，删除 host_updater 的 legacy 分支与哨兵
  解析（ADR-0037 §5 Revisit ①）；
- ADR-0035 per-host 凭据落地后重审 wrapper 授权主体（共享 agent secret →
  主机身份）；
- `flash_preflight` 恢复修复能力需**新版本**脚本改调 wrapper 子命令；
- wrapper 本体更新需 root（Ansible 或手工），不能走热更新自身——若因此出现
  运维痛点，评估给 wrapper 增加「只读自检 + 版本上报」以便控制面提示升级，
  但不为便利回退宽 sudoers。
