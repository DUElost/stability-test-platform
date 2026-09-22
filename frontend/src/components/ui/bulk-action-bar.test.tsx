import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DeviceBulkActionBar from '@/components/device/DeviceBulkActionBar';
import HostBulkActionBar from '@/components/host/HostBulkActionBar';
import {
  BULK_BAR_INNER_CLASS,
  BULK_BAR_OUTER_CLASS,
  BULK_BAR_SPACER_CLASS,
  BulkBarSpacer,
} from './bulk-action-bar';

/**
 * #2614：悬浮批量条的几何规格只有一个来源。
 *
 * 钉的是「两页不得再各自漂移」（#360 里 `max-w-5xl` vs `max-w-4xl`、断点不一致的同一件事）：
 * 谁把类名抄回字面量、或只改一页，这里就红。
 */
describe('bulk-action-bar 共享规格', () => {
  it('两张表的批量条使用同一个外层与内层规格', () => {
    render(
      <DeviceBulkActionBar
        selectedCount={3}
        filteredCount={12}
        selectedFilteredCount={3}
        onSelectAllFiltered={vi.fn()}
        onEditTags={vi.fn()}
        onCopySerials={vi.fn()}
        onExport={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    const deviceBar = screen.getByTestId('device-bulk-action-bar');
    expect(deviceBar.className).toBe(BULK_BAR_OUTER_CLASS);
    expect((deviceBar.firstElementChild as HTMLElement).className).toBe(BULK_BAR_INNER_CLASS);

    render(
      <HostBulkActionBar
        counts={{ selected: 3, firstInstall: 2, reinstall: 1, hotUpdate: 2, flashPrereqs: 0 }}
        isAdmin
        onInstall={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    const hostBar = screen.getByTestId('host-bulk-action-bar');
    expect(hostBar.className).toBe(BULK_BAR_OUTER_CLASS);
    expect((hostBar.firstElementChild as HTMLElement).className).toBe(BULK_BAR_INNER_CLASS);
  });

  it('占位块是纯装饰、带 testid、并用共享高度', () => {
    render(<BulkBarSpacer testId="probe-table-selection-spacer" />);

    const spacer = screen.getByTestId('probe-table-selection-spacer');
    expect(spacer).toHaveAttribute('aria-hidden');
    expect(spacer.tagName).toBe('DIV');
    expect(spacer.className).toBe(BULK_BAR_SPACER_CLASS);
    // 覆盖带必须真的被让开：条体 ≈63–110px + bottom-4，占位取 160px
    expect(BULK_BAR_SPACER_CLASS).toContain('h-40');
  });
});
