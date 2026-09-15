# 刷机前置归位到 provisioning 链：dialout + 0666 规则 + 依赖包（#2133）

Status: implemented
Class: feature

## Decision

`flash_preflight v1.0.2` 起运行期只检不修（见
[消费方 Note](./2026-09-15-flash-consumer-narrow-face-2133.md)、wrapper 窄面见
[primitives Note](./2026-09-15-adr0037-d5-flash-primitives-2133.md)），因此
**provisioning 链必须保证**三件事——本 PR 归位：

1. **dialout 成员**（`install_agent.sh` §1.2）：`usermod -aG dialout "$USER"`
   幂等执行；无 dialout 组则跳过（此时刷机依赖 0666 规则）。
2. **MTK ttyACM 0666 固定规则**（`install_agent.sh` §4c + `update_agent.yml`）：
   写 `/etc/udev/rules.d/98-ttyacm-mtk.rules`（内容与 wrapper
   `ensure-udev-rule`、`flash_preflight._UDEV_RULE_LINE` 逐字同源）+ reload。
3. **Qt/X 运行库五件**（同上两处）：`libice6 / libsm6 / libxrender1 /
   libfontconfig1 / libglib2.0-0`（含 Debian 13 `t64` 兜底），与
   `flash_preflight._DEFAULT_PACKAGES` 由 `tests/test_flash_provisioning_prereqs_2133.py`
   锁定同源。

**归位时发现的事实修正**：此前 install 链只部署 `99-ttyacms.rules`
（`ATTRS{idVendor}=="0e8d", ENV{ID_MM_DEVICE_IGNORE}="1"`，ModemManager
ignore，来自 **gitignore 的** `backend/agent/resources/`，非仓库工件）；
**0666 规则一直是旧版 preflight 的 `sudo` 修复运行期写的**（两台刷机主机
`98-ttyacm-mtk.rules` 时间戳 08-27/09-11 实证）。所以「udev 已有」在 #2133
原文里是错的，本 PR 补齐。

**`update_agent.yml` 的 provisioning 段是 opt-in**（`agent_ensure_flash_prereqs`
默认 `false`）：常规更新不触碰系统包面；刷机主机用
`-e agent_ensure_flash_prereqs=true` 收敛。该段：

- 每个任务都受同一开关门控；
- **位于升级门禁释放之后**——本段失败不得让门禁悬挂（#1249 的教训，本会话
  曾实测悬挂一次）；缺失项本来就由 `flash_preflight` 如实报出，所以失败
  设计为 `failed_when: false` + 结果 debug 汇总，不隐藏但也不阻断更新；
- 结果汇总打印（dialout_changed / udev_rule_changed / packages rc 与输出）。

`group_vars/linux_hosts.yml` 新增 `agent_ensure_flash_prereqs`（默认 false）
与 `agent_flash_prereq_packages`（包集合单一来源）。

## Alternatives

- **单独建 provisioning playbook 而不是 update 段**：否决——#2133 明确
  install/update 链归位；update 是既有主机唯一的收敛通道（热更新不碰系统面），
  另起 playbook 会让「更新完还缺前置」成为常态。
- **update 段默认开启**：否决——48 台 fleet 的常规更新不应联网装包/改 udev；
  默认关 + 显式开关 = 变更面可控（与 ADR-0037 D4「不动 Ansible 密码 become」
  的克制一致）。
- **install 段按 flashtool 目录存在与否门控**：否决——资源由 gitignore 目录
  布放，投放顺序不确定；改为无条件写规则/装包，用
  `AGENT_SKIP_FLASH_PREREQ_PKGS=1` 供离线/精简装机跳过。
- **改写资源里的 `99-ttyacms.rules` 直接加 0666**：否决——该文件不在仓库
  （`/backend/agent/resources/` 被 gitignore），改动不可复现；仓库拥有的
  固定规则文件才是可版本化的那个。
- **provisioning 段失败硬中断**：否决——见上（门禁悬挂 + 与「更新已成功」
  的语义混淆）；preflight 侧已有一手失败信号。

## Verification

- `pytest tests/test_flash_provisioning_prereqs_2133.py -q` → 7 passed
  （dialout/规则/包集合/门控完备性/门禁释放后的位置/组变量同源）；
- `bash -n backend/agent/install_agent.sh` + 两个 YAML `yaml.safe_load` 通过；
- 相关存量测试同跑：`test_install_agent_artifacts` /
  `test_install_agent_noninteractive` / `test_agent_priv_boundary` /
  `test_ansible_digest_contract` / `test_ansible_digest_bookkeeping` /
  `test_shell_line_endings` → 63 passed；
- PR 内 `check:pr` 全量门禁。

## Revisit

- canary（#2133）：在两台刷机主机跑
  `update_agent.yml -l <hosts> -e agent_ensure_flash_prereqs=true`，随后核对
  `dialout` / `98-ttyacm-mtk.rules` / 五包在位，再跑真机刷机；
- I4 容器验收：install 链在容器内执行时 `dialout` 组可能不存在（已跳过）、
  `udevadm` 可能缺失（规则写入失败会 warn）——若验收要求容器内全绿，再评估
  是否引入更宽松的分支；
- 若后续有多站点安装工具（P1）统一 provisioning：把本段并入安装编排，
  本处开关退化为兼容入口（保持默认关）；
- 参数/包集合需要变化时：包集合改 `group_vars` 并同步 preflight 常量
  （有门禁锁），不要就地改已发布脚本版本。
