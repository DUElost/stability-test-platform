import { describe, expect, it } from 'vitest';

import { mergeDraftParameters } from './draftParameterMerge';
import fixture from '../../../../tests/fixtures/plan_parameter_merge.json';

interface MergeCase {
  id: string;
  param_schema: Record<string, unknown> | null;
  default_params: Record<string, unknown> | null;
  step_params: Record<string, unknown> | null;
  expected: Record<string, unknown>;
}

const cases = (fixture as { cases: MergeCase[] }).cases;

describe('mergeDraftParameters', () => {
  it.each(cases)('$id 与共享夹具一致', (item) => {
    expect(mergeDraftParameters(item.param_schema, item.default_params, item.step_params))
      .toEqual(item.expected);
  });

  it('深拷贝：改结果里的嵌套对象不改 default_params', () => {
    const defaults = { wifi: { password: 'SENTINEL_PASSWORD' } };
    const merged = mergeDraftParameters({}, defaults, null);
    (merged.wifi as { password: string }).password = 'changed';
    expect(defaults.wifi.password).toBe('SENTINEL_PASSWORD');
  });
});
