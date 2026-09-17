# 步骤墙钟输入框清空 = 回到「未配置」（#2513，补 #2454 的另一半）

Status: implemented
Class: bug-fix

## Decision

`PlanStepInspector` 的墙钟输入原先 `raw === ''` 时只 `setDraft('')` 就 return——
**step 没被改动**，失焦 `setDraft(null)` 后输入框弹回旧值：用户看得见「删掉了」，保存后
仍是旧秒数。「未配置」这个契约态于是在 UI 上**不可达**。

改为清空即提交未配置：

```tsx
if (raw === '') {
  onUpdateStep({ ...step, timeout_seconds: undefined });
  return;
}
```

**为什么现在才能这么做**（两条前提都已在前序单里成立）：

1. **#2382**：组装侧 `apply_step_timing_fields` 对 `None` **省略键**——省略键才是「未配置」，
   Agent 据此回落 `STP_STEP_WALL_CLOCK_SECONDS` → 300s。此前 `null` 会被 schema 拒（422），
   所以旧代码「清空不落库」在当时是**对的**（测试里那句「0 与 null 都存不进后端」即此意）；
2. **#2454**：读回侧不再把 `null` 折成 30，保存也仍发 null——所以「未配置」存得进、
   读得回、不会在一次保存后被钉死。

两者合起来才让 UI 的「清空」有意义；本单是这条链的最后一段。

**不放开 `0`（不限）**：契约要求 `timeout_seconds=0` 必须与 `stall_seconds ≥ 1` 配对，
而本 Inspector 没有停滞钟输入框——放开会造出必然 422 的组合（`Math.max(1, n)` 与
`min={1}` 保持不动）。这条限制写进了 issue 的验收，未在本单扩大。

## Alternatives

- **A. 保持现状（清空不落库）**：否决。与 #2454 的读回修复配套后，「未配置」成为唯一
  存得进却**产不出**的状态——UI 达得到的数据形态应当 UI 也能表达。
- **B. 清空时直接删除该 step 的 timeout 键（`delete`）**：等价但更绕。`undefined` 经
  `JSON.stringify`（脏检查）/ `?? null`（buildStepsForApi）两条路都与「无键」同形，
  与读回侧的 `...(s.timeout_seconds != null ? {...} : {})` 正好对称。
- **C. 顺手放开 `0`**：不做，理由见上（缺少停滞钟输入框）。
- **D. 在 Inspector 里加「重置为默认」按钮**：不做。清空是最自然的表达，多一个按钮等于
  给同一语义两处入口。

## Verification

- **红绿差分**：两条改写的用例在基线实现上**红**——
  `清空超时 = 回到「未配置」并提交`（`expected "vi.fn()" to be called 1 times, but got 0 times`）
  与 `清空后失焦保持空（placeholder 表示「默认」），不弹回旧值`；换回新实现全绿。
- **测试**：`PlanStepInspector.test.tsx` 42 passed（含改写 2 例 + 保留的 `-5 → 1`、
  `0 → 1`、`.5 不提交`、`清空后重新输入 600`）；`src/components/pipeline/` 3 文件 84 passed；
  **前端全量 122 files / 972 tests 全绿**。
- `npx tsc --noEmit`、`npx eslint --max-warnings 0`、`check:quick`（10 gates）通过。
- **未做**：未在浏览器实测保存往返（读回/组装两条链已各有用例：`planEditUtils.test.ts`
  的「未配置往返」与后端 `apply_step_timing_fields` 的省略键规则）。

## Revisit

- **`0`（不限）在 UI 上仍不可达**：需要 Inspector 同时提供停滞钟输入（或一个「不限」开关
  并自动补 `stall_seconds`）。属独立一单——放开前必须先有配对输入，否则是 422 生成器。
- **同一 Inspector 的其它数值输入**：本单只动墙钟。`retry` 等字段是否有同类「清空是空操作」
  问题未全量清点；形态与判据可复用本单的用例。
- **契约前提的记录方式**：本单的改动依赖 #2382/#2454 两个前序修复。若将来有人回退其中任一
  （例如让组装侧重新写 `None`），本单的行为会立刻变成「保存被 422 拒」——两条链的用例
  分别在 `planEditUtils.test.ts` 与后端 `pipeline_schema` 相关测试里，回退时会同时红。
