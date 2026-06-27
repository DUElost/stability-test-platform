import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DataList } from './DataList';

describe('DataList', () => {
  const items = [
    { id: '1', name: 'Alpha' },
    { id: '2', name: 'Beta' },
  ];

  it('renders items', () => {
    render(
      <DataList
        items={items}
        keyExtractor={(i) => i.id}
        renderItem={(item) => <div>{item.name}</div>}
      />,
    );
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('shows empty state', () => {
    render(
      <DataList
        items={[] as { id: string; name: string }[]}
        keyExtractor={(i) => i.id}
        renderItem={(item) => <div>{item.name}</div>}
      />,
    );
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('calls onSelectionChange', () => {
    const onChange = vi.fn();
    render(
      <DataList
        items={items}
        keyExtractor={(i) => i.id}
        renderItem={(item, ctx) => (
          <button onClick={ctx.toggleSelect}>{item.name}</button>
        )}
        selection="multiple"
        selectedKeys={new Set()}
        onSelectionChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText('Alpha'));
    expect(onChange).toHaveBeenCalledWith(new Set(['1']));
  });
});
