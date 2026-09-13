# #704 设备 model 采集端大小写归一

Status: implemented
Class: bug-fix

## Decision

`backend/agent/device_discovery.py` 的 `discover_devices` 解析 `adb devices -l`
的 `model:` 字段时统一 `.upper()`。这是 issue 建议里的「先设备侧」路线：
#644/#675 契约（映射写入归一大写、读端归一匹配、成员行写设备事实**原值**）
以「同型号原值唯一」为前提；当同一物理型号以两种大小写并存时（一台
`Infinix_X1102D` + 一台 `infinix_x1102d`），`model_facts`（归一值→首个原值）
只保留首个原值，持有另一种大小写的设备在 `Device.model == match_value`
全等 join 上 miss——该 join 不止 map preview，还包括派发派生
（`plan_dispatcher_sync.py:624`）、suite_binding（`suite_binding.py:322`）
与设备列表归属过滤（`devices.py:324`、`projects.py:77/99/480/814`）。

归一点选在**解析处**而非心跳组装处：`discover_devices` 的 model 字段全仓只有
心跳一个消费方（`heartbeat_thread.py:191`），而 `_STATIC_DEVICE_SERIALS` 静态
路径的 `"model": "static"` 是占位哨兵值、不可误大写——解析处归一只覆盖 adb
真实上报，不触碰哨兵。存量混合大小写行由服务端心跳覆盖语义自愈
（`heartbeat.py:420` 每次 `model is not None` 即覆盖），agent 升级后下一拍
心跳即归一。

## Alternatives

- **model_facts 多值映射 + 读路径大小写不敏感 join**：issue 已裁定改动面大、
  先设备侧，不做。
- **服务端 ingest 归一（heartbeat.py 落库前 upper）**：能覆盖未升级 agent，但
  #644 的读端已归一匹配、join miss 的根源只是「原值不唯一」，在采集源头
  收口即可；服务端兜底属多余一层（生产当前无混合形态，本单本就是未来形态
  预防）。
- **心跳组装处 upper**：需额外区分 static 哨兵，且 discovery 的其他潜在
  消费方仍见原始大小写，劣于解析处单点。

## Verification

- `agent/tests/test_device_discovery.py`：既有 `test_discover_devices_success`
  断言同步为 `PIXEL_7`；新增
  `test_discover_devices_normalizes_model_case`——同型号两种大小写并存
  双设备均归一为 `INFINIX_X1102D`。
- 44 passed（test_device_discovery.py）+ 13 passed（heartbeat_thread 五件）。
- `python scripts/run_gates.py check:quick`：见 PR 内记录。

## Revisit

- 若未来出现**绕过 agent 采集**的 Device.model 写入面（数据修复导入、手工
  SQL），归一前提再次被破坏——届时再评估服务端 ingest 归一或读端不敏感
  join，不提前加层。
- 生产存量行在 agent 升级前仍按旧大小写上报，混合形态出现窗口 = 部署时间差；
  生产当前无此形态（issue 已核），不另做迁移。
