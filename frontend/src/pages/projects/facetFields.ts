/** ADR-0029 facet：正交可组合筛选字段。

 * 此前 `ProjectsPage` 存 `['customer', ...] as const` + 独立 label 映射，
 * `ProjectDetailPage` 存 `[['customer', '客户'], ...]` 元组——同一概念两份、
 * 形态还不一样，加一个 facet 要改两处且极易漏（#499 E3）。此处作为唯一事实源：
 * `FACET_FIELDS` 给筛选逻辑用，`FACET_FIELD_ENTRIES` 给遍历渲染用。
 *
 * 匹配语义（保持既有口径）：`platforms`（P1-B 派生数组）= 包含；其余标量 = 相等。
 */
export const FACET_FIELDS = [
  'customer',
  'platforms',
] as const;

export type FacetField = (typeof FACET_FIELDS)[number];

export const FACET_LABEL: Record<FacetField, string> = {
  customer: '客户',
  platforms: '平台',
};

export const FACET_FIELD_ENTRIES: ReadonlyArray<readonly [FacetField, string]> = [
  ['customer', FACET_LABEL.customer],
  ['platforms', FACET_LABEL.platforms],
];
