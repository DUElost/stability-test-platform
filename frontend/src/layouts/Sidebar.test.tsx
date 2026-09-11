/**
 * #1197 — 折叠分组内链接退出键盘焦点顺序（inert），展开后恢复。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({ data: { id: 1, username: 'tester', role: 'admin' } }),
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
