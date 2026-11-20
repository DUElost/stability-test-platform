# Scan-Result-GT / Monkey-Log-Scan-GT-SPRD 2026.11.20：表格 Version 用 ro.internal.build.version

Status: implemented
Class: feature

## Decision

登记并发布外部工具包 `Scan-Result-GT@2026.11.20` 与
`Monkey-Log-Scan-GT-SPRD@2026.11.20`（append-only），承接
[automation-toolkit#34](https://github.com/DUElost/automation-toolkit/pull/34)：
第一阶段把 `ro.internal.build.version` 写入设备级 `rom_ram.json`；第二阶段表格
`Version` / Detail「手机版本」优先读该字段，目录名仍保留 `ro.build.display.id`。
STP 平台 UI 版本显示口径不变（仍为 `device.build_display_id` ← display.id）。

## Alternatives

- 只改 `/mnt/stp-aee/tools/` 过渡树、不发新包：否决。`PACKAGE_REF` 优先生效，
  过渡树对已解析包缓存无效。
- 改 STP 心跳/UI 为 `ro.internal.build.version`：否决。Owner 裁决保持现状；
  且 `sys_mssi_*` 上该 prop 为空，荣耀机 display/internal 形制均不同。
- 仅发 Scan-Result-GT、不发 Monkey 第一阶段：否决。无 `rom_ram.json` 新字段时
  第二阶段只能回退目录名，新扫描无法产出短版本名。

## Verification

- automation-toolkit #34 已合入；Scan-Result-GT `pytest` 50 passed。
- `package_tool_asset.py` 登记：
  - Scan-Result-GT sha `a74276c840db3bd166e1b0212f904af3cfe359a86acda44535e77e42076034b9`
  - Monkey-Log-Scan-GT-SPRD sha `d089e13caeeb2e8be3c823c05a9b8d6f271d91057395944006e6fe649590d77a`
- 站点包已落 `/mnt/stp-aee/packages/{Scan-Result-GT,Monkey-Log-Scan-GT-SPRD}/2026.11.20.tar.gz`；
  过渡树 `/mnt/stp-aee/tools/` 已同步。
- 合入后需切换站点 env：
  `STP_AGENT_UNISOC_SCAN_RESULT_PACKAGE_REF=Scan-Result-GT/2026.11.20`，
  以及对应 Monkey-Log-Scan 包引用键到 `Monkey-Log-Scan-GT-SPRD/2026.11.20`，
  再 Agent fleet `--force` 热更新。

## Revisit

若需存量问题包根补 `internal_build_version` 后重出表，可离线写 `rom_ram.json`
或等设备下次第一阶段扫描回填；勿把 STP UI 版本口径与表格 Version 绑死。
