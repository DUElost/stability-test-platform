import type { PlanFailedDevicesItem } from '@/utils/api/types';

/**
 * 「方案失败设备数排行」的行装配（#2848）——**顺序与条数以服务端为权威**。
 *
 * 放在独立模块而不是组件文件里：一是 `react-refresh/only-export-components` 要求
 * 组件文件只导出组件；二是仓内已有同形先例（`deviceTableVirtual.ts`、`minimapGrid.ts`），
 * 纯算术与几何渲染分家后，jsdom 才能直接断言「不重排、不截断」——图的几何测不了
 * （`docs/development/testing.md` §4），但顺序与条数测得了。
 *
 * 原先组件里又做一次 `sort(failed DESC)` + `slice(0, 10)`：
 * - 排序键比服务端少一个 tie-break（`failed DESC, total_jobs DESC`）⇒ 同分次序两边各说一套；
 * - `slice(0, 10)` 把调用方要的 `limit > 10`（接口允许到 50）**静默截回 10**
 *   ——「改了没生效」是最难查的那类。
 */
export function buildFailedDeviceRows(data?: PlanFailedDevicesItem[]) {
  if (!data || data.length === 0) return [];
  return data.map((d) => ({ ...d, label: getPlanLabel(d) }));
}

/** 标签截断：超过 20 字符留 19 + 省略号（纯展示口径，唯一一份）。 */
export function getPlanLabel(d: PlanFailedDevicesItem): string {
  return d.plan_name.length > 20 ? d.plan_name.slice(0, 19) + '...' : d.plan_name;
}
