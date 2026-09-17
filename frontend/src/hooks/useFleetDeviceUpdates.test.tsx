/**
 * #2447：`fleet:devices` 的 DEVICE_UPDATE 合流必须是「前缘立即 + 尾部补一次」。
 *
 * 回归前是纯前缘节流：窗口内到达的推送**整条丢弃**（既不失效也不记账），设备页在
 * 事件密集时最长陈旧一个窗口——当时靠 `DevicesPage` 的 10s 轮询兜底，轮询一撤
 * 就退化为长期陈旧。这里钉住两条：窗口内不丢（尾部补一次）、密集推送不饥饿
 * （尾部只补一次，不是每条都推迟）。
 */
import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  FLEET_DEVICE_UPDATE_THROTTLE_MS,
  useFleetDeviceUpdates,
} from './useFleetDeviceUpdates';

const mocks = vi.hoisted(() => ({
  onMessage: null as null | ((msg: { type: string; payload: unknown }) => void),
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: (
    _sub: string,
    opts: { onMessage?: (msg: { type: string; payload: unknown }) => void },
  ) => {
    mocks.onMessage = opts.onMessage ?? null;
  },
}));

function renderFleet() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const view = renderHook(() => useFleetDeviceUpdates(true), { wrapper });
  return { invalidate, view };
}

const push = () => {
  act(() => {
    mocks.onMessage!({ type: 'DEVICE_UPDATE', payload: {} });
  });
};

describe('useFleetDeviceUpdates 合流（#2447）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.onMessage = null;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('窗口外到达：立即失效（前缘语义不变）', () => {
    const { invalidate } = renderFleet();
    push();
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('窗口内到达：不当场失效，但窗口末补一次（不再整条丢弃）', () => {
    const { invalidate } = renderFleet();
    push();
    expect(invalidate).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(500);
    });
    push();
    expect(invalidate).toHaveBeenCalledTimes(1); // 窗口内不叠加

    act(() => {
      vi.advanceTimersByTime(FLEET_DEVICE_UPDATE_THROTTLE_MS);
    });
    expect(invalidate).toHaveBeenCalledTimes(2); // 尾部补上
  });

  it('窗口内密集推送：尾部只补一次（不饥饿，也不放大成 N 次）', () => {
    const { invalidate } = renderFleet();
    push();

    for (let i = 0; i < 5; i += 1) {
      act(() => {
        vi.advanceTimersByTime(100);
      });
      push();
    }
    expect(invalidate).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(FLEET_DEVICE_UPDATE_THROTTLE_MS);
    });
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it('非 DEVICE_UPDATE 事件不触发失效', () => {
    const { invalidate } = renderFleet();
    act(() => {
      mocks.onMessage!({ type: 'DASHBOARD_SUMMARY', payload: {} });
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
