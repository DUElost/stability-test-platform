import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Table, TableEmptyRow } from './table';

describe('Table', () => {
  it('provides a discoverable overflow container', () => {
    render(
      <Table aria-label="测试表格">
        <tbody>
          <tr><td>内容</td></tr>
        </tbody>
      </Table>,
    );

    const table = screen.getByRole('table', { name: '测试表格' });
    expect(table.parentElement).toHaveAttribute('data-slot', 'table-scroll-container');
    expect(table.parentElement).toHaveClass('table-scrollbar', 'overflow-auto');
  });
});

describe('TableEmptyRow', () => {
  // 表格已有表头、只是没有行时用它。塞 EmptyState 进 tbody 会带进 py-16 + 64px
  // 图标，把一张空表撑成半屏——本轮（A1）收敛前 FileServerPage / UserTable 各写各的。
  it('横跨全部列，居中一行灰字', () => {
    render(
      <Table>
        <tbody>
          <TableEmptyRow colSpan={7}>暂无在线 host</TableEmptyRow>
        </tbody>
      </Table>,
    );

    const cell = screen.getByRole('cell', { name: '暂无在线 host' });
    expect(cell).toHaveAttribute('colspan', '7');
    expect(cell).toHaveClass('text-center', 'text-muted-foreground');
  });

  it('不传内容时给出通用兜底文案', () => {
    render(
      <Table>
        <tbody>
          <TableEmptyRow colSpan={3} />
        </tbody>
      </Table>,
    );
    expect(screen.getByRole('cell', { name: '暂无数据' })).toBeInTheDocument();
  });
});
