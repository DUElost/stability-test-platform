import { render, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  auditList: vi.fn(),
  auditFacets: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    audit: {
      list: (...a: unknown[]) => mocks.auditList(...a),
      facets: () => mocks.auditFacets(),
    },
  },
}));

import AuditLogPage from './AuditLogPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuditLogPage />
    </QueryClientProvider>,
  );
}

describe('AuditLogPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.auditFacets.mockResolvedValue({ resource_types: [], actions: [] });
    mocks.auditList.mockResolvedValue({
      items: [{
        id: 1,
        action: 'create',
        resource_type: 'plan',
        resource_id: 1,
        timestamp: '2026-09-13T00:00:00Z',
      }],
      total: 1,
    });
  });

  it('narrow-viewport 下保留表格高度下限与外层兜底滚动（#750）', async () => {
    const { container } = renderPage();

    // 表格分支渲染完成（数据到达）后再断言结构——「创建」同时出现在筛选下拉里，
    // 不能作为表格已渲染的判据，故直接等表格区出现。
    const page = container.firstElementChild as HTMLElement;
    const tableArea = await waitFor(() => {
      const el = page.querySelector('[class*="min-h-[240px]"]') as HTMLElement | null;
      if (!el) throw new Error('table area not rendered yet');
      return el;
    });

    // 外层兜底：AppShell main 为 overflow-hidden，页头+筛选行高于视口时只有这里能滚
    expect(page.className).toContain('overflow-auto');
    expect(tableArea.className).toContain('overflow-auto');
  });

  it('#2369：可见性恢复时重取审计日志（本页无轮询、不在跨端同步域内）', async () => {
    renderPage();
    await waitFor(() => expect(mocks.auditList).toHaveBeenCalledTimes(1));

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    await act(async () => {
      // react-query 的 focusManager 监听 window 的 visibilitychange（query-core focusManager.ts）
      window.dispatchEvent(new Event('visibilitychange'));
    });

    await waitFor(() => expect(mocks.auditList).toHaveBeenCalledTimes(2));
  });
});
