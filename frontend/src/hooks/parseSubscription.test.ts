import { describe, it, expect } from 'vitest';
import { parseSubscription } from '@/hooks/useSocketIO';
import {
  DASHBOARD_SUBSCRIPTION,
  FLEET_DEVICES_SUBSCRIPTION,
  consoleSubscription,
  planRunSubscription,
} from '@/config';
import { SOCKET_EVENT_NAMES } from '@/utils/socketEvents';

describe('parseSubscription (#419)', () => {
  it('maps dashboard descriptor', () => {
    const cfg = parseSubscription(DASHBOARD_SUBSCRIPTION);
    expect(cfg.room).toBeNull();
    expect(cfg.events).not.toContain(SOCKET_EVENT_NAMES.deviceUpdate);
    expect(cfg.events).toContain(SOCKET_EVENT_NAMES.dashboardSummary);
    expect(cfg.events).toContain(SOCKET_EVENT_NAMES.planChanged);
  });

  it('maps fleet:devices descriptor (#2369)', () => {
    expect(parseSubscription(FLEET_DEVICES_SUBSCRIPTION)).toEqual({
      room: 'fleet:devices',
      events: [SOCKET_EVENT_NAMES.deviceUpdate],
    });
  });

  it('maps plan_run / console helpers', () => {
    expect(parseSubscription(planRunSubscription(42))).toEqual({
      room: 'plan_run:42',
      events: [
        SOCKET_EVENT_NAMES.jobStatus,
        SOCKET_EVENT_NAMES.planRunStatus,
        SOCKET_EVENT_NAMES.precheckUpdate,
        SOCKET_EVENT_NAMES.watcherSignal,
      ],
    });
    expect(parseSubscription(consoleSubscription('run-abc'))).toEqual({
      room: 'console:run-abc',
      events: [SOCKET_EVENT_NAMES.consoleLog, SOCKET_EVENT_NAMES.consoleStatus],
    });
  });

  // #2400：job:/run: 房间两侧（emit 与订阅）一起删——描述符解析退化为无订阅，
  // 与任意未知描述符同路径。
  it('no longer resolves job:/run: descriptors (#2400)', () => {
    expect(parseSubscription('job:7')).toEqual({ room: null, events: [] });
    expect(parseSubscription('run:9')).toEqual({ room: null, events: [] });
  });

  it('rejects legacy /ws paths and unknown descriptors', () => {
    expect(parseSubscription('/ws/dashboard')).toEqual({ room: null, events: [] });
    expect(parseSubscription('/ws/plan-runs/1')).toEqual({ room: null, events: [] });
    expect(parseSubscription('/ws/console/x')).toEqual({ room: null, events: [] });
    expect(parseSubscription('nope')).toEqual({ room: null, events: [] });
    expect(parseSubscription('')).toEqual({ room: null, events: [] });
  });
});
