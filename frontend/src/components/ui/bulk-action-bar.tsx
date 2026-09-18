/**
 * 底部悬浮批量条的避让规格（#2614，与 #360 里「同类悬浮批量操作条各页不一致」是同一件事）。
 *
 * 为什么要成文：条体是 `fixed bottom-4` + `z-40` 的**覆盖层**，外层 `pointer-events-none`
 * 只是把两侧留让出命中区，真正接事件的是居中的内层实条——所以它必然压住滚动到底的最后一块
 * 可点区域（分页行）。主机页先修过一次（`be51f2c6`）但没把规格留下来，设备页随即以同一形状
 * 复发：1280/1366 宽下「下一页」的坐标点击被条体吞掉，最坏情况落在「取消选择」上，
 * 刚建立的整页选中被静默清空。收成一处之后，第三张表格页要么复用、要么被守卫用例判红。
 */

/** 悬浮条外层：不接指针事件，左右留白处可穿透（几何规格，两页共用）。 */
export const BULK_BAR_OUTER_CLASS =
  'pointer-events-none fixed bottom-4 left-4 right-4 z-40 flex justify-center lg:left-60';

/** 悬浮条内层：真正参与命中测试的实条，其横向范围随视口宽度变化。 */
export const BULK_BAR_INNER_CLASS =
  'pointer-events-auto flex w-full max-w-5xl flex-wrap items-center gap-3 ' +
  'rounded-2xl border border-border bg-card/95 px-3 py-3 shadow-xl backdrop-blur ' +
  'supports-[backdrop-filter]:bg-card/90 sm:px-4';

/**
 * 选中态占位高度：必须 ≥「条体高度 + `bottom-4`」。实测条体 ≈63px（单行）到 ≈110px
 * （窄视口 `flex-wrap` 两行），取 160px 留余量——`h-40` 是两个页面共用的同一规格。
 */
export const BULK_BAR_SPACER_CLASS = 'h-40 shrink-0';

/**
 * 选中态占位块：渲染在表格**之后**，把最后一行（分页行）顶出悬浮条的覆盖带。
 *
 * 由调用方在「有选中」时渲染——无选中时条体不存在，占位就变成无故留白。
 */
export function BulkBarSpacer({ testId }: { testId: string }) {
  return <div data-testid={testId} aria-hidden className={BULK_BAR_SPACER_CLASS} />;
}
