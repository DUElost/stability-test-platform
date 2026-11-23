import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SearchableSelect, type SearchableSelectOption } from './searchable-select';

const OPTIONS: SearchableSelectOption[] = [
  { value: 'alpha', label: '项目 Alpha', count: 12 },
  { value: 'beta', label: '项目 Beta', count: 5 },
  { value: 'gamma', label: '项目 Gamma', count: 0 },
];

function ControlledSelect({ onChangeSpy }: { onChangeSpy?: (value: string) => void }) {
  const [value, setValue] = useState('all');
  return (
    <div data-testid="overflow-wrapper" className="overflow-hidden">
      <SearchableSelect
        value={value}
        onChange={(next) => {
          setValue(next);
          onChangeSpy?.(next);
        }}
        options={OPTIONS}
        allLabel="全部项目"
        ariaLabel="按项目筛选"
        testId="project-select"
      />
    </div>
  );
}

describe('SearchableSelect（D1 Portal 定位 + 键盘导航）', () => {
  it('浮层通过 Portal 挂载到 document.body，不被父级 overflow-hidden 裁切', () => {
    render(<ControlledSelect />);
    const wrapper = screen.getByTestId('overflow-wrapper');
    const trigger = screen.getByTestId('project-select');

    fireEvent.click(trigger);

    const listbox = screen.getByRole('listbox', { name: '按项目筛选' });
    expect(listbox).toBeInTheDocument();
    expect(wrapper.contains(listbox)).toBe(false);
    expect(document.body.contains(listbox)).toBe(true);
    expect(listbox.className).toContain('ring-border/50');
    expect(listbox.className).not.toContain('ring-black/5');
  });

  it('支持键盘 ArrowDown / ArrowUp / End / Home / Enter 选中与 Escape 关闭', () => {
    const onChangeSpy = vi.fn();
    render(<ControlledSelect onChangeSpy={onChangeSpy} />);
    const trigger = screen.getByTestId('project-select');

    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const listbox = screen.getByRole('listbox');
    expect(listbox).toBeInTheDocument();

    // 默认高亮第 0 项（全部项目），ArrowDown 到 alpha，再 ArrowDown 到 beta，Enter 选中
    fireEvent.keyDown(listbox, { key: 'ArrowDown' });
    fireEvent.keyDown(listbox, { key: 'ArrowDown' });
    fireEvent.keyDown(listbox, { key: 'Enter' });

    expect(onChangeSpy).toHaveBeenCalledWith('beta');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    // 重新打开后按 End 高亮最后一项，再按 Escape 关闭且不改变选中值
    fireEvent.click(trigger);
    const reopened = screen.getByRole('listbox');
    fireEvent.keyDown(reopened, { key: 'End' });
    const options = screen.getAllByRole('option');
    expect(options[options.length - 1]).toHaveAttribute('data-highlighted', 'true');

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(onChangeSpy).toHaveBeenCalledTimes(1);
  });

  it('搜索过滤后回车选中唯一匹配项，且支持一键清除', () => {
    const onChangeSpy = vi.fn();
    render(<ControlledSelect onChangeSpy={onChangeSpy} />);
    const trigger = screen.getByTestId('project-select');

    fireEvent.click(trigger);
    const searchInput = screen.getByPlaceholderText('输入关键字过滤...');
    fireEvent.change(searchInput, { target: { value: 'gamma' } });

    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(searchInput, { key: 'Enter' });
    expect(onChangeSpy).toHaveBeenLastCalledWith('gamma');

    fireEvent.click(screen.getByRole('button', { name: '清除按项目筛选' }));
    expect(onChangeSpy).toHaveBeenLastCalledWith('all');
  });
});
