/**
 * #1197 — 折叠分组内链接退出键盘焦点顺序（inert），展开后恢复。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';

const mocks = vi.hoisted(() => ({ role: 'admin' }));

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({ data: { id: 1, username: 'tester', role: mocks.role } }),
}));

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Sidebar onNavigate={vi.fn()} collapsed={false} />
    </MemoryRouter>,
  );
}

describe('Sidebar 折叠分组焦点顺序（#1197）', () => {
  it('折叠分组内容容器带 inert，展开后移除；链接仍在 DOM（不卸载）', () => {
    renderSidebar();

    // 初始态：无活动项的组默认折叠
    const groupToggle = screen
      .getAllByRole('button')
      .find((b) => b.getAttribute('aria-expanded') === 'false');
    expect(groupToggle).toBeTruthy();

    const content = groupToggle!.nextElementSibling as HTMLElement;
    expect(content).toHaveAttribute('inert');
    // 关键：链接仍是挂载状态（inert 只退焦点顺序，不卸载）——直接查 DOM 规避 jsdom 对 inert 的可见性推导差异
    expect(content.querySelectorAll('a').length).toBeGreaterThan(0);

    fireEvent.click(groupToggle!);
    expect(content).not.toHaveAttribute('inert');
  });
});

describe('admin-only 入口按角色隐藏（#2360）', () => {
  it('user 角色看不到 WiFi 资源池与文件服务器；admin 看得到', () => {
    // 折叠分组的链接仍在 DOM（#1197 只退焦点顺序），所以直接按文案查即可
    mocks.role = 'user';
    const { unmount } = renderSidebar();
    expect(screen.queryByText('WiFi 资源池')).not.toBeInTheDocument();
    expect(screen.queryByText('文件服务器')).not.toBeInTheDocument();
    unmount();

    mocks.role = 'admin';
    renderSidebar();
    expect(screen.getByText('WiFi 资源池')).toBeInTheDocument();
    expect(screen.getByText('文件服务器')).toBeInTheDocument();
  });
});

describe('Sidebar AI 助手固定入口交互（#4117 U1-3）', () => {
  it('移动端点击 AI 助手触发 onNavigate 收起抽屉，桌面收起态提供 title 提示', () => {
    const onNavigate = vi.fn();
    const { rerender } = render(
      <MemoryRouter initialEntries={['/']}>
        <Sidebar onNavigate={onNavigate} collapsed={false} isMobile />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: 'AI 助手' });
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledTimes(1);

    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Sidebar onNavigate={onNavigate} collapsed isMobile={false} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'AI 助手' })).toHaveAttribute('title', 'AI 助手');
  });
});

describe('Sidebar 核心组默认展开、仅低频更多功能默认折叠（#4117 PR 2）', () => {
  it('首页挂载时工作区、分析报告、资源均默认展开，仅更多功能默认折叠', () => {
    mocks.role = 'admin';
    renderSidebar();
    expect(screen.getByRole('button', { name: '工作区' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '分析报告' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '资源' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '更多功能' })).toHaveAttribute('aria-expanded', 'false');
  });
});
