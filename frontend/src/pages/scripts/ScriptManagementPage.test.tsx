import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import ScriptManagementPage from './ScriptManagementPage';
import type { ScriptEntry } from '@/utils/api';

/** #3350（ADR-0023 D3）：`?name=&version=` 深链定位脚本版本并展开参数详情。 */

const SCRIPTS: ScriptEntry[] = [
  {
    id: 1, name: 'monkey_test', version: '5.2.0', category: 'device',
    script_type: 'python', nfs_path: '/nfs/monkey_test', display_name: null,
    content_sha256: 'a'.repeat(64), is_active: true,
    default_params: { cycles: 10 }, param_schema: {},
  },
  {
    id: 2, name: 'device_prepare', version: '1.2.3', category: 'device',
    script_type: 'python', nfs_path: '/nfs/device_prepare', display_name: null,
    content_sha256: 'b'.repeat(64), is_active: true,
    default_params: {}, param_schema: {},
  },
];

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      scripts: {
        list: vi.fn(async () => SCRIPTS),
        usage: vi.fn(async () => ({ projects: [] })),
        scan: vi.fn(),
      },
    },
  };
});

function renderAt(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <ScriptManagementPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ScriptManagementPage 深链（#3350）', () => {
  it('?name=&version= 定位到指定脚本版本并展开参数详情', async () => {
    renderAt('/script-management?name=monkey_test&version=5.2.0');

    // 搜索框被 URL 预填 → 列表只留该脚本
    await waitFor(() =>
      expect(screen.getByTestId('script-search')).toHaveValue('monkey_test'),
    );
    await waitFor(() => expect(screen.getByText('5.2.0')).toBeInTheDocument());
    expect(screen.queryByText('device_prepare')).not.toBeInTheDocument();

    // 目标版本行自动展开（参数详情可见）
    await waitFor(() => expect(screen.getByText('默认参数:')).toBeInTheDocument());
    expect(screen.getByText('cycles', { exact: false })).toBeInTheDocument();
  });

  it('只给 ?name= 时只筛选，不展开任何行', async () => {
    renderAt('/script-management?name=device_prepare');

    await waitFor(() =>
      expect(screen.getByTestId('script-search')).toHaveValue('device_prepare'),
    );
    await waitFor(() => expect(screen.getByText('1.2.3')).toBeInTheDocument());
    expect(screen.queryByText('默认参数:')).not.toBeInTheDocument();
  });
});
