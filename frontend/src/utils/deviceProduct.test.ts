import { describe, expect, it } from 'vitest';
import { deviceProductLabel, deviceProductText } from './deviceProduct';

describe('device product display', () => {
  it('distinguishes the five observed devices without replacing raw product', () => {
    expect(deviceProductText('MLD_NX2', 'MLD-NX2')).toBe('NX2 · MLD-NX2');
    expect(deviceProductText('MLD_NX3', 'MLD-NX3')).toBe('NX3 · MLD-NX3');
    expect(deviceProductText('MLD_NX3', 'sys_mssi_64_cn_armv82')).toBe('NX3Carrier · sys_mssi_64_cn_armv82');
    expect(deviceProductLabel('MLD-NX3', 'sys_mssi_64_cn_armv82')).toBe('NX3Carrier');
  });
  it('does not guess from model or a generic product on another model', () => {
    expect(deviceProductText('MLD_NX3', null)).toBe('未上报');
    expect(deviceProductLabel('OTHER', 'sys_mssi_64_cn_armv82')).toBeNull();
    expect(deviceProductText('OTHER', 'sys_mssi_64_cn_armv82')).toBe('sys_mssi_64_cn_armv82');
    expect(deviceProductText('MLD_NX2', 'MLD-NX3')).toBe('MLD-NX3');
  });
});
