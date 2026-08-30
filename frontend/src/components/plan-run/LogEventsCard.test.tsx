/**
 * #529 — LogEventsCard：终态 PlanRun 的 DLE 事件视图（归档权威）。
 * 只读 device_log_event 端点；RUNNING 不触发；路径优先 remote_path。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import LogEventsCard from './LogEventsCard';
import realPayload from './__fixtures__/log-events-103.json';

const mocks = vi.hoisted(() => ({
  getLogEvents: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      getLogEvents: mocks.getLogEvents,
    },
  },
}));

function renderCard(runId: number, isTerminal: boolean) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <LogEventsCard runId={runId} isTerminal={isTerminal} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('LogEventsCard (#529)', () => {
  it('RUNNING 时不触发请求（不变量：RUNNING 仍读 watcher-summary）', () => {
    renderCard(103, false);
    expect(mocks.getLogEvents).not.toHaveBeenCalled();
  });

  it('终态时拉取 DLE 并渲染 remote_path 优先的路径', async () => {
    mocks.getLogEvents.mockResolvedValue(realPayload);
    renderCard(103, true);

    await waitFor(() => expect(mocks.getLogEvents).toHaveBeenCalledWith(103, expect.any(Object)));
    expect(screen.getByTestId('log-events-card')).toBeInTheDocument();

    // 有 remote_path → 展示 remote_path；无 remote_path → 回落 local_path
    await waitFor(() => {
      expect(screen.getByText('/mnt/stp-aee/devices/103/0000NX2622000514/NE_20260725_175707')).toBeInTheDocument();
    });
    expect(screen.getByText('/mnt/hdd/aee_events/103/0000NX2622000514/ANR_20260725_175656')).toBeInTheDocument();

    // 状态徽章（REMOTE / UPLOAD_PENDING / ARCHIVED）
    expect(screen.getByText('REMOTE')).toBeInTheDocument();
    expect(screen.getByText('UPLOAD_PENDING')).toBeInTheDocument();
    expect(screen.getByText('ARCHIVED')).toBeInTheDocument();
  });

  it('无 DLE 记录时显示空态', async () => {
    mocks.getLogEvents.mockResolvedValue({
      plan_run_id: 103, data_authority: 'device_log_event', total: 0, items: [],
    });
    renderCard(103, true);
    await waitFor(() => expect(screen.getByText('无 device_log_event 记录')).toBeInTheDocument());
  });

  it('加载失败显示 InlineError 可重试', async () => {
    mocks.getLogEvents.mockRejectedValue(new Error('boom'));
    renderCard(103, true);
    await waitFor(() => expect(screen.getByText('日志事件归档加载失败')).toBeInTheDocument());
  });
});
