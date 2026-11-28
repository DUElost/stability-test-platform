import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import HandoffWorkbench from './HandoffWorkbench';
import { api } from '@/utils/api';
import type { HandoffBundleOut, HandoffPreflightOut } from '@/utils/api/types';

vi.mock('@/utils/api', () => ({
  api: {
    jiraHandoff: {
      preflight: vi.fn(),
      createBundle: vi.fn(),
      listBundles: vi.fn(),
      getBundle: vi.fn(),
      downloadArtifact: vi.fn(),
      artifactDownloadUrl: vi.fn(),
    },
  },
}));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
});
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

function makePreflight(over: Partial<HandoffPreflightOut> = {}): HandoffPreflightOut {
  return {
    platform: 'mtk',
    snapshot_cutoff: '2026-11-27T00:00:00Z',
    first_run_started_at: '2026-11-20T00:00:00Z',
    runs: [
      {
        plan_run_id: 1594,
        plan_name: 'Monkey',
        status: 'SUCCEEDED',
        partition_mode: 'CURRENT',
        started_at: '2026-11-20T00:00:00Z',
        ended_at: '2026-11-21T00:00:00Z',
        project_key: 'P1',
        jira_project_key: 'TINNO',
        hosts_expected: ['h1', 'h2'],
        hosts_with_reports: ['h1'],
        missing_hosts: ['h2'],
        unexpected_hosts: [],
        mapping_status: 'partial',
      },
    ],
    identity_gate: {
      total_org_rows: 10,
      unique_events: 0,
      keyed_rows: 0,
      compat_fallback_rows: 0,
      unbound_rows: 2,
      after_cutoff_skipped_rows: 0,
      unbound_by_reason: { missing_dle: 2 },
    },
    unbound_rows: [
      {
        plan_run_id: 1594,
        host_id: 'h1',
        report_file: 'h1_Result_org.xls',
        row_no: 3,
        serial: 'SN001',
        raw_path: '/x',
        reason: 'missing_dle',
      },
      {
        plan_run_id: 1594,
        host_id: 'h1',
        report_file: 'h1_Result_org.xls',
        row_no: 4,
        serial: 'SN002',
        raw_path: '/y',
        reason: 'cur_process_none',
      },
    ],
    builds: [{ build: 'V78', product: 'X', device_count: 1, unique_event_count: 0 }],
    gates: {
      coverage_complete: false,
      identity_degraded: true,
      blocking_issues: ['missing hosts: h2'],
      warnings: ['identity degraded'],
    },
    ...over,
  };
}

function makeBundle(over: Partial<HandoffBundleOut> = {}): HandoffBundleOut {
  return {
    schema: 'stp.jira_handoff_manifest',
    schema_version: '1.0',
    handoff_id: 'ho-pending-1',
    status: 'PENDING_EXPORT',
    platform: 'mtk',
    created_at: '2026-11-27T01:00:00Z',
    created_by: 'tester',
    plan_run_ids: [1594, 1595],
    snapshot_cutoff: '2026-11-27T00:00:00Z',
    first_run_started_at: '2026-11-20T00:00:00Z',
    target_folder_hint: null,
    scan_tool_release: { version: '2.1.1' },
    gates: {
      coverage_complete: true,
      identity_degraded: false,
      blocking_issues: [],
      warnings: [],
    },
    identity_gate: {
      total_org_rows: 10,
      unique_events: 3,
      keyed_rows: 10,
      compat_fallback_rows: 0,
      unbound_rows: 0,
      after_cutoff_skipped_rows: 0,
      unbound_by_reason: {},
    },
    runs: [],
    unbound_rows: [],
    builds: [
      {
        build: 'V78',
        build_slug: 'V78',
        product: 'X',
        device_count: 2,
        unique_events: 3,
        issue_count: 1,
        grouped_events: 3,
        filtered_events: 0,
        filtered_by_reason: {},
        baseline_distribution: { current_only: 2, mixed: 1, baseline_only: 0 },
        artifacts: {
          preview_xls: {
            rel_path: 'V78_preview.xls',
            filename: 'V78_preview.xls',
            sha256: 'abc',
            size_bytes: 100,
          },
          review_workbook: {
            rel_path: 'V78_review.xls',
            filename: 'V78_review.xls',
            sha256: 'def',
            size_bytes: 200,
          },
        },
        issues: [
          {
            issue_index: 1,
            build: 'V78',
            count: 2,
            device_count: 2,
            device_ids: ['SN1', 'SN2'],
            source_runs: [1594],
            baseline_class: 'current_only',
            clock_fallback_count: 0,
            preview_path: '/preview',
            representative: {
              dedup_key: 'k1',
              identity_source: 'occurrence_key',
              occurrence_key: 'ok1',
              dle_id: 'd1',
              serial: 'SN1',
              entry: 'e1',
              evidence_location: 'center',
              evidence_tier: 'A',
            },
            member_dedup_keys: ['k1', 'k2'],
            form_fields: { CurProcess: 'system' },
          },
        ],
        filtered_rows: [],
        events: [],
      },
    ],
    manifest_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
    ...over,
  };
}

