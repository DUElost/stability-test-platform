import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DeviceBulkActionBar from './DeviceBulkActionBar';

const handlers = {
  onSelectAllFiltered: vi.fn(),
  onEditTags: vi.fn(),
  onCopySerials: vi.fn(),
  onExport: vi.fn(),
  onClear: vi.fn(),
};

describe('DeviceBulkActionBar', () => {
  it('renders nothing without a selection', () => {
    const { container } = render(
      <DeviceBulkActionBar
        selectedCount={0}
        filteredCount={10}
        selectedFilteredCount={0}
        {...handlers}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('#2600：计数带分母——页选（50）与全量（546）不再同形', () => {
    render(
      <DeviceBulkActionBar
        selectedCount={50}
        filteredCount={546}
        selectedFilteredCount={50}
        {...handlers}
      />,
    );
    const bar = screen.getByTestId('device-bulk-action-bar');
    // 只报「已选择 50 台设备」时看不出还有 496 台没选——分母必须写出来
    expect(bar).toHaveTextContent('已选择 50 / 546 台设备');
    expect(screen.getByTestId('device-select-all-filtered')).toHaveTextContent(
      '选择全部筛选结果 (546)',
    );
  });

  it('#2600：已全选筛选结果时不再提示补齐入口（分母仍在）', () => {
    render(
      <DeviceBulkActionBar
        selectedCount={546}
        filteredCount={546}
        selectedFilteredCount={546}
        {...handlers}
      />,
    );
    expect(screen.getByTestId('device-bulk-action-bar')).toHaveTextContent('已选择 546 / 546 台设备');
    expect(screen.queryByTestId('device-select-all-filtered')).not.toBeInTheDocument();
  });

  it('shows contextual actions and all-filtered selection', () => {
    const onSelectAllFiltered = vi.fn();
    render(
      <DeviceBulkActionBar
        selectedCount={2}
        filteredCount={12}
        selectedFilteredCount={2}
        statusSummary="空闲 1 · 离线 1"
        canEditTags
        {...handlers}
        onSelectAllFiltered={onSelectAllFiltered}
      />,
    );

    expect(screen.getByTestId('device-bulk-action-bar')).toHaveClass('fixed', 'bottom-4');
    expect(screen.getByText('空闲 1 · 离线 1')).toBeInTheDocument();
    expect(screen.getByTestId('device-bulk-tags')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('device-select-all-filtered'));
    expect(onSelectAllFiltered).toHaveBeenCalledOnce();
  });

});
