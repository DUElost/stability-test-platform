/**
 * #823 — 终态动作展开后 LogPanel 挂载即应请求历史日志（原 enabled: active 永不请求）。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

  // #3496（B2-G8）：拉取失败不得显示成「暂无输出」——失败 ≠ 命令没有输出。
  it('加载失败显示加载失败与重试，不显示「暂无输出」', async () => {
    mocks.getActionLog.mockRejectedValue(new Error('log fetch failed'));
    renderPanel(false);

    expect(await screen.findByText('执行日志加载失败，暂无法判断输出。')).toBeInTheDocument();
    expect(screen.queryByText('暂无输出')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无输出，等待执行…')).not.toBeInTheDocument();

    // 重试入口真的重新取数（恢复后日志行可见）
    mocks.getActionLog.mockResolvedValue([{ seq: 1, stream: 'stdout', line: '恢复后的日志行' }]);
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('恢复后的日志行')).toBeInTheDocument();
  });

  it('成功且为空仍显示「暂无输出」（原空态语义不变）', async () => {
    mocks.getActionLog.mockResolvedValue([]);
    renderPanel(false);

    expect(await screen.findByText('暂无输出')).toBeInTheDocument();
  });
});
