# 提权边界退役 D 步：删除 host_updater 的 legacy 分支与哨兵（#2180）

Status: implemented
Class: bug-fix

## Decision

ADR-0037 §5 Revisit #1 的执行（v0.3）：迁移期结束，热更新的**裸 sudo 回退面**整体退役。

**前置判据达成证据（#2134 B/C 步，2026-09-15 实测）**：B 步 48/48 台
`stp-agent-priv selftest` 通过（`W=1 cap=1 usb=1 selftest=1`），抽样热更新
`priv_mode=wrapper`；C 步 48/48 台清除 `/etc/sudoers.d/android` 宽文件，终审
`no_wide=1 / sudo_true_rc=1 / wrapper_rc=0 / systemctl_rc=0`；flash 链已在无宽文件
主机验收通过（canary #405 `gating.hidden(18)`/errors={} + .82 删宽文件后真机刷机
PlanRun #407 SUCCESS、restore 18/18，#2133）。

**改动**（`backend/services/host_updater.py` 为主）：

1. **fail-closed 前置**：`_REMOTE_SCRIPT` 开头改为唯一入口探测——
   `sudo -n "$PRIV" selftest`，失败即
   `ERROR: stp-agent-priv selftest failed (missing/outdated wrapper?); run
   tools/ansible/playbooks/update_agent.yml on this host, then retry` + `exit 1`，
   **早于任何动作**；通过才打 `STP_PRIV_MODE=wrapper`。`selftest` 内含子命令契约校验
   （`_SUBCOMMAND_CONTRACT` 逐条 parse 真实 argv，#2011/#1942 形态），因此
   `write-digest` 空跑探针与资源层 `apply-resources --help` 探针一并退役——
   能力协商从「按子命令、按层」收敛为「脚本头一次」。
2. **legacy 分支删除**：`USE_PRIV_WRAPPER` 判断与全部裸 sudo 命令体
   （`rsync`/`tee`/`mkdir`/`install`/`chown`/`systemctl` 及两段 `.env` heredoc 补丁器）
   删除；各动作只余 wrapper 调用（`apply-code`/`write-version`/`install-schema`/
   `sync-env`/`fix-ownership`/`deps-marker`/`restart`/`write-digest`/
   `apply-resources`）。rsync 过滤面（mtbf exclude+protect、resources/ protect-only）
   只此一处，位于 wrapper 内。
3. **哨兵与审计字段退役**：`STP_PRIV_FALLBACK`/`STP_RESOURCES_PRIV_FALLBACK`
   删除；`_parse_priv_mode` 只剩 `wrapper`/`unknown` 两态；result 与
   `agent_version_info` 审计 details 的 `resources_priv_fallback` 字段删除。
4. **env 写入面迁移的声明收口**：`STP_ENV_SYNCED=` / `STP_ENV_PATH_MISSING=` 的
   发射方由删除的 heredoc 补丁器变为 wrapper `sync-env`（控制面解析面不变）；
   `AGENT_SECRET_B64`/`ENV_OVERRIDES_B64`/`ENV_PATH_KEYS_B64`/`INSTALL_DIR` 不再是
   控制面读取的环境变量（只作为远端脚本内 shell 变量经 argv 传递），从
   `tools/dev/env_inventory.py` 的 `_INTERNAL_ONLY` 删除并 `--write` 刷新清单。

语义变化是**有意的**：迁移期「legacy 回退」在宽 sudoers 清零后已无可用环境——回退不是
兼容而是必炸（#2024 的根因即隐形失败）。fail-closed 把「半部署 + 无因失败」变成
「零动作 + 可执行指引」，代价是 wrapper 缺失主机热更新不可用（应走 `update_agent.yml`）。

## Alternatives

- **保留 legacy 分支**：否决。48/48 宽文件已清除，任何命中该分支的主机都必然在被拒的
  `sudo rsync` 上静默中止（`set -e`），既不可用又不可诊断；保留只会让审计里继续出现
  无生产者的 `priv_mode=legacy`。
- **保留哨兵字段（离线/历史分析用）**：否决。字段无生产者即死值，审计正确性靠
  「不可能出现」保证，比保留一个永远为 false 的字段更清晰。
- **继续按子命令做 capability 探针（#1942 模式）**：否决。`selftest` 的契约校验
  覆盖面严格更大（含新增子命令、参数接线错误）且失败更早（脚本头 vs 动作前），
  逐探针只是历史形式。
- **同时删除 `_build_remote_script` 的 `user`/`group` 参数**：本单不做。两参数已不被
  远端脚本使用（wrapper 内部固定 `android:android`），但删除会波及安装器与另两个
  测试文件，超出本单边界；如清理应另立小单。

## Verification

- 沙箱真跑（PATH shim，`tests/test_remote_script_privilege_paths.py` 重写）→ **4 passed**
  （1 例参数化×2）：① 旧 wrapper（selftest 契约失败）与 ② 无 wrapper + 宽 sudo 可用
  → 均退出非 0、含指引、`STP_PRIV_MODE` 未打、sudo 日志**零裸调用**、无 legacy 哨兵；
  ③ 健康 wrapper → 退出 0、`STP_RESOURCES_APPLIED=1`、全部提权调用经 wrapper；
  ④ 静态：指引在场 + 10 个 legacy 字面量（含 `USE_PRIV_WRAPPER`/`sudo rsync`/`sudo tee`）
  全部不在脚本。
- 相关测试：4 个受影响文件（`backend/tests/services/test_host_updater.py`、
  `tests/test_remote_script_privilege_paths.py`、`tests/test_agent_priv_apply_code_protection.py`、
  `tests/test_agent_priv_parser_contract.py`）→ **50 passed**；新增跨边界哨兵测试
  （wrapper 发射 ⇄ 控制面解析）。
- 邻域回归：`backend/tests/services` → **941 passed**；`tests/` → **994 passed**；
  `tools/dev/env_inventory.py --check` → **OK（210 个读取名）**。
- `python scripts/run_gates.py check:quick` → **[OK]（10 gates）**；
  `python scripts/run_gates.py check:pr` → **[OK]（18 gates，含 pr-migrate
  一次性 postgres:16 空库迁移 + schema 比对 5 项基线零新增）**；本机 `schema-at-head`
  因不读生产库连接而 WARN 跳过（worktree 无 `.env`，不出生产凭据）。
- 文档同步：ADR-0037 v0.3（D2/D3/§4 失败模式与回滚/§5 Revisit #1 标执行）、
  `docs/adr/README.md`、`docs/DOC-MAP.md`、Ansible runbook、#1250/#1942/#2024 三篇
  历史 Note 加后续指向。

## Revisit

- 合入后抽样验证（≥3 台、含 1 台非纳管主机）：`POST /hosts/{id}/hot-update?force=true`
  → `priv_mode=wrapper`、无 WARN/legacy 痕迹；若出现 selftest 失败主机，按脚本内指引
  重跑 `update_agent.yml`（fail-closed 的预期出口）。
- 若未来出现「wrapper 缺陷导致热更新不可用」的事故：按 ADR-0037 §5 Revisit #4，
  修 wrapper 发新版本，**不回退宽 sudoers**；本单已把回退代码面删除，事故处置不依赖
  代码回滚。
- #2134 残留（异常设备 offline 时的宽文件/升级处置）不属本单判据，按原单推进。
