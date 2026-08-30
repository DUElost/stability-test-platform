import apiClient, { unwrapApiResponse } from './client';
import type {
  ApiResponseEnvelope,
  SuiteExportResult,
  SuiteValidateResult,
  TestCase,
  TestCaseInput,
  TestSuiteCreateInput,
  TestSuiteDetail,
  TestSuiteSummary,
  TestSuiteUpdateInput,
} from './types';

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** ADR-0030 — 套件管理面（Plan 绑定用 name 引用）。 */
export const suites = {
  list: (params?: { project_key?: string; is_active?: boolean; q?: string }) =>
    unwrapApiResponse(
      apiClient.get<ApiResponseEnvelope<TestSuiteSummary[]>>('/test-suites', { params }),
    ),

  get: (suiteId: number) =>
    unwrapApiResponse<TestSuiteDetail>(
      apiClient.get<ApiResponseEnvelope<TestSuiteDetail>>(`/test-suites/${suiteId}`),
    ),

  create: (payload: TestSuiteCreateInput) =>
    unwrapApiResponse<TestSuiteDetail>(
      apiClient.post<ApiResponseEnvelope<TestSuiteDetail>>('/test-suites', payload),
    ),

  update: (suiteId: number, payload: TestSuiteUpdateInput) =>
    unwrapApiResponse<TestSuiteDetail>(
      apiClient.put<ApiResponseEnvelope<TestSuiteDetail>>(`/test-suites/${suiteId}`, payload),
    ),

  remove: (suiteId: number) =>
    unwrapApiResponse<{ id: number; is_active: boolean }>(
      apiClient.delete<ApiResponseEnvelope<{ id: number; is_active: boolean }>>(
        `/test-suites/${suiteId}`,
      ),
    ),

  listCases: (
    suiteId: number,
    params?: { enabled?: boolean; q?: string; skip?: number; limit?: number },
  ) =>
    unwrapApiResponse<TestCase[]>(
      apiClient.get<ApiResponseEnvelope<TestCase[]>>(`/test-suites/${suiteId}/cases`, { params }),
    ),

  createCase: (suiteId: number, payload: TestCaseInput) =>
    unwrapApiResponse<TestCase>(
      apiClient.post<ApiResponseEnvelope<TestCase>>(`/test-suites/${suiteId}/cases`, payload),
    ),

  updateCase: (caseId: number, payload: TestCaseInput) =>
    unwrapApiResponse<TestCase>(
      apiClient.put<ApiResponseEnvelope<TestCase>>(`/test-cases/${caseId}`, payload),
    ),

  deleteCase: (caseId: number) =>
    unwrapApiResponse<{ id: number; deleted: boolean }>(
      apiClient.delete<ApiResponseEnvelope<{ id: number; deleted: boolean }>>(
        `/test-cases/${caseId}`,
      ),
    ),

  import: async (suiteId: number, runtask: File, global?: File | null) => {
    const fd = new FormData();
    fd.append('file', runtask);
    if (global) fd.append('global', global);
    return unwrapApiResponse<TestSuiteDetail>(
      apiClient.post<ApiResponseEnvelope<TestSuiteDetail>>(`/test-suites/${suiteId}/import`, fd),
    );
  },

  export: async (suiteId: number, times = 0): Promise<{ blob: Blob; stale: boolean }> => {
    const response = await apiClient.get(`/test-suites/${suiteId}/export`, {
      params: { times },
      responseType: 'blob',
    });
    const stale = response.headers['x-export-stale'] === '1';
    return { blob: response.data as Blob, stale };
  },

  exportGlobal: async (suiteId: number): Promise<Blob> => {
    const response = await apiClient.get(`/test-suites/${suiteId}/global`, {
      responseType: 'blob',
    });
    return response.data as Blob;
  },

  downloadExport: async (suiteId: number, filename = 'runtask.xml') => {
    const { blob, stale } = await suites.export(suiteId);
    downloadBlob(blob, filename);
    return stale;
  },

  validate: (suiteId: number) =>
    unwrapApiResponse<SuiteValidateResult>(
      apiClient.post<ApiResponseEnvelope<SuiteValidateResult>>(
        `/test-suites/${suiteId}/validate`,
        {},
      ),
    ),

  exportToToolDir: (suiteId: number) =>
    unwrapApiResponse<SuiteExportResult>(
      apiClient.post<ApiResponseEnvelope<SuiteExportResult>>(
        `/test-suites/${suiteId}/export-to-tool-dir`,
        {},
      ),
    ),
};
