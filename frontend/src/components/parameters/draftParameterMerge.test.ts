import { describe, expect, it } from 'vitest';

import { mergeDraftParameters } from './draftParameterMerge';
import fixture from '../../../../tests/fixtures/plan_parameter_merge.json';

interface MergeCase {
  id: string;
  param_schema: Record<string, unknown> | null;
  default_params: Record<string, unknown> | null;
  project_preset?: Record<string, unknown> | null;
  step_params: Record<string, unknown> | null;
  expected: Record<string, unknown>;
}

const typedFixture = fixture as {
  log_prepare_v110_schema_bounds: {
    mobilelog_size_mb: { minimum: number; maximum: number };
    ylog_size_mb: { minimum: number; maximum: number };
  };
  cases: MergeCase[];
};
const cases = typedFixture.cases;

describe('mergeDraftParameters', () => {
  it.each(cases)('$id 与共享夹具一致', (item) => {
    expect(
      mergeDraftParameters(
        item.param_schema,
        item.default_params,
        item.step_params,
        item.project_preset ?? null,
      ),
    ).toEqual(item.expected);
  });

  it('共享夹具钉死 log_prepare 容量边界（ADR-0067 D2.6）', () => {
    expect(typedFixture.log_prepare_v110_schema_bounds).toEqual({
      mobilelog_size_mb: { minimum: 256, maximum: 20480 },
      ylog_size_mb: { minimum: 64, maximum: 10240 },
    });
  });

  it('深拷贝：改结果里的嵌套对象与数组不改输入', () => {
    const defaults = { wifi: { password: 'SENTINEL_PASSWORD' } };
    const preset = { ylog_required_streams: ['android', 'kernel'] };
    const merged = mergeDraftParameters({}, defaults, null, preset);
    (merged.wifi as { password: string }).password = 'changed';
    (merged.ylog_required_streams as string[]).push('uboot');
    expect(defaults.wifi.password).toBe('SENTINEL_PASSWORD');
    expect(preset.ylog_required_streams).toEqual(['android', 'kernel']);
  });
});
