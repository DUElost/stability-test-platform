import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

// 桩掉 LiveConsole（避免 xterm/socket 进 jsdom）
vi.mock('@/components/console/LiveConsole', () => ({
  default: ({ consoleRunId }: { consoleRunId: string }) => (
    <div data-testid="live-console-stub">{consoleRunId}</div>
  ),
}));

const startJiraRun = vi.fn();
const cancelRun = vi.fn();
vi.mock('@/utils/api/dedup', () => ({
  dedup: {
    startJiraRun: (...a: unknown[]) => startJiraRun(...a),
    cancelRun: (...a: unknown[]) => cancelRun(...a),
  },
}));

import JiraSubmitPanel from './JiraSubmitPanel';

function pickFile(name = 'Result.xls') {
  const input = screen.getByTestId('jira-file') as HTMLInputElement;
  const f = new File(['x'], name, { type: 'application/vnd.ms-excel' });
  fireEvent.change(input, { target: { files: [f] } });
}

describe('JiraSubmitPanel', () => {
  beforeEach(() => {
    startJiraRun.mockReset();
    cancelRun.mockReset();
  });

  it('renders vendor/stage/dry-run menu + file input + run button', () => {
    render(<JiraSubmitPanel />);
    expect(screen.getByTestId('jira-vendor')).toBeInTheDocument();
    expect(screen.getByTestId('jira-stage')).toBeInTheDocument();
    expect(screen.getByTestId('jira-dryrun')).toBeInTheDocument();
    expect(screen.getByTestId('jira-file')).toBeInTheDocument();
    expect(screen.getByTestId('jira-run-btn')).toBeInTheDocument();
  });

  it('create stage shows reporter input; upload_list hides it', () => {
    render(<JiraSubmitPanel />);
    // upload_list 默认：reporter 输入不显示
    expect(screen.queryByTestId('jira-reporter')).not.toBeInTheDocument();
    // 切到 create：reporter 显示
    fireEvent.change(screen.getByTestId('jira-stage'), { target: { value: 'create' } });
    expect(screen.getByTestId('jira-reporter')).toBeInTheDocument();
  });

  it('create stage with reporter passes it to startJiraRun', async () => {
    startJiraRun.mockResolvedValue({
      console_run_id: 'con-abc', room: 'console:con-abc', vendor: 'transsion', stage: 'create',
    });
    render(<JiraSubmitPanel />);
    fireEvent.change(screen.getByTestId('jira-stage'), { target: { value: 'create' } });
    fireEvent.change(screen.getByTestId('jira-reporter'), { target: { value: 'bob' } });
    pickFile('JIRA_Upload_List.xlsx');
    fireEvent.click(screen.getByTestId('jira-run-btn'));
    await waitFor(() => expect(startJiraRun).toHaveBeenCalledTimes(1));
    const [params] = startJiraRun.mock.calls[0];
    expect(params).toMatchObject({ vendor: 'transsion', stage: 'create', dryRun: true, reporter: 'bob' });
  });

  it('without file → error, does not call API', async () => {
    render(<JiraSubmitPanel />);
    fireEvent.click(screen.getByTestId('jira-run-btn'));
    expect(await screen.findByTestId('jira-error')).toBeInTheDocument();
    expect(startJiraRun).not.toHaveBeenCalled();
  });

  it('one-click with file calls startJiraRun (no runId/cookie) + renders LiveConsole', async () => {
    startJiraRun.mockResolvedValue({
      console_run_id: 'con-abc', room: 'console:con-abc', vendor: 'transsion', stage: 'create',
    });
    render(<JiraSubmitPanel />);
    fireEvent.change(screen.getByTestId('jira-stage'), { target: { value: 'create' } });
    pickFile('JIRA_Upload_List.xlsx');
    fireEvent.click(screen.getByTestId('jira-run-btn'));

    await waitFor(() => expect(startJiraRun).toHaveBeenCalledTimes(1));
    const [params] = startJiraRun.mock.calls[0];
    expect(params).toMatchObject({ vendor: 'transsion', stage: 'create', dryRun: true });
    expect(params.file).toBeInstanceOf(File);
    expect('runId' in params).toBe(false);
    expect('cookie' in params).toBe(false);
    expect(await screen.findByTestId('live-console-stub')).toHaveTextContent('con-abc');
  });

  it('surfaces API error', async () => {
    startJiraRun.mockRejectedValue(new Error('503 not configured'));
    render(<JiraSubmitPanel />);
    pickFile();
    fireEvent.click(screen.getByTestId('jira-run-btn'));
    expect(await screen.findByTestId('jira-error')).toHaveTextContent('503');
  });
});
