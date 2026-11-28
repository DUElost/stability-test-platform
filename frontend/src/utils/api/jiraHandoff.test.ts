import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
const post = vi.fn();

vi.mock('./client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
  unwrapApiResponse: async <T>(promise: Promise<{ data: { data: T } }>) => {
    const res = await promise;
    return res.data.data;
  },
}));

import { jiraHandoff } from './jiraHandoff';

describe('jiraHandoff client', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('artifactDownloadUrl 仅允许白名单 kind，preview 必须带 build', () => {
    expect(jiraHandoff.artifactDownloadUrl('ho-1', 'manifest')).toBe(
      '/api/v1/jira/handoff/bundles/ho-1/files/manifest',
    );
    expect(jiraHandoff.artifactDownloadUrl('ho-1', 'preview_xls', 'V78')).toBe(
      '/api/v1/jira/handoff/bundles/ho-1/files/preview_xls?build=V78',
    );
    expect(() => jiraHandoff.artifactDownloadUrl('ho-1', 'preview_xls')).toThrow(/build is required/);
    expect(() =>
      jiraHandoff.artifactDownloadUrl('ho-1', 'not_a_kind' as 'manifest'),
    ).toThrow(/unsupported handoff artifact_kind/);
  });

  it('downloadArtifact 请求白名单路径', async () => {
    get.mockResolvedValue({
      data: new Blob(['{}']),
      headers: { 'content-disposition': 'attachment; filename="manifest.json"' },
    });
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    const click = vi.fn();
    vi.spyOn(document, 'createElement').mockReturnValue({
      click,
      href: '',
      download: '',
    } as unknown as HTMLAnchorElement);

    await jiraHandoff.downloadArtifact('ho-1', 'manifest');
    expect(get).toHaveBeenCalledWith('/jira/handoff/bundles/ho-1/files/manifest', {
      params: undefined,
      responseType: 'blob',
      timeout: 0,
    });

    get.mockResolvedValue({
      data: new Blob(['xls']),
      headers: {},
    });
    await jiraHandoff.downloadArtifact('ho-1', 'preview_xls', { build: 'V78' });
    expect(get).toHaveBeenLastCalledWith('/jira/handoff/bundles/ho-1/files/preview_xls', {
      params: { build: 'V78' },
      responseType: 'blob',
      timeout: 0,
    });
  });
});
