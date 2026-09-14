import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LoginPage from './LoginPage';
import { ThemeProvider } from '@/contexts/ThemeContext';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  login: vi.fn(),
  clearAppQueryCache: vi.fn(),
  queryClient: {
    removeQueries: vi.fn(),
    clear: vi.fn(),
  },
  location: { state: null as unknown },
  searchParams: new URLSearchParams(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useLocation: () => mocks.location,
    useSearchParams: () => [mocks.searchParams] as const,
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
    mocks.location.state = null;
    mocks.searchParams = new URLSearchParams();
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
        onchange: null,
      })),
    );
  });

  it('clears cached queries before navigating after successful login', async () => {
    mocks.login.mockResolvedValue({ ok: true });

    render(
      <ThemeProvider>
        <LoginPage />
      </ThemeProvider>,
    );

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
    expect(mocks.navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  it('redirects back to the deep link carried in router state after login', async () => {
    mocks.login.mockResolvedValue({ ok: true });
    mocks.location.state = { from: '/execution/plan-runs/375/logs' };

    render(
      <ThemeProvider>
        <LoginPage />
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs/375/logs', {
        replace: true,
      });
    });
  });

  it('ignores non in-app redirect targets (open-redirect guard)', async () => {
    mocks.login.mockResolvedValue({ ok: true });
    mocks.location.state = { from: 'https://evil.example.com/phish' };

    render(
      <ThemeProvider>
        <LoginPage />
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/', { replace: true });
    });
  });

  // client.ts 401 硬跳转无法携带 router state，深链经 ?next= 回跳
  it('redirects back via ?next= query param after hard 401 redirect', async () => {
    mocks.login.mockResolvedValue({ ok: true });
    mocks.searchParams = new URLSearchParams([
      ['next', '/execution/plan-runs/375/logs'],
    ]);

    render(
      <ThemeProvider>
        <LoginPage />
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/execution/plan-runs/375/logs', {
        replace: true,
      });
    });
  });

  it('rejects protocol-relative ?next= targets', async () => {
    mocks.login.mockResolvedValue({ ok: true });
    mocks.searchParams = new URLSearchParams([['next', '//evil.example.com']]);

    render(
      <ThemeProvider>
        <LoginPage />
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/', { replace: true });
    });
  });

  it('prefers router state.from over ?next= when both present', async () => {
    mocks.login.mockResolvedValue({ ok: true });
    mocks.location.state = { from: '/devices' };
    mocks.searchParams = new URLSearchParams([['next', '/hosts']]);

    render(
      <ThemeProvider>
        <LoginPage />
      </ThemeProvider>,
    );

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/devices', { replace: true });
    });
  });
});
