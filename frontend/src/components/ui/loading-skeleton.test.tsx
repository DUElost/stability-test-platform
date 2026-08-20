import { render } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PageSkeleton } from './loading-skeleton';

describe('PageSkeleton', () => {
  it('Block md/lg 输出固定高度占位（不开 className 口子）', () => {
    const { container } = render(
      <PageSkeleton>
        <PageSkeleton.Block size="md" />
        <PageSkeleton.Block size="lg" />
      </PageSkeleton>,
    );
    const blocks = container.querySelectorAll<HTMLElement>('.animate-pulse');
    expect(blocks).toHaveLength(2);
    expect(blocks[0].className).toContain('h-32');
    expect(blocks[1].className).toContain('h-64');
    expect(blocks[0].className).toContain('rounded-lg');
  });

  it('Block 默认 md', () => {
    const { container } = render(<PageSkeleton.Block />);
    expect(container.querySelector('.animate-pulse')?.className).toContain('h-32');
  });

  it('Cards 渲染 count 张卡占位', () => {
    const { container } = render(<PageSkeleton.Cards count={3} />);
    expect(container.querySelectorAll('.rounded-xl.border')).toHaveLength(3);
  });

  it('List 渲染 count 条列表项占位（图标块 + 双行）', () => {
    const { container } = render(<PageSkeleton.List count={2} />);
    const items = container.querySelectorAll('.flex.items-center.gap-3.p-4');
    expect(items).toHaveLength(2);
    expect(container.querySelectorAll('.h-10.w-10')).toHaveLength(2);
  });

  it('积木组合由 PageSkeleton 容器统一纵向堆叠', () => {
    const { container } = render(
      <PageSkeleton>
        <PageSkeleton.Block />
        <PageSkeleton.Block size="lg" />
      </PageSkeleton>,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('space-y-4');
    expect(root.children).toHaveLength(2);
  });
});
