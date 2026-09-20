# GPU 兼容边界：setup 增 instrument 冒烟探测，init 快速失败（#774）

Status: implemented
Class: bug-fix

## Decision

开 `gpu_setup` **v1.2.2**（ADR-0020/0029 不可变）：在 `dismiss_antutu_dialogs` 之后、
`push_device_script` / `launch_stress` 之前，默认跑一轮 `am instrument` 冒烟
（`compat_probe`，可用 `STP_GPU_COMPAT_PROBE=false` / step params 关掉）。

命中 issue 正文实证签名即 `raise`，让 init 快速失败：

- `UiAutomationService already registered`
- `NullPointerException` + `UiDevice.isScreenOn|wakeUp`
- `BaseTestCase.tearDown`（兜底，覆盖 setUp 未初始化后的 teardown NPE）

**不**把其它 JUnit FAILURES（如 antutu app start）当兼容边界——那些已有
pre_reboot/settle、dismiss、finish 侧 `junit_failed_rounds` 归因；本版只收
「APK 框架在本机不可用」这一类，避免整窗空转。

根因仍在 transsion 测试框架（平台无法 root-fix APK）。本单交付的是平台侧
**早发现 / 早退出**，不是兼容矩阵本身。force-stop 不足以根治（issue 已证
411 设备 force-stop 后仍 NPE）——探测前仍 force-stop，只为降低假阳性。

## Alternatives

- **只关 issue / 只写文档**：已有 pre_reboot、dismiss、FAILURES 解析，但仍会让
  不兼容机把整窗 GPU 轮次烧完才暴露；缺「init 快速失败」这一环。
- **探测失败即对任意非 OK 失败**：过宽——会把 antutu 弹窗/瞬时启动失败也挡在
  长循环外，和现有 dismiss/settle 分工冲突。
- **原地改 v1.2.1**：违反脚本版本不可变。
- **模板立刻钉 v1.2.2**：本单只交付新版本；生产 pin / Seed 激活留给上线收尾
  （与 #2865 同形），避免未 scan 热更新前误指。

## Verification

- `python -m pytest backend/agent/tests/test_gpu_setup_compat_probe_774.py -q`
- `python -m pytest backend/agent/tests/test_script_version_fork_guards_2048.py -k gpu_setup -q`
- `python tools/dev/check-script-version-immutability.py --base origin/main`
- `python scripts/run_gates.py check:quick`

## Revisit

- 生产启用：scan 解锁 `gpu_setup` 1.2.2 后，模板 / PlanStep 钉到 `1.2.2`（或 Seed）。
- 若 APK 方给出固件/机型兼容矩阵，平台侧可改成「矩阵门禁」替代盲探。
- 签名漂移（新崩溃栈）：只加 `_COMPAT_SIGNATURES` 条目并发新版，不原地改。
