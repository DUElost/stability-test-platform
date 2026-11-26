/**
 * #1197 — 移动抽屉关闭态整棵子树 inert；关闭后焦点返回触发按钮。
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AppShell from './AppShell';
import { DASHBOARD_SUBSCRIPTION } from '@/config';

const { mockUseSocketIO } = vi.hoisted(() => ({
  mockUseSocketIO: vi.fn(() => ({ isConnected: true })),
}));

vi.mock('./Sidebar', () => ({
  default: ({
    onCloseMobile,
    collapsed,
    onToggleCollapse,
  }: {
    onCloseMobile?: () => void;
    collapsed?: boolean;
    onToggleCollapse?: () => void;
  }) => (
    <div data-testid="sidebar-stub" data-collapsed={String(Boolean(collapsed))}>
      {onCloseMobile && (
        <button type="button" onClick={onCloseMobile}>关闭侧栏</button>
      )}
      {onToggleCollapse && (
        <button type="button" onClick={onToggleCollapse}>
          {collapsed ? '展开侧边栏' : '收起侧边栏'}
        </button>
      )}
    </div>
  ),
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: mockUseSocketIO,
  disconnectDashSocket: vi.fn(),
}));
vi.mock('@/hooks/useCrossClientSync', () => ({ useCrossClientSync: vi.fn() }));
vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({ data: { id: 1, username: 'tester', role: 'admin' } }),
}));
vi.mock('@/components/ui/NotificationBell', () => ({ NotificationBell: () => null }));
vi.mock('@/components/ui/UserMenu', () => ({ UserMenu: () => null }));
vi.mock('@/components/ui/ThemeToggle', () => ({ ThemeToggle: () => null }));
vi.mock('@/utils/api', () => ({ api: { auth: { logout: vi.fn() } } }));

function renderShell() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<div>内容区</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // isMobile = window.innerWidth < 1024（AppShell 挂载时判定）
  Object.defineProperty(window, 'innerWidth', { value: 800, writable: true, configurable: true });
});

describe('AppShell 移动抽屉焦点顺序（#1197）', () => {
  it('关闭态抽屉整棵子树 inert，打开后移除', () => {
    renderShell();
    const drawer = screen.getByTestId('mobile-drawer');
    expect(drawer).toHaveAttribute('inert');

    fireEvent.click(screen.getByRole('button', { name: '打开侧边栏' }));
    expect(drawer).not.toHaveAttribute('inert');
  });

  it('经关闭按钮收起后焦点回到触发按钮', () => {
    renderShell();
    const toggle = screen.getByRole('button', { name: '打开侧边栏' });
    fireEvent.click(toggle);

    const drawer = screen.getByTestId('mobile-drawer');
    const closeBtn = within(drawer).getByRole('button', { name: '关闭侧栏' });
    closeBtn.focus();
    expect(document.activeElement).toBe(closeBtn);

    fireEvent.click(closeBtn);
    expect(drawer).toHaveAttribute('inert');
    expect(document.activeElement).toBe(toggle);
  });

  it('挂载即调用 useSocketIO(DASHBOARD_SUBSCRIPTION) 发起 dash socket 连接（#3970）', () => {
    renderShell();
    expect(mockUseSocketIO).toHaveBeenCalledWith(
      DASHBOARD_SUBSCRIPTION,
      expect.objectContaining({ trackLastMessage: false }),
    );
  });
});

describe('AppShell 紧凑顶栏、静音连接指示器与侧栏持久化（#4117 PR 2）', () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.defineProperty(window, 'innerWidth', { value: 1440, writable: true, configurable: true });
    mockUseSocketIO.mockReturnValue({ isConnected: true });
  });

  it('常态连接为紧凑状态圆点（无宽胶囊），断线展开红色已断开徽标', () => {
    const { rerender } = renderShell();
    const indicator = screen.getByTestId('ws-connection-status');
    expect(indicator).toHaveAttribute('title', '实时数据通道已连接');
    expect(indicator.textContent).toBe('');

    mockUseSocketIO.mockReturnValue({ isConnected: false });
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<div>内容区</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('ws-connection-status')).toHaveTextContent('已断开');
  });

  it('桌面侧栏折叠态持久化到 localStorage 且不再渲染中部悬浮展开圆钮', () => {
    const { unmount } = renderShell();
    const toggleBtn = screen.getByRole('button', { name: '收起侧边栏' });
    fireEvent.click(toggleBtn);
    expect(window.localStorage.getItem('stp_sidebar_collapsed')).toBe('1');
    // 仅存在侧栏内部展开按钮，不再有悬浮覆盖内容区的第二枚按钮
    expect(screen.getAllByRole('button', { name: '展开侧边栏' })).toHaveLength(1);
    unmount();

    // 重新挂载应从 localStorage 恢复折叠态
    renderShell();
    expect(screen.getAllByTestId('sidebar-stub')[0]).toHaveAttribute('data-collapsed', 'true');
  });
});
