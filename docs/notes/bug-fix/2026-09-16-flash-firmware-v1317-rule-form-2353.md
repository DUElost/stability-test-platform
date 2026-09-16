# flash_firmware v1.3.17：ttyACM 写入路径判据识别 0660 形态（#2353 修法 3）

Status: implemented
Class: bug-fix

## Decision

**问题**：v1.3.16 及以前用 `_udev_has_mtk_0666_rule()`——**只认 `0666` 文本**。在已升
`0660 + GROUP="dialout"` 的宿主上，它把规则形态识成"无规则"，于是环境预检给的诊断是错的：

```
no /dev/ttyACM* yet to probe; user NOT in dialout and no udev MODE=0666 rule — … ;
fix: usermod -aG dialout <user> (provisioning) or install udev rule
```

（后半句"or install udev rule"在该宿主上是错的——规则就在位；真正的修复是补组 + 重启 agent。）

**改法**：新增 `_udev_rule_form(rules_dir) -> "0660" | "0666" | None`（两种形态都识别），
环境预检的 `ttyacm-write-path` 项改为按「**规则形态 × 本进程组集合**」给结论：

| 状态 | 结论 |
|---|---|
| 节点存在且可写 | 通过（不读规则，不变） |
| 节点存在但不可写 | 判否（不变） |
| 进程组集合覆盖 dialout | 通过（不变） |
| 无 dialout + 规则 `0666` | 通过（未升级主机兼容，不变） |
| 无 dialout + 规则 `0660` | **判否** + 「补组 + 重启 agent」指引（此前也判否，但文案误导） |
| 无 dialout + 无规则 | 判否 + "usermod 或装规则"（不变） |

**行为面：verdict 不变**——该 item 在非 `strict_env_check` 下一直是**非致命** WARNING
（07-31 实测 `.87` 上 `crw-rw-rw-` 也能跑，故 #2133 起把它列入 `non_fatal`）。本版改的是
**形态识别与修复指引**。真正把「0660 + 非成员」拦下来的是 **flash_preflight v1.0.4** 的
`dialout-group` 项：它在 `flash_firmware` **之前**执行（plan 39/40 里 sort_order 0 vs 1），
且失败即停链。

## Alternatives

- **A. 不改**（并入 ADR-0039/#735 收窄轨道，我最初的建议）：本条 item 非致命、且已被 preflight
  挡在前面，残留收益小。**未选**是因为用户明确要求把该面收口，且本改动只涉及一个判据函数、
  **无 verdict 变化**，风险可控。
- **B. 把「0660 + 非成员」升级为硬失败**：否决——会把"环境不明"从 WARNING 变致命，与 #2133
  的既有分级（`non_fatal` 清单 + `strict_env_check` 开关）冲突；该状态已由 preflight 拦住，
  重复硬失败没有新增价值，反而会让单设备 host 在 provisioning 未就绪时整步失败。
- **C. 保留 `_udev_has_mtk_0666_rule` 名字只改语义**：否决——名字会骗人（它不再只找 `0666`），
  且调用点只有一处，改名成本为零。

## Verification

- **差分**（同一份 `0660` 规则 + 进程不在 dialout，`_precheck_environment` 直调）：

  | 版本 | 识别形态 | item.ok | detail |
  |---|---|---|---|
  | v1.3.16 | `None` | `False` | "no udev MODE=0666 rule … install udev rule"（误导） |
  | v1.3.17 | `"0660"` | `False` | "0660+dialout rule present but current process is NOT in dialout … fix: usermod -aG dialout then restart the agent" |

  两者非 strict 下 `hard_ok=True`（→ verdict 未变，本改动是诊断面收口）。
- **守卫**（新文件 `backend/agent/tests/test_flash_firmware_fork_guards_2353.py`，**动态解析最新
  版本目录**——#2048 教训）：`_udev_rule_form` 三取值；0660+非成员判否且指引含 dialout/restart；
  0660+成员通过；0666+非成员通过；无规则判否。**5 passed**。
- **回归**：`backend/agent/tests/` 全量（含 `test_flash_firmware_v130.py`）+ 门禁结果见下。
- **不可变门禁**：v1.3.16 未动、新增 v1.3.17。

## Revisit

- **收敛出口**：等全站规则都是 `0660` 后，可删掉 `0666` 兼容分支——与 preflight 的 legacy 形态
  同一节奏（见 #2284 Agent Note §Revisit 的抽样判据）。
- **本版不需要 pin 才安全**：verdict 未变，因此 1.3.15/1.3.16 与 1.3.17 可并存。要让宿主吃到
  正确诊断，需把 `flash_firmware` 步重指到 1.3.17（当前 pin：1.3.15 ×1 / 1.3.16 ×4）。
- **真机面**：本轮 #2284 的端到端验收（run 415 SUCCESS）里该 item 因 `.82` 进程在 dialout 走的是
  第一个分支（通过），故 0660+非成员这条路径未在真机上出现——它由 preflight 负责拦，两者分工
  已在 Decision 里写清。
