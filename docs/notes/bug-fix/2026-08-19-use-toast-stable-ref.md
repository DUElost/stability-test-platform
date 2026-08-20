# useToast 引用稳定化，断开 SchedulesPage 无限请求环

Status: implemented
Class: bug-fix

## Decision

- `hooks/useToast.ts` 返回值包 `useMemo(() => ({...}), [])`：跨渲染引用稳定。
  消费方（24 处引用）普遍把 `toast` 放进 `useCallback`/`useEffect` 依赖数组，
  引用不稳定会让依赖链上的 effect 每次渲染重跑。
- `error` 的 `duration: Infinity` → `10_000`：错误 toast 10s 自动消失。
- 回归兜底：`components/ui/toast.test.tsx` 断言多次渲染拿到同一引用——
  该环在代码评审里看不出来（对象字面量完全合法），只有跑起来现形。

## Alternatives

- **只修 SchedulesPage 的 effect 依赖**（否决）：环在该页，但根因在 hook；
  另有 4 处把 `toast` 放进依赖数组（PlanExecutePage×3、PlanRunDetailPage×1），
  属「无害但每次渲染白跑」，根修后一并消除，不动各页面代码。
- **eslint 规则兜底**（否决）：无现成规则能断言「hook 返回值引用稳定」，
  自写规则成本高于一个测试用例。
- **error 保留 Infinity + closeButton**（否决）：校验类错误（「请选择 Plan」）
  没有理由常驻；10s 足够读完一条错误，且从机制上杜绝堆叠。closeButton
  方案保留了「永不消失」的一半问题。

## Verification

- `tsc --noEmit` / eslint（改动文件 `--max-warnings 0`）/ **全量 vitest 78 文件
  582 用例**（含新增稳定性用例）/ `vite build` 全绿。
- DOM 实测（`/tmp/ui-shot-rig/verify-loop-fix.js`，dev server 5173 + 生产控制面
  只读 GET + route 拦截）：
  - 失败路径（全部 `/schedules`→500）：10s 观察窗请求恒为 2，不再增长；
    修复前同场景 3s 150+ 持续增长；
  - toast 恒为 2 条，~13s 后全部自动消失（10s duration 生效）；
  - 成功路径 10s 请求恰为 2（修复前成功也在环上）；
  - 前 toast top=88，与页头 action 不相交（#313 的 offset 行为不回归）。

**请求计数断言的风格（给下一个写这类断言的人）**：`main.tsx` 带
`<React.StrictMode>`，开发模式下 effect 挂载双调用，请求计数签名是
**dev=2 / prod=1**。断言写「有界不增长（恒等于 2）」而不是「恰好等于 1」，
是唯一不会在两种环境间翻车的写法；反过来若某天要断言「恰好 1」，须先
说明是在生产构建下测的。

## Revisit

- **duration 与断环缺一不可**：断环回答「为什么会有 7 条」，10s 回答「为什么
  第 7 条还在」。只断环，一次真实持续故障照样堆叠（每轮失败各留一条）；
  只改 duration，是把无限请求藏起来。两者已同 PR 落地，此处仅留推理备查。
- **duration 阶梯**（success 3s / info 4s / error 10s / action 5s）——
  2026-08-20 B10 轮评估后**维持现状**：梯度本身有内在逻辑（越需要读的留
  越久），且无「错误 toast 来不及读」的实际反馈；不再是待办。唯一保留
  观察：带 CTA 按钮的 `action` toast 5s 偏短，若未来收到相关反馈单独调。
- `useMemo` 依 React 文档不是语义保证（未来可能丢缓存），稳定性测试用例
  会在真实退化时报警；同理，回归用例断的是**引用稳定**而非「当前环没了」
  的行为快照——行为断言只证明这一个环修好，引用断言能拦住任何消费方
  把环重新写出来。
- 开发模式 StrictMode 双加载是 `main.tsx` 的既有选择，非缺陷；若未来觉得
  双请求碍事，在入口关 StrictMode 即可，与本修复无关。
