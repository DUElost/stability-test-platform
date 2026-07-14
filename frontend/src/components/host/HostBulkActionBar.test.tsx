import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import HostBulkActionBar from '@/components/host/HostBulkActionBar';

describe('HostBulkActionBar', () => {
  it('renders nothing when selected=0', () => {
    const { container } = render(
      <HostBulkActionBar
        counts={{ selected: 0, firstInstall: 0, reinstall: 0, hotUpdate: 0 }}
        isAdmin
        onInstall={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows first-install label and enables install when eligible', () => {
    const onInstall = vi.fn();
    render(
      <HostBulkActionBar
        counts={{ selected: 3, firstInstall: 2, reinstall: 0, hotUpdate: 1 }}
        isAdmin
        onInstall={onInstall}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByTestId('host-bulk-action-bar')).toHaveClass('fixed', 'bottom-4');
    const btn = screen.getByTestId('host-bulk-install');
    expect(btn).toHaveTextContent('首次安装 (2)');
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onInstall).toHaveBeenCalled();
  });

  it('shows mixed install label and disables hot-update', () => {
    render(
      <HostBulkActionBar
        counts={{ selected: 5, firstInstall: 2, reinstall: 1, hotUpdate: 2 }}
        isAdmin
        onInstall={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByTestId('host-bulk-install')).toHaveTextContent('安装 Agent (3)');
    expect(screen.getByText('首次安装 2 · 重新安装 1 · 在线 2')).toBeInTheDocument();
    const hot = screen.getByTestId('host-bulk-hot-update');
    expect(hot).toBeDisabled();
    expect(hot).toHaveAttribute(
      'title',
      expect.stringContaining('仅选择一台'),
    );
  });

  it('enables hot update for one selected online host', () => {
    const onHotUpdate = vi.fn();
    render(
      <HostBulkActionBar
        counts={{ selected: 1, firstInstall: 0, reinstall: 0, hotUpdate: 1 }}
        isAdmin
        onInstall={vi.fn()}
        onHotUpdate={onHotUpdate}
        onClear={vi.fn()}
      />,
    );

    const hot = screen.getByTestId('host-bulk-hot-update');
    expect(hot).not.toBeDisabled();
    fireEvent.click(hot);
    expect(onHotUpdate).toHaveBeenCalledOnce();
  });

  it('disables install when no installable hosts', () => {
    render(
      <HostBulkActionBar
        counts={{ selected: 2, firstInstall: 0, reinstall: 0, hotUpdate: 2 }}
        isAdmin
        onInstall={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByTestId('host-bulk-install')).toBeDisabled();
  });

  it('calls onClear', () => {
    const onClear = vi.fn();
    render(
      <HostBulkActionBar
        counts={{ selected: 1, firstInstall: 1, reinstall: 0, hotUpdate: 0 }}
        isAdmin
        onInstall={vi.fn()}
        onClear={onClear}
      />,
    );
    fireEvent.click(screen.getByTestId('host-bulk-clear'));
    expect(onClear).toHaveBeenCalled();
  });
});
