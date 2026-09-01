# PlanRun 详情：侧栏操作条位置

Status: preview
Class: bug-fix

## Decision

「导出 / 中止 / 复跑」回到 **Hero 卡片内原位**。侧栏改为：Hero（含操作条）固定在顶部、不参与滚动；下方 KPI + 执行链单独 `overflow-y-auto`。这样操作钮既在原视觉位置，也不会被下方内容滚走或盖住。

曾试过贴侧栏底栏；反馈要原位后改回上述结构。导出菜单在视口下方空间不足时仍可向上展开。

Hero 副文案（`Plan #… · name`）挪到标题行下方独占整行，不再与右侧 Running pill 争宽被截成省略号；够宽单行，过长 `break-words` 换行。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

极矮视口下若 Hero 自身高于侧栏，再考虑给 Hero 区加 `max-h` + 区内滚动，或把操作条钉在 Hero 区底。
