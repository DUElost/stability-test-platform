import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import TestSuiteDetailPage from './TestSuiteDetailPage';

const mocks = vi.hoisted(() => ({
  getSuite: vi.fn(),
  listCases: vi.fn(),
  importSuite: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({ data: { role: 'admin' } }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('@/utils/api', () => ({
  api: {
    suites: {
      get: mocks.getSuite,
      listCases: mocks.listCases,
      import: mocks.importSuite,
    },
  },
  toApiError: (err: unknown) => (err instanceof Error ? err : new Error(String(err))),
}));

const suiteFixture = {
  id: 7,
  name: 'demo_suite',
  display_name: '演示套件',
  project_key: 'legacy',
  case_count: 2,
  enabled_case_count: 2,
  is_active: true,
  export_stale: false,
  export_dir: '/tmp/export',
  content_sha256: 'abc',
  exported_content_sha256: null,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/test-suites/7']}>
        <Routes>
          <Route path="/test-suites/:suiteId" element={<TestSuiteDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('TestSuiteDetailPage import', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSuite.mockResolvedValue(suiteFixture);
    mocks.listCases.mockResolvedValue([]);
    mocks.importSuite.mockResolvedValue(undefined);
  });

  it('lets admin pick a Global file and passes it with runtask import', async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('suite-global-pick-btn')).toBeInTheDocument();
    });

    const globalInput = screen.getByTestId('suite-global-file-input');
    const globalFile = new File(['<global/>'], 'UiAutomatorTestData.xml', { type: 'text/xml' });
    await user.upload(globalInput, globalFile);

    expect(screen.getByTestId('suite-global-file-label')).toHaveTextContent('UiAutomatorTestData.xml');

    const runtaskFile = new File(['<runtask/>'], 'runtask.xml', { type: 'text/xml' });
    await user.upload(screen.getByTestId('suite-runtask-file-input'), runtaskFile);

    await waitFor(() => {
      expect(mocks.importSuite).toHaveBeenCalledWith(7, runtaskFile, globalFile);
    });
  });
});
