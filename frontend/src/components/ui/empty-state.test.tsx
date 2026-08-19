import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EmptyState, InlineEmpty, SearchEmptyState } from './empty-state';

// 本轮（A1/B8）把全站五种空态画法收敛成三种形态。这些用例护的是「三种形态各自
// 长什么样」以及「它们不会互相退化」——形态一旦被改成同一个样子，页面级空态就会
// 被塞进表格和巴掌大的面板里，那正是收敛之前的状态。

describe('EmptyState', () => {
  it('渲染图标底座 + 标题 + 描述 + CTA', () => {
    render(
      <EmptyState
        title="还没有主机"
        description="添加您的第一台测试执行节点"
        icon={<svg data-testid="custom-icon" />}
        action={<button type="button">添加主机</button>}
      />,
    );

    expect(screen.getByText('还没有主机')).toBeInTheDocument();
    expect(screen.getByText('添加您的第一台测试执行节点')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加主机' })).toBeInTheDocument();

    // 圆形底座：裸描边图标在暗色画布上过轻，收敛时统一补上
    const well = screen.getByTestId('custom-icon').parentElement;
    expect(well).toHaveClass('rounded-full', 'bg-muted');
  });

  it('不传描述与 CTA 时只渲染标题', () => {
    render(<EmptyState title="暂无审计记录" />);
    expect(screen.getByText('暂无审计记录')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('未传图标时回落到默认图标，底座仍在', () => {
    const { container } = render(<EmptyState title="暂无数据" />);
    const well = container.querySelector('.rounded-full.bg-muted');
    expect(well).not.toBeNull();
    expect(well?.querySelector('svg')).not.toBeNull();
  });
});

describe('SearchEmptyState', () => {
  it('回显关键词，与 EmptyState 区分「筛掉了」和「本来就没有」', () => {
    render(<SearchEmptyState keyword="monkey" />);
    expect(screen.getByText('没有匹配的结果')).toBeInTheDocument();
    expect(screen.getByText(/monkey/)).toBeInTheDocument();
  });
});

describe('InlineEmpty', () => {
  it('默认形态是一行灰字，没有图标底座', () => {
    const { container } = render(<InlineEmpty>暂无 WiFi 资源池</InlineEmpty>);
    expect(screen.getByText('暂无 WiFi 资源池')).toBeInTheDocument();
    expect(container.querySelector('.rounded-full')).toBeNull();
    expect(container.querySelector('svg')).toBeNull();
  });

  it('chart 变体占住固定高度——否则数据到达时整块面板会往下弹', () => {
    render(<InlineEmpty chart testId="chart-empty">当前范围内暂无异常包名数据</InlineEmpty>);
    const el = screen.getByTestId('chart-empty');
    expect(el).toHaveClass('h-32', 'items-center');
    expect(el).not.toHaveClass('py-10');
  });

  it('bordered 变体补虚线描边，用于本身无边框的容器', () => {
    const { container } = render(<InlineEmpty bordered>空</InlineEmpty>);
    expect(container.firstChild).toHaveClass('border-dashed');
  });
});
