import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LoginPage from './LoginPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  login: vi.fn(),
  clearAppQueryCache: vi.fn(),
  queryClient: {
    removeQueries: vi.fn(),
    clear: vi.fn(),
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
  };
});

vi.mock('@tanstack/react-query', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-query')>('@tanstack/react-query');
  return {
    ...actual,
    useQueryClient: () => mocks.queryClient,
  };
});

vi.mock('@/utils/api', () => ({
  api: {
    auth: {
      login: mocks.login,
    },
  },
}));

vi.mock('@/components/QueryProvider', () => ({
  clearAppQueryCache: mocks.clearAppQueryCache,
}));

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clears cached queries before navigating after successful login', async () => {
    mocks.login.mockResolvedValue({ data: { ok: true } });

    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(mocks.login).toHaveBeenCalledWith('alice', 'secret');
    });
    await waitFor(() => {
      expect(mocks.clearAppQueryCache).toHaveBeenCalledTimes(1);
    });
    expect(mocks.navigate).toHaveBeenCalledWith('/');
  });
});
