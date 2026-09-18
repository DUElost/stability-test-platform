/**
 * #83：「设备总览」表格视图的行虚拟化口径（几何与窗口的**单一定义点**）。
 *
 * 为什么要单独一个模块：本仓的前端测试跑在 jsdom 上，而 jsdom **没有布局引擎**
 * （`getBoundingClientRect()` 恒 0、滚动位置无语义，见 `docs/development/testing.md` §4）。
 * 于是「虚拟化的几何对不对」在 jsdom 里结构上不可测——能被测的只有**算术**，能被锁的只有
 * **常量**。所以把两者抽到这里：常量给静态守卫（`tests/test_frontend_device_table_virtual_guard_83.py`）
 * 钉住，纯函数给 vitest 用例直接喂合成数据。真实浏览器里的 510 行/千行成本复测另计。
 *
 * 实测依据（#83 的 09-18 测量，510 台 run）：表格态 **14,056 DOM 节点 / 511 个 `<tr>` /
 * `[data-index]=0`（无虚拟化）**，而同一组件的 minimap 态只要 603 节点（≈1.0 节点/设备）。
 * 两种视图渲染同一批数据，成本差约 28× —— 需要虚拟化的只有表格视图，
 * **minimap 保留全量**（它每设备 1 节点，本来就不该分页）。
 */

/** 单行高度估计（px）。真机量得 ≈43px；与 `DeviceMatrix` 的瓦片口径无关，勿互相套用。 */
export const DEVICE_TABLE_ROW_PX = 43;

/** 视口上下各多渲染的行数——滚动时不出现空白带。 */
export const DEVICE_TABLE_OVERSCAN = 8;

/**
 * 超过该行数才启用虚拟化。
 *
 * 与 `SelectedMinimap` 的 `MINIMAP_VIRTUAL_THRESHOLD = 80` 同量级：**小 run 不为虚拟层买单**
 * （多一层滚动容器与测量，换来的只有复杂度），大 run 才是收益方。
 */
export const DEVICE_TABLE_VIRTUALIZE_THRESHOLD = 80;

/** 表格滚动容器的最大高度（px）——真机上表格自己滚，不把整页撑到 2 万像素。 */
export const DEVICE_TABLE_VIEWPORT_MAX_PX = 640;

/** 该行数下是否启用虚拟化（纯函数，便于 jsdom 直接断言阈值语义）。 */
export function shouldVirtualizeDeviceTable(count: number): boolean {
  return count > DEVICE_TABLE_VIRTUALIZE_THRESHOLD;
}

/** `useVirtualizer` 给出的可见行的最小形状（只取算术需要的字段）。 */
export type VisibleRow = { start: number; end: number };

/**
 * 上下垫片高度：把「未渲染的行」用两段空白补回去，滚动条长度与真实总高一致。
 *
 * - 空列表 → 两端都是 0（不能算出负垫片）；
 * - 首行不在窗口里 → `padTop = first.start`；
 * - 尾行之后仍有剩余 → `padBottom = totalSize - last.end`（**夹到 ≥0**：
 *   `estimateSize` 与实际测量有偏差时 `last.end` 可能超过 `totalSize`，
 *   不夹会出现负高度，React 会把它渲染成 `height: -7px` 这种脏样式）。
 */
export function deviceTableSpacers(
  visible: readonly VisibleRow[],
  totalSize: number,
): { padTopPx: number; padBottomPx: number } {
  if (visible.length === 0) {
    return { padTopPx: 0, padBottomPx: Math.max(0, totalSize) };
  }
  const first = visible[0];
  const last = visible[visible.length - 1];
  return {
    padTopPx: Math.max(0, first.start),
    padBottomPx: Math.max(0, totalSize - last.end),
  };
}
