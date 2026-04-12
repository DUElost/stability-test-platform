import { describe, expect, it } from 'vitest';

import { getLatestArtifact, getWorkflowDisplayStatus, isTerminalJobStatus, shouldPollJobData } from './taskDetailsState';

describe('taskDetailsState', () => {
  it('identifies terminal job statuses', () => {
    expect(isTerminalJobStatus('COMPLETED')).toBe(true);
    expect(isTerminalJobStatus('FAILED')).toBe(true);
    expect(isTerminalJobStatus('ABORTED')).toBe(true);
    expect(isTerminalJobStatus('RUNNING')).toBe(false);
  });

  it('polls only while active run is non-terminal', () => {
    expect(shouldPollJobData({ status: 'RUNNING' })).toBe(true);
    expect(shouldPollJobData({ status: 'PENDING' })).toBe(true);
    expect(shouldPollJobData({ status: 'COMPLETED' })).toBe(false);
    expect(shouldPollJobData(null)).toBe(false);
  });

  it('falls back to pending workflow status when no run exists', () => {
    expect(getWorkflowDisplayStatus({ status: 'FAILED' })).toBe('FAILED');
    expect(getWorkflowDisplayStatus(null)).toBe('PENDING');
  });

  it('returns the most recent artifact from report payload', () => {
    const artifact = getLatestArtifact({
      run: {
        artifacts: [
          {
            id: 1,
            name: 'first',
            storage_uri: 'file:///tmp/first.txt',
            size_bytes: 1,
            checksum: 'a',
            created_at: '2026-01-01T00:00:00Z',
          },
          {
            id: 2,
            name: 'last',
            storage_uri: 'file:///tmp/last.txt',
            size_bytes: 2,
            checksum: 'b',
            created_at: '2026-01-01T00:01:00Z',
          },
        ],
      },
    } as any);

    expect(artifact?.id).toBe(2);
    expect(getLatestArtifact(null)).toBeNull();
  });
});
