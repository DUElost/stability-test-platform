/** #83 表格虚拟化的算术与阈值（jsdom 测不了几何 → 只测可测的部分，见模块注释）。 */
import { describe, expect, it } from 'vitest';
import {
  DEVICE_TABLE_OVERSCAN,
  DEVICE_TABLE_ROW_PX,
  DEVICE_TABLE_VIRTUALIZE_THRESHOLD,
  deviceTableSpacers,
  shouldVirtualizeDeviceTable,
} from './deviceTableVirtual';

describe('shouldVirtualizeDeviceTable', () => {
  it('阈值处不虚拟化、刚过阈值虚拟化（小 run 不为虚拟层买单）', () => {
    expect(shouldVirtualizeDeviceTable(0)).toBe(false);
    expect(shouldVirtualizeDeviceTable(DEVICE_TABLE_VIRTUALIZE_THRESHOLD)).toBe(false);
    expect(shouldVirtualizeDeviceTable(DEVICE_TABLE_VIRTUALIZE_THRESHOLD + 1)).toBe(true);
    expect(shouldVirtualizeDeviceTable(510)).toBe(true); // #83 的实测规模
  });
});

describe('deviceTableSpacers', () => {
  it('窗口在中间时，两端各补回未渲染行的高度', () => {
    const visible = [
      { start: 86, end: 129 },
      { start: 129, end: 172 },
    ];
    expect(deviceTableSpacers(visible, 510 * DEVICE_TABLE_ROW_PX)).toEqual({
      padTopPx: 86,
      padBottomPx: 510 * DEVICE_TABLE_ROW_PX - 172,
    });
  });

  it('空窗口把全部高度留给下垫片（首屏还没测出行时不能塌成 0 高）', () => {
    expect(deviceTableSpacers([], 1000)).toEqual({ padTopPx: 0, padBottomPx: 1000 });
    expect(deviceTableSpacers([], 0)).toEqual({ padTopPx: 0, padBottomPx: 0 });
  });

  it('estimateSize 与实测有偏差时末行 end 会超过 totalSize —— 垫片必须夹到 ≥0', () => {
    // 不夹就是 `height: -7px` 这种脏样式，React 会真的写进 DOM
    const visible = [{ start: 990, end: 1007 }];
    expect(deviceTableSpacers(visible, 1000).padBottomPx).toBe(0);
  });

  it('overscan 与行高是正数常量（几何单一定义点，静态守卫也钉这里）', () => {
    expect(DEVICE_TABLE_ROW_PX).toBeGreaterThan(0);
    expect(DEVICE_TABLE_OVERSCAN).toBeGreaterThan(0);
  });
});
