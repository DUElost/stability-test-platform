# flash_preflight v1.0.4：可写性判据与「规则在位」解耦（#2353）

Status: implemented
Class: bug-fix

## Decision

**根因**：v1.0.3（#2284）把 `_udev_rule_ok` 从「一行含 `0666`」放宽为「`0660+dialout`
或 `0666` **在位**」，但同文件的 `dialout-group` 项仍拿它当「该用户**可写**」的证据。
两种形态**放行面不同**（`0666` 对任何本地用户放行；`0660` 只对 `GROUP="dialout"`
成员放行），于是「0660 规则 + 用户不在 dialout」被判通过——上一条链上，宿主的串口对
该用户根本不可写。

**改法**（本轮只改脚本侧，写入侧见 Revisit）：

1. 新增 `_udev_rule_form(rules_dir)` 返回在位形态（`"0660"` / `"0666"` / `None`）；
   `_udev_rule_ok()` 改为 `_udev_rule_form(...) is not None` —— **`udev-rule` 项与
   wrapper 修复流语义不变**（两形态皆算在位）。
2. `dialout-group` 项按「当前进程能不能写」判定：进程组集合含 dialout → 通过；
   否则按形态：`0666` → 通过 + warning（提示收敛形态）；`0660` → **判否**（含
   「持久成员已补齐但服务未重启」这一情形，指引＝重启 agent）；无规则 → 判否。
3. 条目名 `dialout-group` 保留（下游 `step_trace`/展示按名消费）。

**判否而非 warning 的理由**：preflight 是「刷机前」的放行门，跑刷机的就是当前进程
（`flash_tool` 是 agent 的子进程，继承同一进程组集合）；进程组集合不含 dialout 时
`0660` 节点必然写不了。让它在刷机前响铃并给出可执行指引，好过 warning 放行后在设备侧
以 EACCES/`STATUS_ERR` 失败——`flash_firmware` 自述那类症状「极难定位」。

## Alternatives

- **A. 只改文案**（把「由 udev 0666 规则放行」这句改掉、保留 `ok=True`）：否决——
  检查仍是假通过，刷机照样中途失败，只是错误信息好看一点。
- **B. 判据收窄为只认 0660**：否决——未升级主机（规则仍 0666）会被判缺失 → 调 wrapper
  重写 → 卡刷机，正是 #2284 双形态要避免的窗口。
- **C. `pending_relogin` 维持 warning**（v1.0.3 行为）：否决——该状态同样写不了串口；
  保留 warning 会让它在铺开后继续以「中途失败」的形态出现。
- **D. 同批改写入侧**（`install_agent.sh §4c` / wrapper `_udev_rule_line()` /
  `update_agent.yml` 两条 `when` 改按成员资格）：**本轮不做**——`install_agent.sh`
  有两条在窗 Execution（Python floor / #2181 中心存储）、`stp_agent_priv.py` 有开放
  PR（#2315/#2319/#2317 安装升级链批）。按执行契约避免同批文件并行修改，留给另一 PR
  （#2353 修法 2）。缺口已记入 Revisit（本版已让该状态响铃，不阻塞）。
- **E. 顺手改 `flash_firmware` 的 0666 文本判据**：未做——脚本版本 SOP 独立（#2285
  先例：脚本版本单独 PR），且它只影响 warning 级别，另立。

## Verification

- **判据层**：`_udev_rule_form` 对「0660」「0660+理由注释」「0666」「空目录」「不存在的
  目录」分别返回 `0660 / 0660 / 0666 / None / None`；`_udev_rule_ok` 仍两形态皆真。
- **item 级**（新用例 `backend/agent/tests/test_flash_preflight_v104.py`）：0660 + 非成员
  → 判否且 detail 不再出现「0666」；0660 + 持久成员但服务未重启 → 判否 + 重启指引；
  0660 + 进程在组 → 通过；0666 + 非成员 → 通过且进 `warnings`；无规则 → 判否；
  缺规则经 wrapper 修复后按新形态复判（`udev-rule.fixed=True`，`dialout-group` 仍判否）。
- **分叉守卫**（新用例 `backend/agent/tests/test_flash_preflight_fork_guards_2353.py`）：
  **动态解析最新版本目录**并在 item 级钉住三条语义（#2048 教训——钉历史版本会让新版本
  无人覆盖；v1.0.3 的这条缺陷正是如此逃逸的）。
- **差分实证**（同一 hermetic 场景：0660 规则 + 进程不在 dialout，`fix=false`）：
  v1.0.3 → **整步 `success=True`**、`dialout-group ok=True`，文案「ttyACM 访问由 udev
  0666 规则放行」；v1.0.4 → `success=False`、`dialout-group ok=False`，detail
  「当前进程组集合不含 dialout，而 0660 规则只对 dialout 成员放行——ttyACM 不可写；
  provisioning 补组后重启 agent」。
- **回归面**：`test_flash_preflight_v104.py` + `test_flash_preflight_fork_guards_2353.py`
  + `test_flash_preflight_v102/v103` + `tests/test_flash_provisioning_prereqs_2133.py`
  （wrapper ↔ **最新**版本三常量同源，动态解析最新＝v1.0.4）+ `tests/test_agent_priv_flash_primitives.py`
  → **90 passed**。
- **不可变门禁**：`python tools/dev/check-script-version-immutability.py --base origin/main`
  → OK（v1.0.3 未动、新增 v1.0.4）。
- **`ruff` + `python scripts/run_gates.py check:quick`** → OK (10 gates)。

## Revisit

- **写入侧依据（#2353 修法 2）仍是缺口**：`install_agent.sh §4c`、wrapper
  `_udev_rule_line()`、`update_agent.yml` 两条 copy 的 `when` 都按「dialout 组**是否存在**」
  二选一，而非「agent 用户**是否是成员**」；playbook 的加组任务带 `failed_when: false`
  （失败被静默吞掉）。补齐后「0660 + 非成员」这一状态不再产生；本轮已让它响铃。
- **`pending_relogin` 由 warning 变判否**：铺开后，成员资格刚补齐而服务未重启的主机会
  开始失败并提示重启——这是有意为之（该状态确实写不了串口）；如需灰度，先跑一遍
  `update_agent.yml -e agent_ensure_flash_prereqs=true` 并重启 agent。
- **`flash_firmware` 第三判据面**：v1.3.16 的 `_udev_has_mtk_0666_rule` 仍只认 `0666`
  文本（对 0660 返回 False，仅 warning），收敛需发新版本。
- **与 #2284 真机验收的关系**：本版只改判据，不改变「0660+dialout 下刷机链端到端可用」
  的取证方式（单台装机/更新 → 规则两行 + 设备节点非 0666 + agent 进程 `Groups` 含
  dialout → 跑一次真链路刷机）。该验收仍 pending。
