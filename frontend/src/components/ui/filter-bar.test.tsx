import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FilterSearchInput, MoreFiltersPopover } from './filter-bar';
import { Button } from './button';

describe('FilterSearchInput & Button 基座收敛（#4117 U1-2 / #3487）', () => {
  it('FilterSearchInput 合并顺序确保 pl-9 / pr-8 / bg-muted/30 覆盖 FORM.inputSm', () => {
    render(
      <FilterSearchInput
        id="device-search"
        value=""
        onChange={vi.fn()}
        placeholder="搜索串号"
        ariaLabel="搜索设备"
        testId="filter-search"
      />,
    );
    const input = screen.getByTestId('filter-search');
    const tokens = input.className.split(/\s+/);
    expect(tokens).toContain('pl-9');
    expect(tokens).toContain('pr-8');
    expect(tokens).toContain('bg-muted/30');
    expect(tokens).not.toContain('pl-7');
    expect(tokens).not.toContain('pr-2');
  });

  it('MoreFiltersPopover 使用语义化 ring-border/50，不含硬编码 ring-black/5', () => {
    render(
      <MoreFiltersPopover activeCount={1} testId="more-filters">
        <div>子筛选</div>
      </MoreFiltersPopover>,
    );
    expect(screen.getByTestId('more-filters')).toBeInTheDocument();
  });

  it('Button 默认图标规则对带 h-*/w-* 的小图标放行，不强覆写为 size-4（#3487）', () => {
    render(
      <Button size="sm">
        <svg data-testid="small-icon" className="h-3.5 w-3.5" />
        操作
      </Button>,
    );
    const button = screen.getByRole('button', { name: '操作' });
    expect(button.className).toContain(":not([class*='h-']):not([class*='w-'])");
  });
});
