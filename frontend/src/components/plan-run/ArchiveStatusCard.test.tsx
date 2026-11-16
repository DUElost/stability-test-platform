import { render, screen, within } from '@testing-library/react';
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

const linkStatsNearPerfectBroken: WatcherSignalLinkStats = {
  ...linkStatsHealthy,
  total_signals: 250,
  linked_signals: 249,
  unlinked_linkable: 1,
  link_rate: 0.996,
  not_yet_archived: 0,
  unlinkable: 0,
  unlinked_fixable: 1,
  fixable_link_rate: 0.996,
};

describe('ArchiveStatusCard (#528 link_stats)', () => {
  it('renders signal link health as normal when unlinked_fixable is 0 and fixable_link_rate is 1.0', () => {
    render(
      <ArchiveStatusCard opsMetrics={opsMetrics} linkStats={linkStatsHealthy} />,
    );
    const section = screen.getByTestId('signal-link-stats');
    expect(section).toBeInTheDocument();
    expect(section.querySelector('.bg-success')).not.toBeNull();
    expect(within(section).getByText('100%')).toBeInTheDocument();
    expect(screen.getByTestId('link-not-yet-archived')).toHaveTextContent('未归档 1');
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveTextContent('待修复 0');
  });

  it('highlights unlinked_fixable when link repair is needed', () => {
    render(
      <ArchiveStatusCard opsMetrics={opsMetrics} linkStats={linkStatsBroken} />,
    );
    const section = screen.getByTestId('signal-link-stats');
    expect(section.querySelector('.bg-success')).toBeNull();
    expect(section.querySelector('.bg-destructive')).not.toBeNull();
    expect(within(section).getByText('80%')).toBeInTheDocument();
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveTextContent('待修复 2');
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveClass('text-destructive');
  });

  it('does not show normal color or 100% when fixable_link_rate is 0.996 and unlinked_fixable is 1', () => {
    render(
      <ArchiveStatusCard opsMetrics={opsMetrics} linkStats={linkStatsNearPerfectBroken} />,
    );
    const section = screen.getByTestId('signal-link-stats');
    expect(section.querySelector('.bg-success')).toBeNull();
    expect(section.querySelector('.bg-destructive')).not.toBeNull();
    expect(within(section).queryByText('100%')).toBeNull();
    expect(within(section).getByText('99%')).toBeInTheDocument();
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveTextContent('待修复 1');
    expect(screen.getByTestId('link-unlinked-fixable')).toHaveClass('text-destructive');
  });

  it('does not show normal color or 100% when unlinked_fixable > 0 even if rounded rate is 1.0', () => {
    render(
      <ArchiveStatusCard
        opsMetrics={opsMetrics}
        linkStats={{
          ...linkStatsNearPerfectBroken,
          total_signals: 20001,
          linked_signals: 20000,
          link_rate: 1.0,
          fixable_link_rate: 1.0,
        }}
      />,
    );
    const section = screen.getByTestId('signal-link-stats');
    expect(section.querySelector('.bg-success')).toBeNull();
    expect(section.querySelector('.bg-destructive')).not.toBeNull();
    expect(within(section).queryByText('100%')).toBeNull();
    expect(within(section).getByText('99%')).toBeInTheDocument();
  });

  it.each([
    { pct: 50, expectedClass: 'bg-success' },
    { pct: 75, expectedClass: 'bg-warning' },
    { pct: 95, expectedClass: 'bg-destructive' },
  ])('preserves HDD usage bar color for $pct%', ({ pct, expectedClass }) => {
    render(
      <ArchiveStatusCard
        opsMetrics={{ ...opsMetrics, local_disk_usage_pct: pct }}
        linkStats={linkStatsHealthy}
      />,
    );
    const hddRow = screen.getByText('HDD 使用率').parentElement;
    expect(hddRow).not.toBeNull();
    expect(hddRow?.querySelector(`.${expectedClass}`)).not.toBeNull();
    expect(within(hddRow as HTMLElement).getByText(`${pct}%`)).toBeInTheDocument();
  });
});
