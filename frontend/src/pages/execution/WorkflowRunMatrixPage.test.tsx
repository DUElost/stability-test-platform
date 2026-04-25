import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ArtifactList } from './WorkflowRunMatrixPage';

const { listJobArtifacts, artifactDownloadUrl } = vi.hoisted(() => ({
  listJobArtifacts: vi.fn(),
  artifactDownloadUrl: vi.fn((runId: number, jobId: number, artifactId: number) => (
    `/artifact-download/${runId}/${jobId}/${artifactId}`
  )),
}));

vi.mock('@/utils/api', () => ({
  api: {
    execution: {
      listJobArtifacts,
      artifactDownloadUrl,
    },
  },
}));

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
  });

  it('uses the API artifact download helper with run id and job id', async () => {
    renderWithQuery(<ArtifactList runId={77} jobId={88} />);

    const link = await screen.findByRole('link');

    expect(listJobArtifacts).toHaveBeenCalledWith(77, 88);
    expect(artifactDownloadUrl).toHaveBeenCalledWith(77, 88, 9);
    expect(link).toHaveAttribute('href', '/artifact-download/77/88/9');
  });
});
