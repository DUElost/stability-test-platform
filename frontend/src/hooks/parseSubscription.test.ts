import { describe, it, expect } from 'vitest';
import { parseSubscription } from '@/hooks/useSocketIO';
import {
  DASHBOARD_SUBSCRIPTION,
  consoleSubscription,
  jobLogsSubscription,
  planRunSubscription,
  runLogsSubscription,
} from '@/config';
import { SOCKET_EVENT_NAMES } from '@/utils/socketEvents';

describe('parseSubscription (#419)', () => {
  it('maps dashboard descriptor', () => {
    const cfg = parseSubscription(DASHBOARD_SUBSCRIPTION);
    expect(cfg.room).toBeNull();
    expect(cfg.events).toContain(SOCKET_EVENT_NAMES.deviceUpdate);
    expect(cfg.events).toContain(SOCKET_EVENT_NAMES.planChanged);
  });

  it('maps plan_run / console / job / run helpers', () => {
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
    expect(parseSubscription(jobLogsSubscription(7))).toEqual({
      room: 'job:7',
      events: [SOCKET_EVENT_NAMES.stepLog, SOCKET_EVENT_NAMES.stepUpdate],
    });
    expect(parseSubscription(runLogsSubscription(9))).toEqual({
      room: 'run:9',
      events: [SOCKET_EVENT_NAMES.stepLog, SOCKET_EVENT_NAMES.stepUpdate],
    });
  });

  it('rejects legacy /ws paths and unknown descriptors', () => {
    expect(parseSubscription('/ws/dashboard')).toEqual({ room: null, events: [] });
    expect(parseSubscription('/ws/plan-runs/1')).toEqual({ room: null, events: [] });
    expect(parseSubscription('/ws/console/x')).toEqual({ room: null, events: [] });
    expect(parseSubscription('nope')).toEqual({ room: null, events: [] });
    expect(parseSubscription('')).toEqual({ room: null, events: [] });
  });
});
