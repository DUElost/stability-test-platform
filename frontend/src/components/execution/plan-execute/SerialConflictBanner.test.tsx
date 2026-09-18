import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SerialConflictBanner } from './SerialConflictBanner';

describe('SerialConflictBanner (#2649)', () => {
  it('空列表不渲染', () => {
    const { container } = render(<SerialConflictBanner serials={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('展示冲突台数与示例序列号', () => {
    render(<SerialConflictBanner serials={['0123456789ABCDEF', '(no']} />);
    const banner = screen.getByTestId('serial-conflict-banner');
    expect(banner).toHaveTextContent('2 台');
    expect(banner).toHaveTextContent('0123456789ABCDEF');
    expect(banner).toHaveTextContent('序列号冲突');
    expect(banner).toHaveTextContent('拒绝执行');
  });

  it('超过 5 个序列号折叠示例并去重', () => {
    const serials = Array.from({ length: 7 }, (_, i) => `dup-serial-${i}`);
    render(<SerialConflictBanner serials={serials} />);
    const banner = screen.getByTestId('serial-conflict-banner');
    expect(banner).toHaveTextContent('7 台');
    expect(banner).toHaveTextContent('等');
    expect(banner.textContent).not.toContain('dup-serial-6');
  });
});
