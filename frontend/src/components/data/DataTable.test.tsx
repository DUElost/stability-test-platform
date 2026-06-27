import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DataTable } from './DataTable';
import type { ColumnDef } from '@tanstack/react-table';

describe('DataTable', () => {
  interface Row { id: string; name: string; }
  const data: Row[] = [{ id: '1', name: 'Alpha' }, { id: '2', name: 'Beta' }];
  const columns: ColumnDef<Row>[] = [
    { accessorKey: 'id', header: 'ID' },
    { accessorKey: 'name', header: 'Name' },
  ];

  it('renders rows', () => {
    render(<DataTable data={data} columns={columns} getRowId={(r) => r.id} />);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('renders row actions', () => {
    render(
      <DataTable
        data={data}
        columns={columns}
        getRowId={(r) => r.id}
        rowActions={() => [{ label: 'View', onClick: vi.fn() }]}
      />,
    );
    expect(screen.getAllByLabelText('行操作')).toHaveLength(2);
  });

  it('calls onSelectionChange', () => {
    const onChange = vi.fn();
    render(
      <DataTable
        data={data}
        columns={columns}
        getRowId={(r) => r.id}
        selection="multiple"
        selectedKeys={new Set()}
        onSelectionChange={onChange}
      />,
    );
    fireEvent.click(screen.getAllByLabelText('选择行')[0]);
    expect(onChange).toHaveBeenCalledWith(new Set(['1']));
  });
});
