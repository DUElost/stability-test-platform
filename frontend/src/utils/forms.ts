/**
 * 表单取值：**以 DOM 实际值为准**，state 只作回落（#2453 / #2456）。
 *
 * 为什么需要：受控组件（`value={state}` + `onChange`）只认 React state，而**密码管理器 /
 * 浏览器自动填充**是**直接写 `.value`**（往往还只派发**非冒泡**的 `input` 事件）——
 * React 的 `onChange` 收不到，state 仍为空。于是校验把「明明填好了」的表单判成空，
 * 提交**根本不发出**（#2453 现场：nginx 访问日志里那几次零请求）。
 *
 * 提交时读 DOM 不依赖任何「谁填的、何时填的、是否派发事件、是否冒泡」的假设：
 * **表单此刻是什么就提交什么**。
 *
 * 用法：给字段加 `name`，提交处：
 * ```tsx
 * const values = readNamedValues(e.currentTarget as HTMLFormElement, { username, password });
 * ```
 * `root` 可以是 `<form>`，也可以是任意容器（无 form 的表单传容器 ref 亦可）。
 */
export function readNamedValues(
  root: ParentNode | null | undefined,
  fallback: Record<string, string>,
): Record<string, string> {
  const values: Record<string, string> = { ...fallback };
  if (!root) return values;
  for (const name of Object.keys(fallback)) {
    const el = root.querySelector<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >(`[name="${name}"]`);
    if (el && typeof el.value === 'string') {
      values[name] = el.value;
    }
  }
  return values;
}
