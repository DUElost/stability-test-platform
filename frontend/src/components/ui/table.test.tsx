import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Table } from './table';

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
