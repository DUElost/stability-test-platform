import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ArchiveStatusCard from './ArchiveStatusCard';
import type { WatcherSignalLinkStats } from '@/utils/api/types';

const opsMetrics = {
  pruned_total: 2,
  local_disk_usage_pct: 42,
  spill_cycles: 0,
  spilled_total: 0,
};

const linkStatsHealthy: WatcherSignalLinkStats = {
  total_signals: 10,
  linked_signals: 8,
  unlinked_linkable: 2,
  signal_only_signals: 0,
  link_rate: 0.8,
  not_yet_archived: 1,
  unlinkable: 1,
  unlinked_fixable: 0,
  fixable_link_rate: 1.0,
};

const linkStatsBroken: WatcherSignalLinkStats = {
  ...linkStatsHealthy,
  unlinked_fixable: 2,
  fixable_link_rate: 0.8,
};

describe('ArchiveStatusCard (#528 link_stats)', () => {
  it('renders signal link health when link_stats present', () => {
    render(
      <ArchiveStatusCard opsMetrics={opsMetrics} linkStats={linkStatsHealthy} />,
    );
    expect(screen.getByTestId('signal-link-stats')).toBeInTheDocument();
    expect(screen.getByTestId('link-not-yet-archived')).toHaveTextContent('未归档 1');
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveTextContent('待修复 0');
  });

  it('highlights unlinked_fixable when link repair is needed', () => {
    render(
      <ArchiveStatusCard opsMetrics={opsMetrics} linkStats={linkStatsBroken} />,
    );
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveTextContent('待修复 2');
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveClass('text-destructive');
  });
});
