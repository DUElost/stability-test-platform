import { describe, expect, it } from 'vitest';
import {
  buildCapacityPlan,
  buildDeviceReadinessRows,
  compareNodeEntries,
  evaluateCapacityOverflow,
  evaluateDeviceReadiness,
  summarizeDeviceReadiness,
} from './planExecuteReadiness';

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

  it('reuses precomputed rows for subset summaries', () => {
    const devices = [
      { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE', adb_connected: true, adb_state: 'device', build_display_id: 'v1' },
      { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE', adb_connected: false, adb_state: 'offline', build_display_id: 'v2' },
    ];
    const hosts = [{ id: 'h1', name: 'node-1', status: 'ONLINE' }];
    const rows = buildDeviceReadinessRows(devices, hosts);
    const rowsByDeviceId = new Map(rows.map((row) => [row.device.id, row]));
    const subset = summarizeDeviceReadiness([devices[0]], rowsByDeviceId);
    expect(subset.readyCount).toBe(1);
    expect(subset.passed).toBe(true);
    const full = summarizeDeviceReadiness(devices, rowsByDeviceId);
    expect(full.readyCount).toBe(1);
    expect(full.passed).toBe(false);
  });
});

describe('evaluateCapacityOverflow', () => {
  it('warns when selected count exceeds effective_slots', () => {
    const warnings = evaluateCapacityOverflow(
      [
        { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'C', host_id: 'h1', status: 'ONLINE' },
      ],
      [{ id: 'h1', ip: '172.21.8.143', capacity: { effective_slots: 2, active_jobs: 1 } }],
    );
    expect(warnings).toHaveLength(1);
    expect(warnings[0].selected).toBe(3);
    expect(warnings[0].effectiveSlots).toBe(2);
    expect(warnings[0].message).toContain('超出剩余可派发槽位 2 个');
  });

  it('does not warn when selected equals effective_slots', () => {
    const warnings = evaluateCapacityOverflow(
      [
        { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE' },
      ],
      [{ id: 'h1', ip: '172.21.8.143', capacity: { effective_slots: 2 } }],
    );
    expect(warnings).toEqual([]);
  });

  it('skips hosts with missing effective_slots to avoid false alarms', () => {
    const warnings = evaluateCapacityOverflow(
      [
        { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE' },
      ],
      [{ id: 'h1', ip: '172.21.8.143', capacity: { active_jobs: 3 } }],
    );
    expect(warnings).toEqual([]);
  });

  it('ignores unassigned devices and evaluates per host', () => {
    const warnings = evaluateCapacityOverflow(
      [
        { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'C', host_id: null, status: 'ONLINE' },
        { id: 4, serial: 'D', host_id: 'h2', status: 'ONLINE' },
      ],
      [
        { id: 'h1', name: 'node-a', capacity: { effective_slots: 1 } },
        { id: 'h2', name: 'node-b', capacity: { effective_slots: 5 } },
      ],
    );
    expect(warnings).toHaveLength(1);
    expect(warnings[0].hostId).toBe('h1');
    expect(warnings[0].hostLabel).toBe('node-a');
  });
});

describe('buildCapacityPlan', () => {
  it('calculates immediate and queued counts for every selected host', () => {
    const rows = buildCapacityPlan(
      [
        { id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE' },
        { id: 2, serial: 'B', host_id: 'h1', status: 'ONLINE' },
        { id: 3, serial: 'C', host_id: 'h2', status: 'ONLINE' },
      ],
      [
        { id: 'h1', ip: '172.21.8.10', capacity: { effective_slots: 1 } },
        { id: 'h2', ip: '172.21.8.11', capacity: { effective_slots: 3 } },
      ],
    );

    expect(rows[0]).toMatchObject({ hostId: 'h1', selected: 2, immediate: 1, queued: 1 });
    expect(rows[1]).toMatchObject({ hostId: 'h2', selected: 1, immediate: 1, queued: 0 });
  });

  it('keeps missing capacity explicit instead of estimating a queue', () => {
    const rows = buildCapacityPlan(
      [{ id: 1, serial: 'A', host_id: 'h1', status: 'ONLINE' }],
      [{ id: 'h1', name: 'node-a', capacity: { active_jobs: 2 } }],
    );

    expect(rows[0]).toMatchObject({
      effectiveSlots: null,
      immediate: null,
      queued: null,
    });
  });
});

describe('compareNodeEntries', () => {
  it('sorts IPv4 labels numerically by octet', () => {
    const nodes = [
      { id: 'h3', label: '172.21.9.124' },
      { id: 'h1', label: '172.21.8.103' },
      { id: 'h2', label: '172.21.9.6' },
    ];
    expect(nodes.sort(compareNodeEntries).map(n => n.label)).toEqual([
      '172.21.8.103',
      '172.21.9.6',
      '172.21.9.124',
    ]);
  });

  it('keeps unassigned last and IPs before non-IP names', () => {
    const nodes = [
      { id: 'unassigned', label: '未分配节点' },
      { id: 'h2', label: 'lab-node' },
      { id: 'h1', label: '172.21.8.103' },
    ];
    expect(nodes.sort(compareNodeEntries).map(n => n.id)).toEqual(['h1', 'h2', 'unassigned']);
  });

  it('falls back to numeric localeCompare for non-IP labels', () => {
    const nodes = [
      { id: 'h2', label: 'node-10' },
      { id: 'h1', label: 'node-2' },
    ];
    expect(nodes.sort(compareNodeEntries).map(n => n.label)).toEqual(['node-2', 'node-10']);
  });
});
