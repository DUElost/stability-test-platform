# B5 U6 UX follow-up：计划设置释义改为帮助图标 Tooltip

Status: implemented
Class: feature

Owner 在 B5 U6 激活后的 Plan #16 L0 反馈：设置区长释义常显灰字过于拥挤。
本变更仅前端 UX，不改后端写模型、投影 API 或参数语义。

## Decision

在术语/字段标签旁放置可聚焦的 `?` 帮助图标（`MeaningHelpTooltip`，Radix Tooltip +
`CircleHelp`），悬停、键盘聚焦或触屏点按时展示完整 `meaning`（及数字设置的回落链摘要）。
五态投影状态芯片、清空操作短提示与校验错误仍常显。

触屏：Radix 默认 TooltipTrigger 在 `pointerType=touch` 时忽略 pointermove，且
pointerdown 抑制 focus-open、click 只关不开。共享组件用受控 `open` + `openRef`，在触屏
`pointerDown` 上 `preventDefault` 后切换开合，抑制随后合成 click 的关闭，并在关闭后
`blur()` 触发器，避免 focus-open 立刻重开；浮层上的触屏点按亦可关闭。不恢复常显长段落。

接入面：

- `PlanSettingsEditor`：五个数字设置与 watcher 可编辑键的释义；
- `ParameterProjectionList`：同义 glossary / 只读投影 / 执行预览清单；
- `PlanStepInspector`：步骤参数 schema `description` / 投影 `meaning`。

不重开已合入 PR #3692；Draft PR #3705 Refs #3659 与 #3653。独立复核 R1（#3705）后补触屏路径，
并在最新 head 上完成真实 Chromium 三入口验收。

## Alternatives

继续常显灰字：Owner 已明确嘈杂，否决。
用原生 `title`：键盘与样式不一致，且与项目既有 Radix Tooltip 模式不符。
只改 Editor、不动投影清单与步骤 inspector：同页/同义释义面会半新半旧，否决。
改用 Popover / 新 UI 原语：项目无既有 Popover，且需求是恢复触屏可读而非换组件族；在现有
Tooltip 上受控开合即可，否决扩大面。
仅用 `ignoreFocusOpenRef` 吞掉 focus-open：会误伤合法重开；改为关闭后 `blur()`。

## Verification

产品修复 head `265461eec77e7da0d59ddec2e4e7ad8ffc02d61b`。权威硬顶重跑证据（入库）：
[`docs/notes/feature/evidence/pr3705-r1-e2d7b48f/`](./evidence/pr3705-r1-e2d7b48f/)
（验收 tip `e2d7b48f…` = `report.json` 的 `head`；tip 相对产品树仅 docs）。

- **硬顶**：`/sys/fs/cgroup/stp-r1-hardcap`，`memory.max=3221225472`（3 GiB，**有限值**），
  `memory.swap.max=0`；验收峰值 `memory.peak≈1.10 GiB`。见 `hardcap-limits.json`。
  （旧目录 `pr3705-r1-f796d8ba/` 记录祖先 `memory.max=max`，不作硬顶权威。）
- 真实 Chromium（硬顶内，390×844、`isMobile`/`hasTouch`，合成安全 DTO，业务网络拦截）：
  三入口各自点按打开→关闭→重开；桌面悬停/焦点/Escape；`pageerror=[]`；无业务写。
  报告：`evidence/pr3705-r1-e2d7b48f/report.json`（`head=e2d7b48f…`）。
- C.5 回退（历史，见 `pr3705-r1-f796d8ba/mutations/`）：去掉触屏处理 → vitest 1 failed；
  恢复后 4 passed。
- 硬顶内：`type-check` exit 0；vitest 11 文件 **208 passed**；`check:quick` **16 gates OK**。

## Revisit

- PlanCanvas 页眉仍保留一条巡检时长摘要行（非设置网格常显灰字）；若 Owner 仍觉拥挤可再收口。
- 合入不等于生产生效；不部署、不转 Ready、不 merge。
- 交原独立 Reviewer 做第 2 轮复核。
