# 滑动留痕 pointer_location 改回 Settings.System 命名空间（#3578）

Status: implemented
Class: bug-fix

关联：[#3578](https://github.com/DUElost/stability-test-platform/issues/3578)。
回归自：[#3179](https://github.com/DUElost/stability-test-platform/issues/3179)（CLOSED，commit `5cfeed29`，批次 B1 / G6）。
前序 Note：[pointer_location 写入 Settings.Secure 命名空间](./2026-09-27-pointer-location-secure-namespace-3179.md)（其 Decision 已被本 Note 推翻）。

## Decision

`pointer_location` 属 **`Settings.System`**，不是 `Settings.Secure`。`_SWIPE_TRAIL_SETTINGS`
两条改回同表：`(("system", "show_touches"), ("system", "pointer_location"))`。

依据是框架读点，不是 `Settings` 常量声明。Android 15 AOSP
`services/core/java/com/android/server/input/InputSettingsObserver.java`：

```java
75:   Map.entry(Settings.System.getUriFor(Settings.System.SHOW_TOUCHES), ...),
77:   Map.entry(Settings.System.getUriFor(Settings.System.POINTER_LOCATION), ...),
182:  mNative.setShowTouches(getBoolean(Settings.System.SHOW_TOUCHES, false));
187:  mService.updatePointerLocationEnabled(getBoolean(Settings.System.POINTER_LOCATION, false));
```

同文件对真·Secure 键（`ACCESSIBILITY_LARGE_POINTER_ICON` 等）一律显式走
`Settings.Secure.getIntForUser(...)`，故命名空间无歧义。`core/java/android/provider/Settings.java`
中两个常量也都声明在 **System** 类（`PRIVATE_SETTINGS`），`Settings.Secure.POINTER_LOCATION`
在 android10/11/12/13/15 全部不存在——`#3179` 的前提据此被推翻。

其余语义（硬编码两条白名单、10s 超时、逐 serial 失败不中断整批）不变。

## Alternatives

- **两条都写 `system` + `secure` 双写**：写错表不报错，双写能让任何读点命中。否决——把一个
  确定的读点问题变成两份状态来源，后续无法判读「到底哪条在起作用」，且关闭时同样要双清。
- **改成读设备上报的当前值再决定写哪张表**：等于把命名空间知识下沉到运行时探测。否决——读点
  是编译期常量，探测只会引入新的不确定性。
- **维持现状、只在文档标注「部分机型不生效」**：否决——`#3178` 已现场证实 `secure.pointer_location=1`
  写入成功而叠加不生效，属确定性缺陷。

## Verification

- `venv/bin/python -m pytest backend/agent/tests/test_control_handler_736.py -q -k "swipe or control_handler or abort"`
  → 9 passed。
- `venv/bin/python -m pytest backend/tests/api/test_devices_bulk_swipe_trail.py backend/agent/tests/test_control_handler_736.py -q` → 通过。
- 变异自证：把 `("system", "pointer_location")` 临时改回 `("secure", "pointer_location")`，
  `test_set_device_swipe_trail_runs_both_settings` 失败；改动已恢复。
- 断言形态一并修正：原断言只钉下标（`calls[1][6] == "secure"`），现改为断言 `calls[1]` 整条
  argv，让命名空间错在任一位置都被捕获。
- 真机读点核对（2026-10-01 现场，只读）：直连宿主读回 X6728 真值
  `system show_touches=1` / `secure pointer_location=1` / `system pointer_location=null`；
  设备 `/system/framework/services.jar` 含 `InputSettingsObserver` 与两个 key 字面量，
  确认 ROM 走 AOSP Android 15 输入栈。
- 未验证：修复后的真机叠加渲染效果需随 Agent 热更新下发后由生效阶段抽验（本 PR 不部署）。
  生效阶段验证命令见 Revisit。

## Revisit

- **生效阶段必须真机抽验**：对一台 X6728 重新下发后读回 `settings get system pointer_location`
  应为 `1`、`settings get secure pointer_location` 应为空，并在亮屏状态下确认指针轨迹出现。
  `#3179` 的教训正在于此——它的反例验证只做了单测变异，未核真机读点，09-28 激活时也跳过了
  这次抽验就关单。
- **命名空间类断言的通用纪律**：断言须锚到框架**读取点**源码，不能锚到 `Settings.java` 常量声明
  或「键名看起来属于哪个表」的直觉。本单已把读点文件与行号写进代码注释与单测注释。
- #3178（OPEN，批量无上限 / fan-out 持同步 session / 前端 30s 超时）与本单成因无关，未合并。
