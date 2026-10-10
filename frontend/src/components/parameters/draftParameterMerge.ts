/**
 * 未保存草稿的顶层浅合并。与 backend merge_effective_params 同优先级：
 * schema.default < default_params < step.params。嵌套对象整键替换，不深合并。
 * 只给草稿预览用，不参与已保存计划或派发。
 */
export function mergeDraftParameters(
  paramSchema: Record<string, unknown> | null | undefined,
  defaultParams: Record<string, unknown> | null | undefined,
  stepParams: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  if (paramSchema && typeof paramSchema === 'object') {
    for (const [key, field] of Object.entries(paramSchema)) {
      if (isField(field) && 'default' in field) {
        merged[key] = clone(field.default);
      }
    }
  }
  assignCloned(merged, defaultParams);
  assignCloned(merged, stepParams);
  return merged;
}

function isField(value: unknown): value is { default?: unknown } {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function assignCloned(
  target: Record<string, unknown>,
  source: Record<string, unknown> | null | undefined,
): void {
  if (!source || typeof source !== 'object') return;
  for (const [key, value] of Object.entries(source)) {
    target[key] = clone(value);
  }
}

function clone<T>(value: T): T {
  if (typeof value !== 'object' || value === null) return value;
  return structuredClone(value);
}
