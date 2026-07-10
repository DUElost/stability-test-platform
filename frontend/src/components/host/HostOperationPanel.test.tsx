import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import HostOperationPanel from '@/components/host/HostOperationPanel';
import type { HostOpItem } from '@/hooks/useHostOperations';

vi.mock('@/components/console/LiveConsole', () => ({
  default: ({
    consoleRunId,
    onStatusChange,
  }: {
    consoleRunId: string;
    onStatusChange?: (s: string) => void;
  }) => (
    <div data-testid={`mock-live-console-${consoleRunId}`}>
      <button type="button" onClick={() => onStatusChange?.('SUCCESS')}>
        finish
      </button>
    </div>
  ),
}));

const ops: HostOpItem[] = [
  {
    hostId: 'h1',
    label: '172.21.8.103',
    kind: 'install',
    status: 'running',
    consoleRunId: 'con-1',
  },
  {
    hostId: 'h2',
    label: '172.21.8.116',
    kind: 'reinstall',
    status: 'success',
    consoleRunId: 'con-2',
  },
];

describe('HostOperationPanel', () => {
  it('shows summary counts and op rows', () => {
    render(
      <HostOperationPanel
        open
        ops={ops}
        onClose={vi.fn()}
        onTerminalStatus={vi.fn()}
      />,
    );
    expect(screen.getByTestId('host-operation-panel')).toBeInTheDocument();
    expect(screen.getByText('172.21.8.103')).toBeInTheDocument();
    expect(screen.getByText('172.21.8.116')).toBeInTheDocument();
    expect(screen.getByText('首次安装')).toBeInTheDocument();
    expect(screen.getByText('重新安装')).toBeInTheDocument();
  });

  it('forwards LiveConsole terminal status', () => {
    const onTerminal = vi.fn();
    render(
      <HostOperationPanel
        open
        ops={ops}
        onClose={vi.fn()}
        onTerminalStatus={onTerminal}
      />,
    );
    fireEvent.click(screen.getByTestId('mock-live-console-con-1').querySelector('button')!);
    expect(onTerminal).toHaveBeenCalledWith('h1', 'SUCCESS');
  });

  it('can collapse all consoles and keep them mounted hidden', () => {
    render(
      <HostOperationPanel
        open
        ops={ops}
        onClose={vi.fn()}
        onTerminalStatus={vi.fn()}
      />,
    );
    expect(screen.getByTestId('mock-live-console-con-1')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('host-op-collapse-all'));
    // still mounted (not removed from DOM) — parent uses hidden
    expect(screen.getByTestId('mock-live-console-con-1')).toBeInTheDocument();
    const row1 = screen.getByTestId('host-op-row-h1');
    // chevron should indicate collapsed (button still there)
    expect(row1.querySelector('button')).toBeTruthy();
  });

  it('expand all shows consoles for every host with consoleRunId', () => {
    render(
      <HostOperationPanel
        open
        ops={ops}
        onClose={vi.fn()}
        onTerminalStatus={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('host-op-collapse-all'));
    fireEvent.click(screen.getByTestId('host-op-expand-all'));
    expect(screen.getByTestId('mock-live-console-con-1')).toBeInTheDocument();
    expect(screen.getByTestId('mock-live-console-con-2')).toBeInTheDocument();
  });

  it('calls onClose', () => {
    const onClose = vi.fn();
    render(
      <HostOperationPanel
        open
        ops={ops}
        onClose={onClose}
        onTerminalStatus={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('关闭'));
    expect(onClose).toHaveBeenCalled();
  });
});
