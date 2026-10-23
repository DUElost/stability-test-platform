import { describe, expect, it } from 'vitest';
import {
  DEVICE_RETURN_AGGREGATE_LABELS,
  disarmResultLabel,
  deviceReturnAggregate,
  familyLabel,
  returnSourceLabel,
} from './deviceReturn';

describe('deviceReturnAggregate', () => {
  it('returns uninitialized for null/empty state (must not infer returned)', () => {
    expect(deviceReturnAggregate(null)).toBe('uninitialized');
    expect(deviceReturnAggregate(undefined)).toBe('uninitialized');
    expect(deviceReturnAggregate({})).toBe('uninitialized');
    expect(DEVICE_RETURN_AGGREGATE_LABELS.uninitialized).toBe('未初始化');
  });

  it('applies debt > unverified > returned', () => {
    const allReturned = {
      'power-cycle': { state: 'returned' },
      sleep: { state: 'returned' },
      mtbf: { state: 'returned' },
      gpu: { state: 'returned' },
      monkey: { state: 'returned' },
    };
    expect(deviceReturnAggregate(allReturned)).toBe('returned');
    expect(
      deviceReturnAggregate({
        ...allReturned,
        sleep: { state: 'unverified' },
      }),
    ).toBe('unverified');
    expect(
      deviceReturnAggregate({
        ...allReturned,
        sleep: { state: 'unverified' },
        monkey: { state: 'debt' },
      }),
    ).toBe('debt');
  });

  it('treats a missing registered family as unverified (backend D1.4 parity)', () => {
    expect(deviceReturnAggregate({ 'power-cycle': { state: 'returned' } })).toBe('unverified');
  });
});

describe('labels keep unknown machine values verbatim', () => {
  it('maps the six disarm results and falls back to the raw value', () => {
    expect(disarmResultLabel('confirmed')).toBe('已确认');
    expect(disarmResultLabel('executed_unverified')).toBe('已执行未验证');
    expect(disarmResultLabel('failed')).toBe('失败');
    expect(disarmResultLabel('readback_failed')).toBe('回读失败');
    expect(disarmResultLabel('not_executed')).toBe('未执行');
    expect(disarmResultLabel('incomplete')).toBe('未完成');
    expect(disarmResultLabel('future_value')).toBe('future_value');
  });

  it('maps sources and families, falling back to raw values', () => {
    expect(returnSourceLabel('job')).toBe('job');
    expect(returnSourceLabel('init_check')).toBe('init 检查');
    expect(returnSourceLabel('discharge')).toBe('清偿');
    expect(returnSourceLabel('override')).toBe('人工放行');
    expect(returnSourceLabel('stock')).toBe('存量初始化');
    expect(returnSourceLabel('new_source')).toBe('new_source');
    expect(returnSourceLabel(null)).toBe('—');
    expect(familyLabel('power-cycle')).toBe('开关机');
    expect(familyLabel('new-family')).toBe('new-family');
  });
});
