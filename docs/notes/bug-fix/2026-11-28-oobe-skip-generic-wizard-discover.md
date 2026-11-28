# Agent Note: `oobe_skip@1.1.4` 通用向导包探测与清场硬校验（P532AA 实证）

Status: implemented
Class: bug-fix

## Decision

修复 `backend/agent/scripts/oobe_skip/oobe_skip.py` 并登记为 `1.1.4`（`flash_unisoc.json` /
`flash_unisoc_bsl.json` 模板 pin 同步升至 `1.1.4`）：

1. **通用向导包探测（不做机型分支）**：种子名单扩到
   `com.tinno.setupwizard` / `com.tinno.factoryoobe` /
   `com.google.android.pixel.setupwizard`（主包仍为 Google SUW）；默认
   `discover_setupwizard=true`，再并入机上名称匹配 `setupwizard|factoryoobe|oobe`
   的已安装包，以及占用 HOME 的非桌面包；排除
   `com.android.managedprovisioning` 与已知 Launcher。只对 `pm path` 存在的包
   force-stop / disable。
2. **对齐工位 P532AA 关键动作且保持通用**：写入
   `settings put global setup_wizard_has_run 1`；禁用优先 `pm disable`，失败再
   `pm disable-user`。
3. **修焦点诊断截断**：`dumpsys window` 改为单参数 `adb shell` 管道 grep
   `mCurrentFocus`，避免默认只留末 300 字导致 `ui_focus` 恒空。
4. **硬校验 `verify_wizard_cleared`（默认 true）**：目标向导仍 enabled，或 HOME
   仍解析到向导包 → 失败。覆盖「flags 已是 1 但仍停 WelcomeActivity」的虚假成功。

P532AA 真机（`MSW000003J6AU00005`）对照：

- `oobe_skip@1.1.3`：`success=true`，但前台仍是
  `com.tinno.setupwizard/.ui.WelcomeActivity`，HOME 仍指向该包。
- `1.1.4`：探测到 `["com.tinno.setupwizard"]`，`pm disable` 成功，前台与 HOME 落到
  `com.android.launcher3`，`wizard_cleared.ok=true`，`success=true`（约 3s）。

## Alternatives

- **只把 `com.tinno.setupwizard` 写死进默认名单、不加探测/硬校验**：否决；能修 532，
  但换厂商向导包名或只改 settings 的机型仍会假成功。
- **按 `ro.product.model` / family 做 532 专用分支**：否决；与「合入须通用」前提冲突，
  且会把机型差异推到调用方。
- **继续只以两个 provisioning 标志为权威判据**：否决；P532AA 实证 flags=1 仍可停在
  Tinno Welcome，且 Tinno SetupWizard 注册了 HOME。

## Verification

- `scripts/run_pytest.py backend/agent/tests/test_oobe_skip_v110.py -q`：**12 passed**
  （含探测 Tinno SetupWizard、HOME 仍被占用时失败、跳过未安装种子包）。
- `scripts/run_pytest.py tests/test_pipeline_template_script_pins_2865.py -q`：**7 passed**。
- `tools/dev/check_script_packages.py`：**OK**（50 族等价；已 `--register oobe_skip 1.1.4`）。
- `tools/dev/check_tool_manifest.py --base origin/main`：**OK**（append-only）。
- 真机 P532AA：`1.1.3` 虚假成功 vs `1.1.4` 真实离开 OOBE（见 Decision）。

## Revisit

- 合入后：`--publish --packages-root /mnt/stp-aee/packages` → `POST /api/v1/scripts/scan`
  → 既有刷机 Plan 的 `oobe_skip` 步骤重指 `1.1.4`。
- `flash_firmware` 锁内 `_vbus_skip_oobe` 仍是瘦配方（仅 Google SUW force-stop + 两标志），
  未在本单对齐；若 MTK/vbus 路径出现同类「flags=1 仍停厂商向导」，再抽共享清场逻辑或
  同步加探测（脚本包之间不得互相 import，ADR-0051）。
