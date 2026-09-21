# 门控自动 xHCI rebind——门控/熔断/动作切片（#2972）

Status: implemented
Class: bug-fix

## Decision

第一切片只交可单测的恢复决策层，**不接线 heartbeat**：

1. `backend/agent/xhci_auto_rebind.py`：opt-in（`STP_XHCI_AUTO_REBIND=1`）+
   host 白名单（空=无人放行）+ 门控合取（空树连续 tick / 无 job·device /
   非维护窗）+ 防洗白熔断（boot≤2、48h≤2、失败再试 1 次）+ 运行时枚举
   `xhci_hcd` PCI id + 可注入 sysfs write。
2. `HeartbeatSettings` 增 `stp_xhci_auto_rebind` / `_hosts` 派生属性。
3. 真值表与熔断单测（mock write，不碰真机）。

## Alternatives

- **同 PR 接 heartbeat**：空树 tick 状态机与 metric 出口未定，易与门控纠缠；否决。
- **默认开 / 空白名单=全员**：违反「白名单灰度」；否决。

## Verification

- `python -m pytest backend/agent/tests/test_xhci_auto_rebind_2972.py -q`

## Revisit

heartbeat 接线：累计 `empty_tree_ticks`、结构化日志、
`stability_xhci_auto_rebind_total` metric；与 #2983 探针拓扑可互为旁证。
