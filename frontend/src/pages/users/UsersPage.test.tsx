import { render, screen, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  usersList: vi.fn(),
  authMe: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    users: { list: (...a: unknown[]) => mocks.usersList(...a) },
    auth: { me: () => mocks.authMe() },
  },
  toApiError: (e: unknown) => ({ message: String(e) }),
}));

import UsersPage from './UsersPage';
import { ConfirmProvider } from '@/hooks/useConfirm';

function renderPage() {
  // 与线上一致：全局默认 refetchOnWindowFocus=false（QueryProvider），本页显式 opt-in
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <UsersPage />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
}

describe('UsersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.usersList.mockResolvedValue({ items: [], total: 0 });
    mocks.authMe.mockResolvedValue({ id: 1, username: 'admin', role: 'admin' });
  });

  it('#2369：可见性恢复时重取用户列表（本页无轮询、不在跨端同步域内）', async () => {
    renderPage();
    await waitFor(() => expect(mocks.usersList).toHaveBeenCalledTimes(1));

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    await act(async () => {
      // react-query 的 focusManager 监听 window 的 visibilitychange（query-core focusManager.ts）
      window.dispatchEvent(new Event('visibilitychange'));
    });

    await waitFor(() => expect(mocks.usersList).toHaveBeenCalledTimes(2));
  });

  // #3195（批次 B2 G3，#3497 §4）：旧行为＝固定单页 `list(0, 200)`，服务端 total 越过
  // 页大小时尾部行静默消失、且无任何提示。修复＝按服务端 total 翻页取全量。
  // 250 条 > 页大小 200 ⇒ 两页取全，最后一条必须出现。
  it('fetches all users across pages when the server total exceeds one page', async () => {
    const rows = Array.from({ length: 250 }, (_, i) => ({
      id: i + 1,
      username: `user-${i}`,
      role: 'user',
      is_active: 'Y',
      created_at: '2026-08-14T00:00:00',
      last_login: null,
    }));
    mocks.usersList.mockImplementation(async (skip = 0, limit = 50) => ({
      items: rows.slice(skip, skip + limit),
      total: rows.length,
      skip,
      limit,
    }));

    renderPage();

    expect(await screen.findByText('user-0')).toBeInTheDocument();
    expect(screen.getByText('user-249')).toBeInTheDocument();
    // 页大小 = 后端 `le` 上限 200；第二页偏移由已取条数驱动
    expect(mocks.usersList).toHaveBeenCalledWith(0, 200);
    expect(mocks.usersList).toHaveBeenCalledWith(200, 200);
  });
});
