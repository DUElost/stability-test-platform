/**
 * #1197 — 移动抽屉关闭态整棵子树 inert；关闭后焦点返回触发按钮。
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AppShell from './AppShell';

vi.mock('./Sidebar', () => ({
  default: ({ onCloseMobile }: { onCloseMobile?: () => void }) => (
    <div data-testid="sidebar-stub">
      {onCloseMobile && (
        <button type="button" onClick={onCloseMobile}>关闭侧栏</button>
      )}
    </div>
  ),
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: () => ({ isConnected: true }),
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
});
