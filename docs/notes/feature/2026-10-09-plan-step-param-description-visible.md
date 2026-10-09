# Plan 编辑器参数说明常显：param_schema 的 description 显示在字段下方

Status: implemented
Class: feature

## Decision

Plan 编辑器的步骤检查器（`frontend/src/components/pipeline/PlanStepInspector.tsx`）原来只把
`param_schema.<key>.description` 当作**字符串输入框**的 placeholder。这带来两个问题：

- 字段一旦有值（通常来自脚本 `default_params`），说明就被遮住；
- 数字、枚举、布尔字段从不显示说明。

结果是已经写好的参数含义，人在 UI 上基本看不到。例如 flash_firmware、oobe_skip、
flash_preflight、fill_storage、connect_wifi 的种子 param_schema 都带中文 label 与 description。

改为：每个有说明的参数，在参数行下方常显说明（`ParamFieldShell`），并用 `aria-describedby`
把说明关联到对应控件。没有说明的字段不渲染空段落。字符串输入框的 placeholder 改为与数字字段
同口径，取 schema 默认值，不再与常显说明重复。

范围只限编辑器检查器的参数表单。执行页步骤列表、运行快照抽屉的参数展示，以及
「参数含义的事实源放在哪」，归后续 ADR（参数含义与生效值的单一事实源）与其批次处理。

来源：UI 人类可达性审查（2026-10-09）§8 A7；owner 同意「参数说明直接显示在字段下方」。

## Alternatives

- **悬停提示（title / tooltip）**：省空间，但触屏与键盘不可达，而且仍要用户「知道去悬停」。
  这正是 placeholder 方案失败的同一原因。不采纳。
- **在本单补齐缺说明的脚本族**：已发布版本的 param_schema 不可原地改（种子迁移对被引用版本直接失败），
  补说明要发新版本、再重指 Plan。说明的事实源先由 ADR 定，本单不碰内容。

## Verification

- 先改测试，在旧实现上运行：3 条新断言失败（说明不可见、无 aria-describedby）；
  改实现后 `npx vitest run src/components/pipeline/` 共 86 条全绿。
- 布局目视：用真实种子形态（flash_firmware v1.3.10 的 7 个字段，含长说明）渲染成静态 HTML，
  套构建产物 CSS，在 320px 检查器宽度下用 headless chromium 截图。说明在字段下方正常换行，无溢出、无截断。
- `scripts/run_gates.py check:quick` 全绿。

## Revisit

参数含义事实源的 ADR 落地后，说明改由「生效计划视图」统一提供。届时本组件改读该投影，
param_schema 里的 description 降为兜底。
