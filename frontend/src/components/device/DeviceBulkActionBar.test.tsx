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
