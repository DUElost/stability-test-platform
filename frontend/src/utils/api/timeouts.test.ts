/**
 * #1199 — 长请求超时豁免矩阵：LLM 推理 / 文件上传下载不设应用层超时（timeout: 0），
 * 避免被 apiClient 默认 30s 误伤。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NO_TIMEOUT } from './timeouts';

// 显式参数签名：否则 mock.calls 元素被推导为空元组，断言索引会 fail type-check
const mocks = vi.hoisted(() => ({
  get: vi.fn(async (_url?: string, _config?: Record<string, unknown>) => ({ data: {}, headers: {} })),
  post: vi.fn(async (_url?: string, _body?: unknown, _config?: Record<string, unknown>) => ({ data: {} })),
}));

vi.mock('./client', () => ({
  default: { get: mocks.get, post: mocks.post },
  unwrapApiResponse: (p: unknown) => p,
  toApiError: (e: unknown) => e,
  ApiError: class ApiError extends Error {},
  registerAuthFailureHandler: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('长请求超时豁免（#1199）', () => {
  it('assistant 连接测试与消息发送豁免应用层超时', async () => {
    const { aiAssistant } = await import('./aiAssistant');

    await aiAssistant.testConnection();
    await aiAssistant.sendMessage(1, 'hi');

    expect(mocks.post.mock.calls[0][2]).toEqual({ timeout: NO_TIMEOUT });
    expect(mocks.post.mock.calls[1][2]).toEqual({ timeout: NO_TIMEOUT });
  });

  it('报表导出与套件导出/Global 下载豁免应用层超时', async () => {
    const { planRuns } = await import('./planRuns');
    const { suites } = await import('./suites');

    await planRuns.exportReport(1);
    await suites.export(1);
    await suites.exportGlobal(1);

    expect(mocks.get).toHaveBeenCalledTimes(3);
    for (const call of mocks.get.mock.calls) {
      expect(call[1]).toMatchObject({ timeout: NO_TIMEOUT });
    }
  });

  it('JIRA xls 上传豁免应用层超时', async () => {
    const { dedup } = await import('./dedup');

    await dedup.startJiraRun({
      vendor: 'transsion',
      stage: 'upload_list',
      dryRun: false,
      file: new File(['x'], 'list.xls'),
    });

    expect(mocks.post.mock.calls[0][1]).toBeInstanceOf(FormData);
    expect(mocks.post.mock.calls[0][2]).toEqual({ timeout: NO_TIMEOUT });
  });
});
