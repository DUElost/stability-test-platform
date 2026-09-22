# L4 paging：`StabilityHostAdbInterfacesMissing`（#3046 Revisit）

Status: implemented
Class: bug-fix

## Decision

给已落地的 reason `adb_interfaces_missing`（#3049）补 Prometheus 告警：

- 规则名：`StabilityHostAdbInterfacesMissing`
- 表达式：`stability_host_health_reason{reason="adb_interfaces_missing"} >= 1`
- `for: 2m`（owner 09-21：连续 ≥2–3 拍即可；开关机链压测未见瞬时 L4）
- severity：`warning`（设备侧配置态，不是主机 USB 死亡）
- **不合物取** `stability_host_device_adb_state`：`usb > 0` 已在 reason 内
- **不做恢复自动化**：报单人 09-22 撤回方向③；注解钉死 triage §1 L4 + 禁 adb/xHCI 自愈

## Alternatives

- **`for: 15m` 对齐 UsbBlind**：过长——L4 在取样窗内稳定，且失明代价是数周静默；
  2m 已过抖。
- **critical**：会与主机失明（UsbBlind）同级抢注意力；L4 处置是设备侧人工，warning 够。
- **agent 侧连续拍状态机再上报 reason**：告警 `for:` 已覆盖去抖；再加状态机是双份。

## Verification

- `python -m pytest tests/test_prometheus_alerts_contract.py tests/test_host_health_reason_surface.py -q`
- `promtool test rules deploy/prometheus/alerts-stability-platform.test.yml`（有二进制时）

## Revisit

- 中心侧规则文件仍是人工副本（`docs/operations/README.md` §告警规则）——合并 ≠ 生效。
- 若生产出现瞬时 L4 噪声，再抬 `for:` 或回到 agent 侧连续拍。
