# 主机页刷机前置归位入口（ensure_flash_prereqs）

Status: implemented
Class: feature

## Decision

刷机必要条件（dialout / MTK ttyACM udev / Qt·X 五库）落在**主机页**，不绑热更新默认路径，也不塞进每个刷机 Plan：

1. 新 playbook `tools/ansible/playbooks/ensure_flash_prereqs.yml`：与 `update_agent.yml` 的 `#2133` opt-in 段同源，**强制**执行；dialout 变更后 `stp-agent-priv restart`。
2. 控制面 API：`POST /hosts/{id}/flash-prereqs/ensure` + `GET .../status`（admin 触发，RunConsole）。
3. 主机页：行内「刷机前置」+ 批量栏按钮；复用 `useHostOperations` 并发闸门。
4. Plan 内 `flash_preflight` 仍只检不修（门禁）。

## Alternatives

- **每次热更新默认 ensure**：否决——48 台常规更新不应联网 apt/改 udev（#2133 同口径）。
- **Plan 首步装包**：否决——host 属性不是设备步骤；违反 ADR-0037 D5。
- **只扩 wrapper apt/usermod**：否决——需 ADR-0037 修订，本切片不做。

## Verification

- `pytest backend/tests/api/test_flash_prereqs_api.py tests/test_flash_provisioning_prereqs_2133.py -q`
- 主机页选机 →「刷机前置」→ RunConsole SUCCESS；再跑刷机 Plan，`flash_preflight` dialout/qt 通过。

## Revisit

- 被动心跳上报 `extra.flash` 就绪态（badge）；本切片仅动作入口。
- 与 `update_agent.yml` 段抽取为共享 tasks 文件，消除双份 YAML。
