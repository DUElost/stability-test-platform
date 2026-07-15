import { describe, expect, it } from 'vitest';
import { evaluateDeviceReadiness } from './planExecuteReadiness';

describe('evaluateDeviceReadiness', () => {
  it('checks runtime blockers from platform state', () => {
    const result = evaluateDeviceReadiness([
      { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE', adb_connected: true, adb_state: 'device', build_display_id: 'v1' },
      { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE', adb_connected: false, adb_state: 'offline', build_display_id: 'v2' },
    ], [{ id: 'h1', name: 'node-1', status: 'ONLINE' }]);
    expect(result.readyCount).toBe(1);
    expect(result.blockedCount).toBe(1);
    expect(result.rows[1].reasons).toEqual(['ADB offline']);
    expect(result.passed).toBe(false);
  });

  it('honors schedulable, host status, and warning branches', () => {
    const result = evaluateDeviceReadiness([
      { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE', schedulable: false },
      { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE', adb_state: 'unauthorized', model: 'M1', build_display_id: 'v1' },
      { id: 3, serial: 'C', host_id: 'h2', status: 'ONLINE', model: 'M2', build_display_id: 'v2' },
    ], [{ id: 'h1', status: 'OFFLINE' }, { id: 'h2', status: 'ONLINE' }]);
    expect(result.blockedDeviceIds).toEqual([1, 2]);
    expect(result.rows[0].reasons).toContain('设备不可调度');
    expect(result.rows[1].reasons).toContain('节点离线');
    expect(result.rows[1].reasons).toContain('ADB unauthorized');
    expect(result.warnings).toContain('已选设备包含多个版本');
    expect(result.warnings).toContain('已选设备包含多个型号');
  });

  it('reports missing version information', () => {
    const result = evaluateDeviceReadiness([{ id: 1, serial: 'A', status: 'ONLINE' }], []);
    expect(result.warnings).toContain('部分设备缺少版本信息');
  });
});
