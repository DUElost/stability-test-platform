# udev 规则形态判据改「成员资格」：install / wrapper / playbook 三面同批（#2353 修法 2）

Status: implemented
Class: bug-fix

## Decision

**根因**：#2284 把 ttyACM 规则改成 `0660 + GROUP="dialout"` 时，**写入侧的判据是
「dialout 组是否存在」**，而 `0660` 的实际放行面是「**该用户是否属于该组**」。两者在
「组在、用户不在」时分离——此时写下的 0660 规则对 Agent 用户等同于不可写，刷机在设备侧
以 EACCES/`STATUS_ERR` 失败（v1.0.4 的 `dialout-group` 项现在会拦这个状态，但产生面还在）。

**改法**：把三个写入面（+ 一个检查面）的判据统一为 **「Agent 用户是否已持久属于 dialout」**：

| 面 | 改动 |
|---|---|
| `install_agent.sh §4c` | 条件 `getent group dialout` → `id -nG "$USER" \| grep -qx dialout`；两个退化理由注释与 warning 文案改写成「用户不属该组」而不是「本机无该组」 |
| `install_agent.sh §1.2` | 两条警告文案改指 §4c（原文案写「刷机将依赖 udev 0666 规则」，而 §4c 当时按组存在写 0660——文案与行为不一致，现在一致了） |
| `stp_agent_priv.py` `_udev_rule_line()` | 新增 `_invoking_user()`（读 sudo 注入的 `SUDO_UID`/`SUDO_USER`）与 `_user_in_dialout(user)`；**调用者**是成员才写 0660。取不到调用者（root 直调，非 Agent 路径）→ 退回 #2284 的「看组是否存在」，不扩大改动面。模块与 `ensure-udev-rule` 的文档串同步 |
| `update_agent.yml` | 新增 `Check agent user is a dialout member`（`id -nG {{ agent_user }} \| grep -qx dialout`，读加组后的真实成员表），两个 copy 的 `when` 由 `agent_flash_dialout_group.rc` 换成 `agent_flash_dialout_member.rc`；汇总 debug 补 `dialout_member_rc` 与 `dialout_usermod_failed` |

**为什么退化仍然是 0666 而不是「不写规则/硬失败」**：usermod 失败或本机无该组时，
写 0660 就等于给该用户一个不可写的节点（正是要避免的状态）；不写规则则连 0666 都没有、
刷机直接不可用；写 0666 保住刷机能力并在安装日志/巡检里留下可见的权限退化信号——与
#2284 既有的「无组才 0666」退化语义一致，只是判据收紧到成员资格。

**顺带修掉一个既有 bug**：playbook 汇总任务引用的是 `agent_flash_udev_rule.changed`，而该
register 从 #2284 拆成两个 copy 任务起就不存在了（`| default('skipped')` 恒为 skipped，
即"规则是否变更"永远不可见）。现改为两个 register 的并集。

## Alternatives

- **A. 只保留 v1.0.4 的判据侧修复**（本单修法 1，已合）：否决作为终态——产生面（三处
  写入）仍会造出「0660 + 非成员」；检查侧只是把它提前响铃。
- **B. 写入侧判据改「组成员表里有没有任意用户」**：否决，等价于没改——要判的是**谁**来写串口。
- **C. wrapper 用 root 调用者判定**：否决——真正写 ttyACM 的是 Agent 用户；`ensure-udev-rule`
  的唯一调用路径是 preflight 经 `sudo -n`，`SUDO_UID` 必然存在。
- **D. 去掉 playbook 加组任务的 `failed_when: false`**：否决——该段刻意放在升级门禁释放
  之后（#1249 教训），硬失败会让门禁悬挂；改为在汇总里显式打印 `dialout_usermod_failed` 与
  `dialout_member_rc`，让失败可见而不阻塞。
- **E. 同批发 `flash_firmware` 新版本认 0660 形态**（#2353 修法 3）：另议——v1.0.4 生效后
  「0660 + 非成员」会被 preflight 拦在 `flash_firmware` **之前**，该判据面的残留收益已几乎
  归零，而代价是 1971 行全量副本（ADR-0039 收窄轨道）。

## Verification

- **判据沙箱实测**（抽 §4c 判据块到 `/tmp/pf-4c/`，PATH 前置假 `id`，未触碰 `/etc`）：
  成员 → 写 `0660` 且理由注释写「Agent 用户 … 属该组可写」；**非成员（组存在）→ 写
  `0666`** 且理由写「不属 dialout（无该组或加组未生效）」；本机真实取值同样落 0666 分支。
- **断言更新（防回退）**：`tests/test_flash_provisioning_prereqs_2133.py`
  - installer：§4c 块内必须含 `id -nG "$USER" … grep -qx dialout`，且**不得**出现
    `getent group dialout`（形态选择再看组存在即红）；
  - playbook：两个 copy 的 `when` 必须引用 `agent_flash_dialout_member`、**不得**再引用
    `agent_flash_dialout_group`，且必须有显式成员检查任务（`id -nG {{ agent_user }}`）；
  - wrapper：monkeypatch 成员资格 → `_udev_rule_line()` 随之切形态；两个形态常量与
    **最新** preflight 版本逐字同源（不变）。
- **语法/结构**：`bash -n backend/agent/install_agent.sh` OK；
  `ansible-playbook --syntax-check tools/ansible/playbooks/update_agent.yml` OK。
- **测试**：`test_flash_provisioning_prereqs_2133.py` + `test_agent_priv_flash_primitives.py`
  + `test_flash_preflight_v104.py` + `test_flash_preflight_fork_guards_2353.py` → 64 passed；
  `backend/agent/tests/` 全量 → 2170 passed / 23 failed，**23 条为预存环境失败**（在未改动的
  `main` 树上同名同数，例如 `test_saq_scan_pipeline.py` 两边都是 16 failed/17 passed）。
- **不可变门禁**：脚本版本目录未动（本 PR 不新增脚本版本）。

## Revisit

- **未升级主机的残留**：#2284 起的规则形态收敛仍按「跑过安装链/playbook 的主机」推进；
  本改动只影响**新写入**，已落盘的 0660 规则不会因为本 PR 自动变回 0666——那些主机若
  Agent 用户不在组里，由 v1.0.4 的 preflight 在刷机前拦下（响铃，不是静默）。
- **wrapper 的「取不到调用者」分支**：root 直调时退回 #2284 的组存在判据；若日后出现
  非 sudo 调用路径，需要重新裁决该分支（当前唯一调用者是 preflight 经 `sudo -n`）。
- **`flash_firmware` 的 0666 文本判据**（#2353 修法 3）仍未动，裁量见上（Alternative E）。
