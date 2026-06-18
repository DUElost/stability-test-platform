import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import RunLogArchiveSection from './RunLogArchiveSection';
import type { WatcherArchive } from '@/utils/api/types';

const { getWatcherSummary } = vi.hoisted(() => ({
  getWatcherSummary: vi.fn(),
}));

vi.mock('@/utils/api/planRuns', () => ({
  planRuns: { getWatcherSummary },
}));

function makeArchive(overrides: Partial<WatcherArchive> = {}): WatcherArchive {
  return {
    archived_jobs: 2,
    total_jobs: 5,
    bundles: [
      {
        job_id: 101,
        artifact_id: 1,
        size_bytes: 1024,
        storage_uri: '/nfs/a/101.tar.gz',
      },
    ],
    bundles_total: 3,
    bundles_limit: 1,
    bundles_offset: 0,
    ...overrides,
  };
}

describe('RunLogArchiveSection', () => {
  beforeEach(() => {
    getWatcherSummary.mockReset();
  });

  it('renders progress and bundle rows', () => {
    render(<RunLogArchiveSection archive={makeArchive()} />);
    expect(screen.getByTestId('archive-progress')).toHaveTextContent('2/5');
    expect(screen.getByTestId('archive-bundle-row')).toHaveTextContent('Job #101');
  });

  it('shows load more when bundles_total exceeds rendered count', () => {
    render(<RunLogArchiveSection archive={makeArchive()} runId={42} timeScope="all" />);
    expect(screen.getByTestId('archive-load-more')).toBeInTheDocument();
  });

  it('loads next page on load more click', async () => {
    getWatcherSummary.mockResolvedValue({
      archive: {
        ...makeArchive(),
        bundles: [
          {
            job_id: 102,
            artifact_id: 2,
            size_bytes: 2048,
            storage_uri: '/nfs/a/102.tar.gz',
          },
        ],
        bundles_offset: 1,
      },
    });

    render(<RunLogArchiveSection archive={makeArchive()} runId={42} timeScope="all" />);
    fireEvent.click(screen.getByTestId('archive-load-more'));

    await waitFor(() => {
      expect(getWatcherSummary).toHaveBeenCalledWith(42, 'all', {
        archive_offset: 1,
        archive_limit: 1,
      });
    });
    expect(screen.getAllByTestId('archive-bundle-row')).toHaveLength(2);
  });

  it('clears loaded extras when parent refetches first page', async () => {
    getWatcherSummary.mockResolvedValue({
      archive: {
        ...makeArchive(),
        bundles: [
          {
            job_id: 102,
            artifact_id: 2,
            size_bytes: 2048,
            storage_uri: '/nfs/a/102.tar.gz',
          },
        ],
        bundles_offset: 1,
      },
    });

    const { rerender } = render(
      <RunLogArchiveSection archive={makeArchive()} runId={42} timeScope="all" />,
    );
    fireEvent.click(screen.getByTestId('archive-load-more'));
    await waitFor(() => {
      expect(screen.getAllByTestId('archive-bundle-row')).toHaveLength(2);
    });

    rerender(
      <RunLogArchiveSection
        archive={makeArchive({
          bundles: [
            {
              job_id: 103,
              artifact_id: 3,
              size_bytes: 4096,
              storage_uri: '/nfs/a/103.tar.gz',
            },
          ],
          archived_jobs: 3,
          bundles_total: 3,
        })}
        runId={42}
        timeScope="all"
      />,
    );

    expect(screen.getAllByTestId('archive-bundle-row')).toHaveLength(1);
    expect(screen.getByTestId('archive-bundle-row')).toHaveTextContent('Job #103');
  });
});
