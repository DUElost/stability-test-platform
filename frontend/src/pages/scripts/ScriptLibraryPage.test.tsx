import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ScriptLibraryPage from './ScriptLibraryPage';

vi.mock('@/utils/api', () => ({
  api: {
    scripts: {
      list: vi.fn().mockResolvedValue([
        {
          id: 1,
          name: 'connect_wifi',
          display_name: 'Connect WiFi',
          category: 'device',
          script_type: 'python',
          version: '1.0.0',
          nfs_path: '/nfs/connect_wifi.py',
          param_schema: {
            properties: {
              ssid: { type: 'string' },
              password: { type: 'string' },
            },
          },
          content_sha256: 'a'.repeat(64),
          is_active: true,
        },
      ]),
      listCategories: vi.fn().mockResolvedValue(['device']),
      scan: vi.fn().mockResolvedValue({ created: 0, skipped: 1, deactivated: 0, conflicts: [] }),
    },
    scriptSequences: {
      list: vi.fn().mockResolvedValue({
        items: [
          {
            id: 1,
            name: 'WiFi 模板',
            items: [{ script_name: 'connect_wifi', version: '1.0.0', params: {}, timeout_seconds: 30, retry: 0 }],
            on_failure: 'stop',
            created_at: '2026-04-26T01:00:00Z',
            updated_at: '2026-04-26T01:00:00Z',
          },
        ],
        total: 1,
        skip: 0,
        limit: 100,
      }),
    },
  },
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ScriptLibraryPage />
    </QueryClientProvider>,
  );
}

describe('ScriptLibraryPage', () => {
  it('renders script catalog entries', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '脚本库' })).toBeInTheDocument();
    expect(await screen.findByText('connect_wifi')).toBeInTheDocument();
    expect(await screen.findByText('ssid')).toBeInTheDocument();
    expect(await screen.findByText('password')).toBeInTheDocument();
    expect(await screen.findByText('1 个模板引用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重新扫描/ })).toBeInTheDocument();
  });
});
