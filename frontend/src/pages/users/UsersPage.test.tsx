import { render, waitFor, act } from '@testing-library/react';
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
});
