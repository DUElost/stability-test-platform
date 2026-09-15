# 资源层提权回退修复：wrapper 模式不再走裸 sudo（#2024）

Status: implemented
Class: bug-fix

## Decision

资源层的「能力协商回退」把两类主机混进了同一个 legacy 臂：
(a) wrapper 在场但版本旧（sudoers 已按 ADR-0037 收窄：仅 wrapper + 固定 systemctl）；
(b) #1250 前的宽 sudoers 存量机。对 (a) 执行 `sudo rsync` / `sudo tee` 必被拒
（SSH `exec_command` 无 tty → sudo 要密码），在 `set -e` 下**静默中止整段脚本**：
code 层已同步/已重启/已写 digest 却记 ok=False、无任何指引、每轮重发 ~130MB resources 载荷。

修法与 code 层 `write-digest` 缺失时的处理**同语义**（`host_updater.py:226-232` 先例）：

1. **显式失败 + 可执行指引**：`apply-resources` 探针失败即
   `ERROR: stp-agent-priv lacks apply-resources (outdated wrapper); run tools/ansible/playbooks/update_agent.yml on this host, then retry` + `exit 1`；
2. **消除混臂**：legacy `sudo rsync` / `sudo tee` 只可能在 `USE_PRIV_WRAPPER=0`（宽 sudoers
   存量机）可达（`USE_RES_WRAPPER=0` 中间态已移除）；
3. **哨兵入审计**：`STP_RESOURCES_PRIV_FALLBACK` 解析进 result（`resources_priv_fallback`）
   并写入 `hot_update_result` 审计 details——此前该哨兵被 echo 但无人解析，审计看不出
   失败在资源层；
4. 同层 digest 写入臂的「旧 wrapper 缺 `--kind`」WARN 补同样指引（该组合理论不可达，
   仅补齐口径）。

## Alternatives

- **保留静默降级**（限定 `USE_PRIV_WRAPPER=0` 后 skip 资源层）：不失败，但 resources
  永不收敛、门禁持续 drift、每轮重发 130MB，且与 code 层语义相反 → 否决；
- **放宽 sudoers**（授予 rsync/tee）：违反 ADR-0037 提权边界与 #1250 修复面 → 否决；
- **在热更新里更新 wrapper**：wrapper 在安装目录之外、只由 Ansible 管理（ADR-0037）→
  不可行；正解就是指引 operator 跑 `update_agent.yml`。

## Verification

- 新增 `tests/test_remote_script_privilege_paths.py`（根 tests，PR 路径执行）→ **3 passed**：
  用 **PATH shim（stub `sudo`）沙箱真跑 `bash -e <远端脚本>`**：
  ① 旧 wrapper + 窄 sudo → **退出非 0、输出含哨兵与可执行指引、且 sudo 日志中零裸调用**；
  ② 无 wrapper + 宽 sudo → **legacy 路径仍可用**（退出 0、日志含 legacy rsync、打出 `STP_RESOURCES_APPLIED=1`）；
  ③ 静态：指引字符串在场、混合分支（`USE_RES_WRAPPER=0`）已移除。
- **反向验证**：把生成脚本的资源层段文本还原为修复前形态，跑同一沙箱 →
  `sudo rsync`（未授权）被发起、`exit 1`、**无 `update_agent.yml` 指引** → 复现 #2024 ✓；
  修复后同场景零裸 sudo + 显式指引。
- 邻域回归：`test_host_updater` + `test_precheck_sync` + `test_agent_version_info` → **55 passed**；
  四条根契约测试（wrapper 接线/保护/Ansible 簿记/本单）→ **16 passed**；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**。

## Revisit

- 同类「新增 wrapper 子命令 → 旧 wrapper 缺能力」的协商：一律按本 Note 的
  **fail-fast + 可执行指引**模式（不静默降级、不碰 legacy sudo 面），并让哨兵进审计；
- 受影响面 = wrapper 未更新的主机：14 台纳管主机已于 2026-09-15 全部铺到最新 wrapper
  （14/14 `selftest` OK）→ 当前生产无受影响主机；存量 34 台非纳管主机走 `USE_PRIV_WRAPPER=0`
  的 legacy 路径（宽 sudoers）不受影响；
- 若后续 wrapper 覆盖扩到全部主机，本单的指引文案与 ADR-0037「wrapper 只由 Ansible 更新」
  需保持同步。
