# 项目编辑「改字段 + 改 key」串行化（#1708）

Status: implemented
Class: bug-fix

## Decision

`EditProjectDialog` 原先把一次保存拆成同一 tick 的两个回调：`onSubmit(payload)` 与
`onRename(newKey)`，父级各自 `mutate` 成一个独立 HTTP 请求（`PUT /projects/{旧key}` 与
`PUT /projects/{旧key}/rename`），无顺序保证。rename 先落地时 update 按旧 key 查
`_get_project_or_404` → 404：rename 的 `onSuccess` 已关窗并跳新 URL，用户刚编辑的字段
**静默丢失**（#824 前端批引入的交互回归）。

修复分两层：

1. **对话框收敛为单回调**（`EditProjectDialog.tsx`）：`onSubmit(payload, newKey?)`
   一次性回传全部意图，key 未变时 `newKey` 为 `undefined`；原 `onRename?: (newKey) => void`
   仅剩「是否显示 key 输入框」的开关语义，改为显式 `canRename?: boolean`，避免留下
   一个永不调用的回调参数；
2. **页面单 mutation 串行提交**（`ProjectDetailPage.tsx`）：`saveMutation.mutationFn`
   内 `await rename(...)` 成功后再以**新 key** `await update(...)`；key 未变则只 update。
   顺序取 rename → 新 key update（与 issue 建议一致）：重命名是最易失败的半步（key
   冲突/校验），失败时尚未写入任何字段。

**残留窗口显式处理**：rename 已生效而 update 失败时，页面若仍停在旧 URL，重试会再次
rename 旧 key（已不存在）而永久 404。故 update 失败包成 `PartialSaveError(renamedTo, cause)`，
`onError` 分支关窗、失效列表、提示「已重命名为 X，但字段保存失败」并**跳转新 key**——
不把页面留在失效 URL，也不静默。

## Alternatives

- **保持两个 mutation、仅调整调用顺序**（`await renameMutation.mutateAsync` 后再
  update）：弃——update 的 `mutationFn` 闭包持有旧 `projectKey`，要用新 key 就得绕过
  mutation 直接调 api；两个 mutation 的 `isPending`/错误/toast 分支还会交叉，状态比
  单 mutation 更难对齐；
- **先 update 再 rename**：弃——失败语义同样部分成功，但 rename 失败时字段已落库，且
  该顺序下「key 冲突」这一最可能的失败会留下「字段已改、key 未改」的静默半成品；
  rename 先行的失败模式更干净（无写入即失败）；
- **加客户端重试/回滚（update 失败时把 rename 改回去）**：弃——回滚本身可能再失败，
  且 rename 是身份变更，回滚会引入第二重语义；`PartialSaveError` 的显式提示 + 跳转
  已覆盖该窗口，符合「不静默」的要求；
- **对话框保留 `onRename` 仅作显示开关**：弃——留下永不调用的回调是误导性 API，
  改 `canRename` 布尔把「能力开关」与「回调」分开。

## Verification

- **红绿对照（页面级）**：`ProjectDetailPage.test.tsx`
  - 新增 `renames then updates with the new key, serially (#1708)`：断言 rename 先于
    update（`invocationCallOrder`）、update 收到新 key、且**从不**以旧 key 调用 update；
  - 新增 `does not update fields when rename fails (#1708)`：rename 失败时 update
    零调用、不跳转、对话框保持打开（用户可修正 key 重试）；
  - 新增 `navigates to the new key when update fails after rename (#1708)`：部分成功
    也跳新 key；
  - 既有 `admin opens prefilled edit dialog...` 追加断言：key 未改时**不**发 rename
    请求（单回调语义）；
  - **反例**：临时把 `mutationFn` 还原为旧行为（并发 + 旧 key）→ 新用例
    `2 failed | 15 passed`（断言 update 收到新 key 失败）；恢复修复后
    `17 passed`；
- 前端全量：`npx vitest run` → **758 passed**（102 files）；
- `npm run type-check` → 通过；`npx eslint src/pages/projects --max-warnings 0` → 通过；
- `python scripts/run_gates.py check:quick` → 见 PR 校验记录。

未做：浏览器内真机时序复现（404 取决于两请求到达顺序，属服务端并发行为）；修复依据
是代码路径与页面级反例，不是现场抓包（issue 置信度亦标注为中）。

## Revisit

- **后端无幂等/顺序保护**：本单只收口前端调用序。`PUT /projects/{key}` 与
  `/rename` 仍是两个端点，任何其它客户端（脚本、旧前端缓存包）并发调用同样会踩
  404；若要根治可在后端加「key 不存在的 update 按 rename 记录回退查找」或合并为
  单端点——属 API 形态变更，需独立 Requirement/ADR，不在本单扩大；
- **`PartialSaveError` 的审计可见性**：部分成功只体现在 toast；若运维需要可追溯，
  可考虑上报前端事件或让后端返回两段式结果，同样留待后续裁决；
- **测试未覆盖对话框独立用例**：`EditProjectDialog` 无独立测试文件，其单回调契约由
  页面级用例（新 key 传递、未改 key 不发 rename）间接锁定；若对话框将来被其它页面
  复用，应补组件级用例。
