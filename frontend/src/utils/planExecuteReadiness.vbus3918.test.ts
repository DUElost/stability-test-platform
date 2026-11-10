import { describe, expect, it } from 'vitest';
import { isSchedulable, resolveDeviceTileStatus } from '@/components/execution/plan-execute/tileStatus';
import {
  buildDeviceReadinessRows,
  isVbusPoweredOffCandidate,
  markVbusSelectable,
  type ReadinessDevice,
} from './planExecuteReadiness';

// ADR-0063 D7 第 6 条 / #3918：vbus 刷机计划下，OFFLINE 且槽位报告关机态的设备可以勾选。
const host = { id: 'h1', status: 'ONLINE' };
const off = (id: number, extra: Partial<ReadinessDevice> = {}): ReadinessDevice => ({
  id,
  serial: `S${id}`,
  host_id: 'h1',
  status: 'OFFLINE',
  adb_connected: false,
  adb_state: 'offline',
  is_stale: true,
  usb_presence: { state: 'powered_off', host_online: true },
  ...extra,
});

describe('vbus powered-off candidates', () => {
  it('only OFFLINE, in-service, powered_off on an online host qualifies', () => {
    expect(isVbusPoweredOffCandidate(off(1))).toBe(true);
    expect(isVbusPoweredOffCandidate(off(2, { usb_presence: null }))).toBe(false);
    expect(isVbusPoweredOffCandidate(off(3, { usb_presence: { state: 'adb_mode', host_online: true } }))).toBe(false);
    expect(isVbusPoweredOffCandidate(off(4, { usb_presence: { state: 'absent', host_online: true } }))).toBe(false);
    expect(isVbusPoweredOffCandidate(off(5, { usb_presence: { state: 'powered_off', host_online: false } }))).toBe(false);
    expect(isVbusPoweredOffCandidate(off(6, { retired_at: '2026-11-01T00:00:00Z' }))).toBe(false);
    expect(isVbusPoweredOffCandidate(off(7, { status: 'ERROR' }))).toBe(false);
  });

  it('non-vbus plan leaves devices untouched and unselectable', () => {
    const devices = [off(1)];
    const marked = markVbusSelectable(devices, false);
    expect(marked).toBe(devices);
    expect(isSchedulable(marked[0])).toBe(false);
    expect(resolveDeviceTileStatus(marked[0])).toBe('offline');
  });

  it('vbus plan makes powered-off candidates selectable and ready', () => {
    const [cand, unmapped] = markVbusSelectable([off(1), off(2, { usb_presence: null })], true);
    expect(isSchedulable(cand)).toBe(true);
    expect(isSchedulable(unmapped)).toBe(false);
    expect(resolveDeviceTileStatus(cand, { readinessReady: true })).toBe('ready');

    const rows = buildDeviceReadinessRows([cand, unmapped], [host]);
    expect(rows[0].reasons).toEqual([]);
    expect(rows[0].ready).toBe(true);
    expect(rows[1].reasons).toContain('设备不可调度');
  });

  it('unauthorized still blocks even under a vbus plan', () => {
    const [cand] = markVbusSelectable([off(1, { adb_state: 'unauthorized' })], true);
    expect(buildDeviceReadinessRows([cand], [host])[0].reasons).toContain('ADB unauthorized');
  });

  it('ONLINE devices stay selectable under a vbus plan', () => {
    const online: ReadinessDevice = { id: 9, serial: 'S9', host_id: 'h1', status: 'ONLINE', adb_state: 'device' };
    const [d] = markVbusSelectable([online], true);
    expect(d).toBe(online);
    expect(isSchedulable(d)).toBe(true);
  });
});
