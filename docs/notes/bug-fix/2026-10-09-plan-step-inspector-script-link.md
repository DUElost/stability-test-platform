# Plan 编辑器「在脚本库中编辑参数」死链：/scripts → /script-management

Status: implemented
Class: bug-fix

## Decision

`PlanStepInspector` 底部链接原指向 `/scripts?name=<脚本>`，而路由只注册了
`/script-management`（`frontend/src/router/index.tsx`），`/scripts` 落入 `*` → 404 页。
该链接自 `2a86d339`（Plan 编排三栏重构）起即失效。原测试把错误 URL 当作期望值钉住，
所以一直是绿的。

修正为 `/script-management?name=<脚本>&version=<版本>`（步骤未选版本时省略 `version`）。
这与快照抽屉的深链同形（#3350，见
[`2026-09-26-planrun-script-identity-3350.md`](../feature/2026-09-26-planrun-script-identity-3350.md)）：
脚本库页用 `name` 预填搜索；`name` 与 `version` 同时存在时，展开该版本的参数详情。

涉及文件：`frontend/src/components/pipeline/PlanStepInspector.tsx`（链接）、
`PlanStepInspector.test.tsx`（期望值改正，并新增「无版本只带名」用例）。

来源：UI 人类可达性审查（2026-10-09）C5 项。

## Alternatives

- **给 `/scripts` 加重定向路由**：能兼容旧书签，但这条 URL 从未对外暴露为正式入口；
  加别名会多出第二个权威路径（#2420 批评过的形态）。不采纳。
- **抽一个共享的 `scriptLibraryHref()` 给两处深链共用**：能从结构上防止同类手写错误，
  但要连带改快照抽屉，超出本单最小范围。若再出现第三处手写的脚本库链接，再收敛。
- **顺手改链接文案**：「编辑参数」对非 admin 不准确（脚本版本的 default_params 不可原地改，
  步骤参数在本面板改）。属于文案口径决策，本单不动，留给参数可理解性的后续工作。

## Verification

- 先改测试，在旧实现上运行：两条链接断言均失败，实际值为 `/scripts?name=install_apk`；
  改实现后 `npx vitest run src/components/pipeline/PlanStepInspector.test.tsx` 共 43 条全绿。
- `scripts/run_gates.py check:quick`：16 个门禁全绿（含 eslint、tsc）。

## Revisit

出现第三处手写的应用内路由字面量，或再次出现指向未注册路由的链接时，考虑加一个
「`<Link to>` 字面量必须命中已注册路由」的静态守卫。
