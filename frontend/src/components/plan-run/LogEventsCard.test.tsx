/**
 * #529 — LogEventsCard：终态 PlanRun 的 DLE 事件视图（归档权威）。
 * 只读 device_log_event 端点；RUNNING 不触发；路径优先 remote_path。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import LogEventsCard from './LogEventsCard';
import realPayload from './__fixtures__/log-events-103.json';
import { SLOW_REFETCH_MS } from '@/hooks/plan-run/planRunDetailUtils';
import { planRunKeys } from '@/utils/api/queryKeys';

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
  const view = render(
    <QueryClientProvider client={qc}>
      <LogEventsCard runId={runId} isTerminal={isTerminal} />
    </QueryClientProvider>,
  );
  return { ...view, queryClient: qc };
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

  it('超过单页时显示计数与「加载更多」，点击后放大窗口（#1194）', async () => {
    const item = (i: number) => ({
      id: `ev-${i}`,
      serial: '0000NX2622000514',
      platform: 'MTK',
      event_type: 'AEE',
      event_subtype: 'NE',
      state: 'REMOTE',
      local_path: `/mnt/hdd/aee_events/103/ev-${i}`,
      remote_path: `/mnt/stp-aee/devices/103/ev-${i}`,
      detected_at: '2026-07-25T17:57:07+08:00',
      device_timestamp: null,
      job_id: 1001,
      host_id: 'h1',
      signal_seq_no: null,
    });
    mocks.getLogEvents
      .mockResolvedValueOnce({
        plan_run_id: 103,
        data_authority: 'device_log_event',
        total: 201,
        items: Array.from({ length: 200 }, (_, i) => item(i)),
      })
      .mockResolvedValueOnce({
        plan_run_id: 103,
        data_authority: 'device_log_event',
        total: 201,
        items: Array.from({ length: 201 }, (_, i) => item(i)),
      });

    renderCard(103, true);

    expect(await screen.findByTestId('log-events-count')).toHaveTextContent('已显示 200 / 201');
    fireEvent.click(screen.getByRole('button', { name: /加载更多/ }));

    await waitFor(() => expect(mocks.getLogEvents).toHaveBeenLastCalledWith(103, { skip: 0, limit: 400 }));
    await waitFor(() => expect(screen.queryByRole('button', { name: /加载更多/ })).not.toBeInTheDocument());
    expect(screen.getByTestId('log-events-count')).toHaveTextContent('已显示 201 / 201');
  });

  it('终态查询挂载慢轮询（#1193：后处理产物延迟到达仍可持续可见）', async () => {
    mocks.getLogEvents.mockResolvedValue(realPayload);
    const { queryClient } = renderCard(103, true);
    await waitFor(() => expect(mocks.getLogEvents).toHaveBeenCalled());

    const query = queryClient
      .getQueryCache()
      .find({ queryKey: planRunKeys.logEvents(103, { limit: 200 }) });
    const options = query?.options as { refetchInterval?: number | false } | undefined;
    expect(options?.refetchInterval).toBe(SLOW_REFETCH_MS);
  });

  it('加载到接口上限后停止放大并明示截断（#1194 复查）', async () => {
    mocks.getLogEvents.mockResolvedValue({
      plan_run_id: 103,
      data_authority: 'device_log_event',
      total: 600,
      items: [
        {
          id: 'ev-0',
          serial: '0000NX2622000514',
          platform: 'MTK',
          event_type: 'AEE',
          event_subtype: 'NE',
          state: 'REMOTE',
          local_path: '/mnt/hdd/aee_events/103/ev-0',
          remote_path: '/mnt/stp-aee/devices/103/ev-0',
          detected_at: '2026-07-25T17:57:07+08:00',
          device_timestamp: null,
          job_id: 1001,
          host_id: 'h1',
          signal_seq_no: null,
        },
      ],
    });
    renderCard(103, true);
    expect(await screen.findByTestId('log-events-count')).toHaveTextContent('已显示 1 / 600');

    fireEvent.click(screen.getByRole('button', { name: /加载更多/ }));
    await waitFor(() =>
      expect(mocks.getLogEvents).toHaveBeenLastCalledWith(103, { skip: 0, limit: 400 }),
    );
    // 换查询键后旧数据不可见，卡片短暂回加载态：等新窗口数据回填再点
    await screen.findByRole('button', { name: /加载更多/ });

    fireEvent.click(screen.getByRole('button', { name: /加载更多/ }));

    // 600 超过服务端上限 500：封顶到 500，不请求 600
    await waitFor(() =>
      expect(mocks.getLogEvents).toHaveBeenLastCalledWith(103, { skip: 0, limit: 500 }),
    );
    await waitFor(() =>
      expect(screen.getByText(/已达接口单次上限 500 条（共 600 条）/)).toBeInTheDocument(),
    );
    expect(screen.queryByRole('button', { name: /加载更多/ })).not.toBeInTheDocument();
  });
});
