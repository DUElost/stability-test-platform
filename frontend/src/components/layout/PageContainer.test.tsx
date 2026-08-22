import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PageContainer } from './PageContainer';
import { LAYOUT } from '@/design-system/tokens';

// 宽度四档按内容**类型**分，判定树见
// docs/design/2026-08-21-frontend-page-shell-spec.md。
// 这些用例护的是「档位与实际生效的类互相对得上」——收敛前 fullBleed 布尔
// 优先于 width，PlanRunLogsPage 的 width="logs" 从未生效过也没人发现。

describe('PageContainer', () => {
  it('未传宽度时落到 content 档（默认档，拿不准就用它）', () => {
    const { container } = render(<PageContainer>content</PageContainer>);
    expect(screen.getByText('content')).toBeInTheDocument();
    expect((container.firstChild as HTMLElement).className).toContain('max-w-6xl');
  });

  it('四档各自映射到不同的宽度类', () => {
    const seen = new Map<string, string>();
    for (const w of ['form', 'content', 'wide', 'bleed'] as const) {
      const { container } = render(<PageContainer width={w}>x</PageContainer>);
      seen.set(w, (container.firstChild as HTMLElement).className);
    }
    expect(seen.get('form')).toContain('max-w-3xl');
    expect(seen.get('content')).toContain('max-w-6xl');
    // wide / bleed 都是 w-full，区别在内边距（见下一条）
    expect(seen.get('wide')).not.toContain('max-w-');
    expect(seen.get('bleed')).not.toContain('max-w-');
  });

  it('只有 bleed 去掉内边距，wide 保留', () => {
    const bleed = render(<PageContainer width="bleed">x</PageContainer>);
    const wide = render(<PageContainer width="wide">x</PageContainer>);
    expect((bleed.container.firstChild as HTMLElement).className)
      .not.toContain(LAYOUT.pagePadding.split(' ')[0]);
    expect((wide.container.firstChild as HTMLElement).className)
      .toContain(LAYOUT.pagePadding.split(' ')[0]);
  });

  it('档位枚举保持四档——加第五档前先说清它承载什么类型差异', () => {
    expect(Object.keys(LAYOUT.pageWidth).sort()).toEqual(['bleed', 'content', 'form', 'wide']);
  });

  it('scrollable=false removes overflow-auto', () => {
    const { container } = render(<PageContainer scrollable={false}>content</PageContainer>);
    const root = container.firstChild as HTMLElement;
    expect(root.className).not.toContain('overflow-auto');
  });
});
