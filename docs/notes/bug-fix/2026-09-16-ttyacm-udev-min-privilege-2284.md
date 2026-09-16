# MTK ttyACM udev 规则最小权限：0660 + dialout（无 dialout 才 0666）（#2284）

Status: implemented
Class: bug-fix

## Decision

四个面**同批协调改**（单一面改会卡刷机，见 Alternatives A）：

| 面 | 改动 |
|---|---|
| **flash_preflight v1.0.3**（新版本，全量副本） | 判据由「一行含 `0666` 且命中 0e8d」改为**语义双形态**：`GROUP="dialout"` + `MODE="0660"` 或 `MODE="0666"` 皆算成立；常量拆为 `_UDEV_RULE_LINE`（新形态）/ `_UDEV_RULE_LINE_LEGACY`（旧形态） |
| `install_agent.sh` §4c | 装机期按本机事实二选一：有 `dialout` 组 → 0660 + GROUP（最小权限）；无该组 → 0666 **并记 warning**。规则文件里写一行「为什么是这个形态」的注释（issue 建议 3） |
| `stp_agent_priv.py` `ensure-udev-rule` | 同一选择逻辑（`_udev_rule_line()` 读本机组表）；内容仍是**两个固定形态之一**——D5 的「调用方无参数面」不变。幂等判定由整文件比对改为**按规则行比对**（`_udev_rule_present`），以免把安装链写的理由注释擦掉、并白记一次 `changed` |
| `update_agent.yml` | 一个 copy 任务拆成两个互补 `when`（有 dialout 组 / 无组），reload 任务取两者 `changed` 的并集（register 各自独立，避免被 skip 覆盖） |

**为什么必须四面包办**：`flash_preflight` 的判据是**文本判据**（要求那一行含 `0666`）。
只把写入面改成 0660 → preflight 判「规则缺失」→ 调 wrapper 重写（若 wrapper 未升级仍写
0666，则第二轮复查仍失败）→ **preflight 失败即刷机被阻断**。实测差分见 Verification。

**为什么 preflight 保留旧形态**：车队升级是渐进的。只认新形态会让「规则仍是 0666 的
未升级主机」判缺失（同样卡刷机）。双形态并存一个收敛周期，等全 fleet 升完再收窄
（出口见 Revisit）。

## Alternatives

- **A. 只改 `install_agent.sh`（issue 的措辞「只需一行」）**：否决——实测 v1.0.2 判据对
  0660 规则返回 `False`（见 Verification 差分），装完即卡刷机。
- **B. 白名单/直接拒绝非法形态**：不适用——这里要改的是**权限位**，不是输入校验。
- **C. 由 wrapper 在 preflight 失败时按需写（issue 建议 2）**：否决。与 D5「运行期不
  装包/写规则（preflight 只检不修）」的方向相反；且按需写仍是 0666，不解决权限面。
- **D. preflight v1.0.3 只认新形态**：否决（未升级主机卡刷机）。**E. 双形态但判据放宽到
  「含 0660 即可」**：否决——`0660` 而组不是 dialout（默认 `root:root`）时 Agent 用户写
  不了，必须 `dialout` 与 `0660` 同时命中才算（v1.0.3 的实现即如此，有用例钉住）。
- **F. wrapper 保持整文件幂等**：否决（改用按行判定）。安装链会写理由注释，整文件比对
  会让 wrapper 每次判「不一致」→ 重写一次、擦掉注释、白记 `changed`。

## Verification

- **差分（判据面）**：同一份 0660+dialout 规则文件，v1.0.2 `_udev_rule_ok` = **False**、
  v1.0.3 = **True**；同一份 0666 规则两者皆 **True**（兼容性保持）。
- **install 链逻辑沙箱实测**（把 §4c 片段路径重写到临时目录后执行，未触碰 `/etc`）：
  - 本机有 dialout → 写出 `GROUP="dialout", MODE="0660"` + 理由注释；
  - 模拟无 dialout（PATH 前置假 `getent`）→ 写出 `MODE="0666"`、打 warning、注释写明
    「本机无 dialout 组，退化为 0666」。
- **wrapper**：新增用例 2 条——「已含目标行时 `changed=0` 且理由注释保留」「`_udev_rule_present`
  忽略注释/空行、形态不符即 False」；既有 udev 用例改为与 `_udev_rule_line()` 比对
  （原先钉死常量，在本机无 dialout 的环境会假红）。
- **四处 parity 测试同步**（这是本次最容易漏的面）：
  - `test_flash_provisioning_prereqs_2133.py`：install 链断言**两个形态常量** + `getent`
    条件；playbook 断言两个 copy 任务的 content 集合 = 两常量、且 `when` 互补（`== 0` / `!= 0`）；
    新增 wrapper ↔ **最新** preflight 的三常量同源断言。
  - `test_agent_priv_flash_primitives.py`：同源校验由钉死 v1.0.1 改为**动态取最新版本目录**
    （#2048 教训——钉历史版本会在下次改版时假红）。
  - `test_flash_preflight_v102.py`：该文件钉的是历史版本 v1.0.2，改为与 wrapper 的
    **legacy 形态**比对，并注明「最新版本的同源校验在 prereqs 测试」。
- 测试批次：`test_flash_preflight_v103.py`（新，8 条）+ `v102` + `prereqs_2133` +
  `agent_priv_flash_primitives` + `remote_script_privilege_paths` + `backend/agent/tests/`
  → **83 passed**。
- `python tools/dev/check-script-version-immutability.py --base origin/main` → **OK**
  （v1.0.2 未动，新增 v1.0.3）；`ruff` 全绿；`check:quick` → **OK (10 gates)**。
- **未跑（验收第 2 条，标 pending）**：真机刷机端到端——「0660 + dialout 下刷机链仍可用」
  需要一台在线设备：装机后 `ls -l /dev/ttyACM*` 应为 `crw-rw---- root dialout`，再跑一次
  刷机确认 flash_preflight / flash_firmware 全绿。**本机无设备，未验证**。
- **未做**：DB 注册与版本 pin——新版本目录落地后需部署侧 `POST /scripts/scan` 注册；
  改 `plan_step` 的 preflight 版本 pin 属部署/计划侧动作，本 PR 不做。

## Revisit

- **真机验证是硬缺口（pending）**：上表那条必须在有设备的窗口补齐；在此之前建议先在
  **一台** host 上装/更新后目视节点权限与一次刷机，再全队铺开（安装链只在装机/更新时写
  规则，未升级主机不受影响）。
- **双形态是过渡态**：等 fleet 升完可把 preflight 判据收窄为只认新形态（旧形态同时从
  wrapper/installer 删掉）。判据：全 host 的 `98-ttyacm-mtk.rules` 均为 0660 形态——
  可由站点巡检/`grep MODE=` 抽样确认；收窄前不要删 legacy 分支。
- **未升级主机的残留**：增量升级只覆盖跑过 installer/playbook 的主机；其余仍是 0666
  （本单不追求一次性收敛）。
- **D5 面约束的表述已更新**：「内容都是常量」→「两个固定形态之一（由本机事实决定）」；
  ADR-0037 正文未改（该表述在 wrapper 注释与测试里），若评审认为需要入 ADR 再补。
