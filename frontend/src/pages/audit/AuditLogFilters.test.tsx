import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  auditList: vi.fn(),
  usersList: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    audit: { list: (...a: unknown[]) => mocks.auditList(...a) },
    users: { list: (...a: unknown[]) => mocks.usersList(...a) },
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

describe('AuditLogPage 筛选（#628）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.auditList.mockResolvedValue({
      items: [{
        id: 1,
        username: 'alice',
        action: 'create',
        resource_type: 'plan',
        resource_id: '42',
        ip_address: '10.62.8.2',
        timestamp: '2026-09-13T00:00:00Z',
      }],
      total: 1,
    });
    mocks.usersList.mockResolvedValue({
      items: [{ id: 1, username: 'alice', role: 'admin' }],
      total: 1,
    });
  });

  it('用户名带下拉候选，且未提交不发请求、blur 才带参数重查', async () => {
    renderPage();
    const ipInput = await screen.findByTestId('audit-ip-filter');

    // 候选来自 users 列表（admin 页面）
    await waitFor(() => {
      const option = document.querySelector('#audit-username-options option');
      expect(option?.getAttribute('value')).toBe('alice');
    });

    const callsBefore = mocks.auditList.mock.calls.length;
    fireEvent.change(ipInput, { target: { value: '10.62.8.2' } });
    // 逐键不发请求
    expect(mocks.auditList.mock.calls.length).toBe(callsBefore);

    fireEvent.blur(ipInput);
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0,
        50,
        expect.objectContaining({ ip_address: '10.62.8.2' }),
      ),
    );
  });

  it('用户名与资源 ID 走同样的 blur/Enter 提交语义', async () => {
    renderPage();
    const usernameInput = await screen.findByTestId('audit-username-filter');
    const resourceInput = screen.getByTestId('audit-resource-id-filter');

    fireEvent.change(usernameInput, { target: { value: 'alice' } });
    fireEvent.blur(usernameInput);
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0,
        50,
        expect.objectContaining({ username: 'alice' }),
      ),
    );

    fireEvent.change(resourceInput, { target: { value: '42' } });
    fireEvent.keyDown(resourceInput, { key: 'Enter' });
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0,
        50,
        expect.objectContaining({ username: 'alice', resource_id: '42' }),
      ),
    );
  });

  it('清空输入并 blur 后不再携带该筛选参数', async () => {
    renderPage();
    const ipInput = await screen.findByTestId('audit-ip-filter');

    fireEvent.change(ipInput, { target: { value: '10.62.8.2' } });
    fireEvent.blur(ipInput);
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0, 50, expect.objectContaining({ ip_address: '10.62.8.2' }),
      ),
    );

    fireEvent.change(ipInput, { target: { value: '' } });
    fireEvent.blur(ipInput);
    await waitFor(() => {
      const calls = mocks.auditList.mock.calls;
      const lastParams = calls[calls.length - 1]?.[2] as Record<string, string>;
      expect(lastParams.ip_address).toBeUndefined();
    });
  });
});
