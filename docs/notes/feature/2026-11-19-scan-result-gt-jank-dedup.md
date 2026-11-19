# Scan-Result-GT 2026.11.19：Jank/focus timeout 稳定指纹去重

## Decision

登记并发布外部工具包 `Scan-Result-GT@2026.11.19`（append-only），使展锐汇总去重对
Jank/`focus timeout` 使用稳定 `Package`（unievent appname）与 `CausedBy`（event_name），
不再写随机兜底串导致同问题无法归并。工具源变更在 automation-toolkit #32；本仓只登记
发布单元并切换 `STP_AGENT_UNISOC_SCAN_RESULT_PACKAGE_REF`。

## Alternatives

- 仅改 `/mnt/stp-aee/tools/Scan-Result-GT` 过渡树、不发新包：否决。现场
  `PACKAGE_REF=Scan-Result-GT/2026.09.23` 优先生效，改过渡树对已解析包缓存无效。
- 平台侧按 (ExpClass, Package) 自行归并 Jank：否决。ADR-0062 规定问题归并规则只由
  扫描工具维护。

## Verification

- automation-toolkit #32 已合入；`pytest` 49 passed；PlanRun 1370 真实 Jank 回放
  40→1（`DeviceCount=4`）。
- `package_tool_asset.py` 登记 sha
  `3384e9e6d47f9e2d6a379014e298dbc0b1037e8a0361739bb9d5477c5cddbf19`，站点包
  `/mnt/stp-aee/packages/Scan-Result-GT/2026.11.19.tar.gz` 同字节。
- 合入后需：站点 env `STP_AGENT_UNISOC_SCAN_RESULT_PACKAGE_REF=Scan-Result-GT/2026.11.19`
  + Agent fleet `--force` 热更新；存量问题包重跑 `scan_result.py -d`。

## Revisit

若第一阶段 `unievent_info.json` 长期不带 `event_name`，依赖 tar 内 `exp_main` 补齐；
可评估让 Monkey-Log-Scan-GT 在落盘时写入 B 类元数据，减少二次解包。
