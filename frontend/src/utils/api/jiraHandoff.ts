/**
 * B11-U2 / #4199：Monkey AEE 提单表单 A 段 API 客户端（`/api/v1/jira/handoff`）。
 *
 * 五个端点与后端 `backend/api/routes/jira_handoff.py` 对齐；下载白名单仅
 * `manifest` / `preview_xls` / `review_workbook`。前端不自算去重计数、不拼接 15.4 路径。
 */
import apiClient, { unwrapApiResponse } from './client';
import { NO_TIMEOUT } from './timeouts';
import type {
  ApiResponseEnvelope,
  HandoffArtifactKind,
  HandoffBundleCreateIn,
  HandoffBundleOut,
  HandoffBundleSummaryOut,
  HandoffPreflightIn,
  HandoffPreflightOut,
} from './types';

const HANDOFF_ARTIFACT_KINDS: ReadonlySet<HandoffArtifactKind> = new Set([
  'manifest',
  'preview_xls',
  'review_workbook',
]);

function assertArtifactKind(kind: string): asserts kind is HandoffArtifactKind {
  if (!HANDOFF_ARTIFACT_KINDS.has(kind as HandoffArtifactKind)) {
    throw new Error(`unsupported handoff artifact_kind: ${kind}`);
  }
}

function filenameFromContentDisposition(header: unknown, fallback: string): string {
  if (typeof header !== 'string' || !header) return fallback;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      return utf8[1].trim();
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1]?.trim() || fallback;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export const jiraHandoff = {
  /** `POST /jira/handoff/preflight` — 覆盖率 + 发生身份双门禁预检。 */
  preflight: (body: HandoffPreflightIn) =>
    unwrapApiResponse<HandoffPreflightOut>(
      apiClient.post<ApiResponseEnvelope<HandoffPreflightOut>>('/jira/handoff/preflight', body),
    ),

  /** `POST /jira/handoff/bundles` — 生成 A 段 `PENDING_EXPORT` 交付包。 */
  createBundle: (body: HandoffBundleCreateIn) =>
    unwrapApiResponse<HandoffBundleOut>(
      apiClient.post<ApiResponseEnvelope<HandoffBundleOut>>('/jira/handoff/bundles', body, {
        timeout: NO_TIMEOUT,
      }),
    ),

  /** `GET /jira/handoff/bundles` — 近期交付包摘要列表。 */
  listBundles: (limit = 20) =>
    unwrapApiResponse<HandoffBundleSummaryOut[]>(
      apiClient.get<ApiResponseEnvelope<HandoffBundleSummaryOut[]>>('/jira/handoff/bundles', {
        params: { limit },
      }),
    ),

  /** `GET /jira/handoff/bundles/{handoff_id}` — 冻结 manifest 详情。 */
  getBundle: (handoffId: string) =>
    unwrapApiResponse<HandoffBundleOut>(
      apiClient.get<ApiResponseEnvelope<HandoffBundleOut>>(
        `/jira/handoff/bundles/${encodeURIComponent(handoffId)}`,
      ),
    ),

  /**
   * 构造白名单下载 URL（同源 cookie 鉴权）。
   * `preview_xls` / `review_workbook` 必须带 `build` 查询参数。
   */
  artifactDownloadUrl: (
    handoffId: string,
    artifactKind: HandoffArtifactKind,
    build?: string | null,
  ): string => {
    assertArtifactKind(artifactKind);
    const base = `/api/v1/jira/handoff/bundles/${encodeURIComponent(handoffId)}/files/${artifactKind}`;
    if (artifactKind === 'manifest') return base;
    if (!build) {
      throw new Error(`build is required for artifact_kind=${artifactKind}`);
    }
    return `${base}?build=${encodeURIComponent(build)}`;
  },

  /**
   * `GET /jira/handoff/bundles/{id}/files/{kind}` — 安全下载白名单产物。
   * 触发浏览器下载；返回实际请求的 kind（便于单测断言）。
   */
  downloadArtifact: async (
    handoffId: string,
    artifactKind: HandoffArtifactKind,
    options?: { build?: string | null; filename?: string },
  ): Promise<{ kind: HandoffArtifactKind; filename: string }> => {
    assertArtifactKind(artifactKind);
    const params =
      artifactKind === 'manifest'
        ? undefined
        : { build: options?.build ?? undefined };
    if (artifactKind !== 'manifest' && !params?.build) {
      throw new Error(`build is required for artifact_kind=${artifactKind}`);
    }
    const response = await apiClient.get(
      `/jira/handoff/bundles/${encodeURIComponent(handoffId)}/files/${artifactKind}`,
      {
        params,
        responseType: 'blob',
        timeout: NO_TIMEOUT,
      },
    );
    const fallback =
      options?.filename ??
      (artifactKind === 'manifest'
        ? 'manifest.json'
        : artifactKind === 'preview_xls'
          ? 'preview.xls'
          : 'review_workbook.xls');
    const filename = filenameFromContentDisposition(
      response.headers?.['content-disposition'],
      fallback,
    );
    downloadBlob(response.data as Blob, filename);
    return { kind: artifactKind, filename };
  },
};
