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
});
