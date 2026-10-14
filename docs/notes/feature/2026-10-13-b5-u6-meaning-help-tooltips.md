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

绑定最终 head （见 PR 正文）。证据目录（本机，不入库）：
`/tmp/pr3705-r1-verify/`；关键截图副本 `/opt/cursor/artifacts/pr3705-r1/`。

- 真实 Chromium（390×844、`isMobile`/`hasTouch`，合成安全 DTO，业务网络拦截）：
  `PlanSettingsEditor` / `ParameterProjectionList` / `PlanStepInspector` 三入口各自
  点按打开完整释义（无裁切）→ 再点按关闭 → 再点按重开；`pageerror=[]`；
  问号交互前后 values/step 不变、无业务 PUT/POST。
  桌面悬停 / 焦点打开 / Escape 关闭通过；校验短提示与投影状态芯片仍常显。
- C.5 回退：临时去掉触屏处理 → `MeaningHelpTooltip` vitest **1 failed / 3 passed，exit 1**；
  恢复后 **4 passed，exit 0**；源码已恢复。
- `npm --prefix frontend run type-check`：exit 0。
- 相关 vitest（`--run --maxWorkers=2`，11 文件 **208 passed**，exit 0）。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：**16 gates OK**，exit 0
  （无 `DATABASE_URL`；schema-at-head WARN/skip 不作部署库对齐证据）。
- 历史：旧 head `8e2f13c5` 上 jsdom 曾记 208 passed；不得替代本轮最新 head 浏览器证据。

## Revisit

- PlanCanvas 页眉仍保留一条巡检时长摘要行（非设置网格常显灰字）；若 Owner 仍觉拥挤可再收口。
- 合入不等于生产生效；不部署、不转 Ready、不 merge。
- 交原独立 Reviewer 做第 2 轮复核。
