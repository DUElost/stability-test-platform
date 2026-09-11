/**
 * #823 — 终态动作展开后 LogPanel 挂载即应请求历史日志（原 enabled: active 永不请求）。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LogPanel } from './LogPanel';

const mocks = vi.hoisted(() => ({ getActionLog: vi.fn() }));

vi.mock('@/utils/api', () => ({
  api: { aiAssistant: { getActionLog: mocks.getActionLog } },
}));

function renderPanel(active: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <LogPanel actionId={7} active={active} />
    </QueryClientProvider>,
  );
  return qc;
}

function intervalOf(qc: QueryClient) {
  const opts = qc.getQueryCache().getAll()[0]?.options as { refetchInterval?: unknown } | undefined;
  return opts?.refetchInterval;
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom 未实现 Element.scrollTo；日志区自动滚动仅在真实浏览器有意义
  Element.prototype.scrollTo = vi.fn();
});

describe('LogPanel（#823）', () => {
  it('终态（active=false）挂载即请求一次且不轮询，历史日志可见', async () => {
    mocks.getActionLog.mockResolvedValue([{ seq: 1, stream: 'stdout', line: '历史日志行' }]);
    const qc = renderPanel(false);

    expect(await screen.findByText('历史日志行')).toBeInTheDocument();
    expect(mocks.getActionLog).toHaveBeenCalledWith(7);
    expect(intervalOf(qc)).toBe(false);
  });

  it('运行中（active=true）保持 2s 轮询', async () => {
    mocks.getActionLog.mockResolvedValue([]);
    const qc = renderPanel(true);

    await waitFor(() => expect(mocks.getActionLog).toHaveBeenCalledWith(7));
    expect(intervalOf(qc)).toBe(2000);
  });
});