describe('HandoffWorkbench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
  });

  it('B11-U2: 预填 initialPlanRunIds', () => {
    render(<HandoffWorkbench initialPlanRunIds={[1594, 1595]} />, { wrapper });
    expect(screen.getByTestId('handoff-plan-run-ids')).toHaveValue('1594, 1595');
    expect(screen.getByTestId('handoff-empty-state')).toBeInTheDocument();
  });

  it('B11-U2: coverage_complete=false 阻止生成并展示缺失主机与 unbound_rows', async () => {
    (api.jiraHandoff.preflight as ReturnType<typeof vi.fn>).mockResolvedValue(makePreflight());

    render(<HandoffWorkbench initialPlanRunIds={[1594]} />, { wrapper });
    fireEvent.click(screen.getByTestId('handoff-preflight-btn'));

    expect(await screen.findByTestId('handoff-generate-blocked')).toHaveTextContent(
      '覆盖率门禁未通过',
    );
    expect(screen.getByTestId('handoff-missing-hosts-1594')).toHaveTextContent('h2');
    expect(screen.getByTestId('handoff-unbound-rows').textContent).toContain('missing_dle');
    expect(screen.getByTestId('handoff-unbound-rows').textContent).toContain('SN001');
    expect(screen.getByTestId('handoff-generate-btn')).toBeDisabled();
    expect(api.jiraHandoff.createBundle).not.toHaveBeenCalled();
  });

  it('B11-U2: PENDING_EXPORT 醒目警示，下载触发白名单 kind', async () => {
    (api.jiraHandoff.preflight as ReturnType<typeof vi.fn>).mockResolvedValue(
      makePreflight({
        gates: {
          coverage_complete: true,
          identity_degraded: false,
          blocking_issues: [],
          warnings: [],
        },
        unbound_rows: [],
        runs: [
          {
            plan_run_id: 1594,
            plan_name: 'Monkey',
            status: 'SUCCEEDED',
            partition_mode: 'CURRENT',
            hosts_expected: ['h1'],
            hosts_with_reports: ['h1'],
            missing_hosts: [],
            unexpected_hosts: [],
            mapping_status: 'ok',
            jira_project_key: 'TINNO',
          },
        ],
      }),
    );
    (api.jiraHandoff.createBundle as ReturnType<typeof vi.fn>).mockResolvedValue(makeBundle());
    (api.jiraHandoff.downloadArtifact as ReturnType<typeof vi.fn>).mockResolvedValue({
      kind: 'manifest',
      filename: 'manifest.json',
    });

    render(<HandoffWorkbench initialPlanRunIds={[1594]} />, { wrapper });
    fireEvent.click(screen.getByTestId('handoff-preflight-btn'));
    await screen.findByTestId('handoff-coverage-gate');
    expect(screen.getByTestId('handoff-generate-btn')).toBeEnabled();

    fireEvent.click(screen.getByTestId('handoff-generate-btn'));
    const banner = await screen.findByTestId('handoff-status-pending-export');
    expect(banner.textContent).toContain('待导出（Path 尚未校验可达，不可直接送去提单）');
    expect(banner.textContent).not.toContain('终稿已发布');
    expect(screen.queryByTestId('handoff-status-exported')).toBeNull();

    fireEvent.click(screen.getByTestId('handoff-download-manifest'));
    await waitFor(() =>
      expect(api.jiraHandoff.downloadArtifact).toHaveBeenCalledWith('ho-pending-1', 'manifest', {
        build: undefined,
      }),
    );

    (api.jiraHandoff.downloadArtifact as ReturnType<typeof vi.fn>).mockResolvedValue({
      kind: 'preview_xls',
      filename: 'V78_preview.xls',
    });
    fireEvent.click(screen.getByTestId('handoff-download-preview-V78'));
    await waitFor(() =>
      expect(api.jiraHandoff.downloadArtifact).toHaveBeenCalledWith('ho-pending-1', 'preview_xls', {
        build: 'V78',
      }),
    );
  });
});
