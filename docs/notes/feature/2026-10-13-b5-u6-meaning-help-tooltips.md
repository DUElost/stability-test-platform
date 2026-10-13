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
pointerdown 抑制 focus-open、click 只关不开；共享组件用受控 `open`，在触屏
`pointerDown` 上 `preventDefault` 后切换开合，并抑制随后合成 click 的关闭，以恢复
点按可读、再点按关闭，而不恢复常显长段落。

接入面：

- `PlanSettingsEditor`：五个数字设置与 watcher 可编辑键的释义；
- `ParameterProjectionList`：同义 glossary / 只读投影 / 执行预览清单；
- `PlanStepInspector`：步骤参数 schema `description` / 投影 `meaning`。

不重开已合入 PR #3692；Draft PR #3705 Refs #3659 与 #3653。独立复核 R1（#3705）后补触屏路径。

## Alternatives

继续常显灰字：Owner 已明确嘈杂，否决。
用原生 `title`：键盘与样式不一致，且与项目既有 Radix Tooltip 模式不符。
只改 Editor、不动投影清单与步骤 inspector：同页/同义释义面会半新半旧，否决。
改用 Popover / 新 UI 原语：项目无既有 Popover，且需求是恢复触屏可读而非换组件族；在现有
Tooltip 上受控开合即可，否决扩大面。

## Verification

- `npm --prefix frontend run type-check`：exit 0。
- 相关 vitest（`--run`，11 文件 **208 passed**，exit 0）：
  `MeaningHelpTooltip`（含触屏点按→完整释义可读→再点按关闭，并保留悬停/键盘）、
  `PlanSettingsEditor`、`ParameterProjectionList`、`PlanSettingsReadonly`、
  `PlanStepInspector`、`PlanExecutePage`、`DispatchCockpit`、`PlanStepList`、
  `PlanCanvas`、`PlanEditPage`、`PlanSnapshotDrawer`。
- 未跑后端 / 生产部署；保持 Draft。

## Revisit

- PlanCanvas 页眉仍保留一条巡检时长摘要行（非设置网格常显灰字）；若 Owner 仍觉拥挤可再收口。
- 合入不等于生产生效；不部署、不转 Ready、不 merge。
