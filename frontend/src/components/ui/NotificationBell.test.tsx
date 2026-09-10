import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationBell } from './NotificationBell';
import { api } from '@/utils/api';

vi.mock('@/hooks/useSocketIO', () => ({ useSocketIO: vi.fn() }));

vi.mock('@/utils/api', () => ({
  api: {
    notifications: {
      unreadCount: vi.fn(),
      listLogs: vi.fn(),
      markAllRead: vi.fn(),
      markRead: vi.fn(),
    },
  },
}));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 0 } },
});
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={queryClient}>
    <MemoryRouter>{children}</MemoryRouter>
  </QueryClientProvider>
);

async function openBell() {
  fireEvent.click(screen.getByLabelText('通知'));
}

describe('NotificationBell (#1195)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    (api.notifications.unreadCount as ReturnType<typeof vi.fn>).mockResolvedValue({
      unread: 0,
    });
  });

  it('loads: shows 加载通知 not 暂无通知 while pending', async () => {
    (api.notifications.listLogs as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise(() => {}),
    );

    render(<NotificationBell />, { wrapper });
    await openBell();

    expect(await screen.findByText('加载通知…')).toBeTruthy();
    expect(screen.queryByText('暂无通知')).toBeNull();
  });

  it('error: shows failure + retry, never the empty state', async () => {
    (api.notifications.listLogs as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom'),
    );

    render(<NotificationBell />, { wrapper });
    await openBell();

    expect(await screen.findByText('通知加载失败')).toBeTruthy();
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
    expect(screen.queryByText('暂无通知')).toBeNull();
  });

  it('success-empty: still shows 暂无通知', async () => {
    (api.notifications.listLogs as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
    });

    render(<NotificationBell />, { wrapper });
    await openBell();

    expect(await screen.findByText('暂无通知')).toBeTruthy();
  });
});
