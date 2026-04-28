import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import WorkflowRunMatrixPage, { ArtifactList } from './WorkflowRunMatrixPage';

const navigateMock = vi.fn();

const { listJobArtifacts, artifactDownloadUrl, getRun, getRunJobs, createJobJiraDraft } = vi.hoisted(() => ({
  listJobArtifacts: vi.fn(),
  artifactDownloadUrl: vi.fn((runId: number, jobId: number, artifactId: number) => (
    `/artifact-download/${runId}/${jobId}/${artifactId}`
  )),
  getRun: vi.fn(),
  getRunJobs: vi.fn(),
  createJobJiraDraft: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    execution: {
      listJobArtifacts,
      artifactDownloadUrl,
      getRun,
      getRunJobs,
      createJobJiraDraft,
    },
  },
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: () => ({
    lastMessage: null,
    connectionStatus: 'connected',
  }),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ runId: '618' }),
    useNavigate: () => navigateMock,
  };
});

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
    </QueryClientProvider>,
  );
}

describe('WorkflowRunMatrixPage artifacts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listJobArtifacts.mockResolvedValue([
      {
        id: 9,
        filename: 'result.tar.gz',
        artifact_type: 'logs',
        size_bytes: 2048,
      },
    ]);
    getRun.mockResolvedValue({
      id: 618,
      workflow_definition_id: 695,
      status: 'RUNNING',
      failure_threshold: 0.05,
      triggered_by: 'api',
      started_at: '2026-04-26T10:33:00.859276+08:00',
      ended_at: null,
      result_summary: null,
      jobs: [],
    });
    getRunJobs.mockResolvedValue([
      {
        id: 672,
        workflow_run_id: 618,
        task_template_id: 569,
        device_id: 1124,
        device_serial: '1215225432000416',
        host_id: 'auto-fdaf1d55e319',
        status: 'RUNNING',
        status_reason: 'claimed_by_agent',
        pipeline_def: {},
        created_at: '2026-04-26T10:33:00.862782+08:00',
        started_at: '2026-04-26T18:33:05.625355+08:00',
        ended_at: null,
        step_traces: [],
      },
    ]);
  });

  it('uses the API artifact download helper with run id and job id', async () => {
    renderWithQuery(<ArtifactList runId={77} jobId={88} />);

    const link = await screen.findByRole('link');

    expect(listJobArtifacts).toHaveBeenCalledWith(77, 88);
    expect(artifactDownloadUrl).toHaveBeenCalledWith(77, 88, 9);
    expect(link).toHaveAttribute('href', '/artifact-download/77/88/9');
  });
});

describe('WorkflowRunMatrixPage job block', () => {
  it('does not pulse the running device tile', async () => {
    renderWithQuery(<WorkflowRunMatrixPage />);

    const button = await screen.findByRole('button', { name: /1215225432000416/i });
    expect(button.className).not.toContain('animate-pulse');
  });
});
