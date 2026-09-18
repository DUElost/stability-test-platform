import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  auditList: vi.fn(),
  auditFacets: vi.fn(),
  usersList: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    audit: {
      list: (...a: unknown[]) => mocks.auditList(...a),
      facets: () => mocks.auditFacets(),
    },
    users: { list: (...a: unknown[]) => mocks.usersList(...a) },
  },
}));

import AuditLogPage from './AuditLogPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuditLogPage />
    </QueryClientProvider>,
  );
}

describe('AuditLogPage 筛选（#628）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.auditList.mockResolvedValue({
      items: [{
        id: 1,
        username: 'alice',
        action: 'create',
        resource_type: 'plan',
        resource_id: '42',
        ip_address: '10.62.8.2',
        timestamp: '2026-09-13T00:00:00Z',
      }],
      total: 1,
    });
    mocks.usersList.mockResolvedValue({
      items: [{ id: 1, username: 'alice', role: 'admin' }],
      total: 1,
    });
    // #2629：候选值来自**实际写入的记录**（后端 facets），这里给一份与词表同源的样本
    mocks.auditFacets.mockResolvedValue({
      resource_types: [
        { value: 'session', count: 160 },
        { value: 'plan_run', count: 85 },
        { value: 'plan', count: 4 },
      ],
      actions: [
        { value: 'abort_plan_run', count: 80 },
        { value: 'create', count: 11 },
        { value: 'update', count: 9 },
      ],
    });
  });

  it('用户名带下拉候选，且未提交不发请求、blur 才带参数重查', async () => {
    renderPage();
    const ipInput = await screen.findByTestId('audit-ip-filter');

    // 候选来自 users 列表（admin 页面）
    await waitFor(() => {
      const option = document.querySelector('#audit-username-options option');
      expect(option?.getAttribute('value')).toBe('alice');
    });

    const callsBefore = mocks.auditList.mock.calls.length;
    fireEvent.change(ipInput, { target: { value: '10.62.8.2' } });
    // 逐键不发请求
    expect(mocks.auditList.mock.calls.length).toBe(callsBefore);

    fireEvent.blur(ipInput);
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0,
        50,
        expect.objectContaining({ ip_address: '10.62.8.2' }),
      ),
    );
  });

  it('用户名与资源 ID 走同样的 blur/Enter 提交语义', async () => {
    renderPage();
    const usernameInput = await screen.findByTestId('audit-username-filter');
    const resourceInput = screen.getByTestId('audit-resource-id-filter');

    fireEvent.change(usernameInput, { target: { value: 'alice' } });
    fireEvent.blur(usernameInput);
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0,
        50,
        expect.objectContaining({ username: 'alice' }),
      ),
    );

    fireEvent.change(resourceInput, { target: { value: '42' } });
    fireEvent.keyDown(resourceInput, { key: 'Enter' });
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0,
        50,
        expect.objectContaining({ username: 'alice', resource_id: '42' }),
      ),
    );
  });


  describe('筛选候选与写入词表同源（#2629）', () => {
    /** 读 `<select>` 的实际候选值（不含哨兵） */
    async function resourceOptionValues(): Promise<string[]> {
      const select = await screen.findByTestId('audit-resource-filter');
      return Array.from((select as HTMLSelectElement).options)
        .map((o) => o.value)
        .filter((v) => v !== 'all');
    }

    it('资源下拉的选项 = facets 返回值，死选项不再存在', async () => {
      renderPage();
      await waitFor(() => expect(mocks.auditFacets).toHaveBeenCalled());

      expect(await resourceOptionValues()).toEqual(['session', 'plan_run', 'plan']);

      // 本单修掉的 6 个死选项（写入侧根本不存在）——它们唯一的贡献是「共 0 条」
      for (const dead of ['tool', 'tool_category', 'template']) {
        expect(await resourceOptionValues()).not.toContain(dead);
      }
      const datalistValues = () => Array.from(
        document.querySelectorAll('#audit-action-options option'),
      ).map((o) => o.getAttribute('value'));
      await waitFor(() => expect(datalistValues()).toContain('abort_plan_run'));
      for (const dead of ['dispatch', 'start', 'cancel']) {
        expect(datalistValues()).not.toContain(dead);
      }
    });

    it('选项上带真实条数，且未映射的值按原字面量展示（不编中文标签）', async () => {
      renderPage();
      const select = await screen.findByTestId('audit-resource-filter');
      await waitFor(() =>
        expect((select as HTMLSelectElement).options[1].textContent).toContain('160'),
      );
      const texts = Array.from((select as HTMLSelectElement).options).map((o) => o.textContent);
      // session 有映射（会话/登录）；plan_run 有映射；没有映射的值不会凭空得中文名
      expect(texts[1]).toContain('会话/登录');
      expect(texts[2]).toContain('计划运行');
      expect(texts[3]).toContain('Plan（4）');
    });

    it('选中 facets 里的资源即按该字面量精确筛（下拉值与参数一一对应）', async () => {
      renderPage();
      const select = await screen.findByTestId('audit-resource-filter');
      await waitFor(async () => expect(await resourceOptionValues()).toContain('plan_run'));

      fireEvent.change(select, { target: { value: 'plan_run' } });
      await waitFor(() =>
        expect(mocks.auditList).toHaveBeenLastCalledWith(
          0, 50, expect.objectContaining({ resource_type: 'plan_run' }),
        ),
      );
    });

    it('操作改成精确匹配 + datalist 补全（86 种字面量不该假装能列全）', async () => {
      renderPage();
      const actionInput = await screen.findByTestId('audit-action-filter');
      await waitFor(() => {
        const values = Array.from(
          document.querySelectorAll('#audit-action-options option'),
        ).map((o) => o.getAttribute('value'));
        expect(values).toEqual(['abort_plan_run', 'create', 'update']);
      });

      // 与 username/IP/resource_id 同一提交语义：blur 才发请求
      fireEvent.change(actionInput, { target: { value: 'abort_plan_run' } });
      fireEvent.blur(actionInput);
      await waitFor(() =>
        expect(mocks.auditList).toHaveBeenLastCalledWith(
          0, 50, expect.objectContaining({ action: 'abort_plan_run' }),
        ),
      );
    });

    it('facets 请求失败时降级为自由输入，而不是造出选项', async () => {
      mocks.auditFacets.mockRejectedValue(new Error('boom'));
      renderPage();

      const select = await screen.findByTestId('audit-resource-filter');
      await waitFor(() => expect(mocks.auditList).toHaveBeenCalled());
      // 只剩哨兵「全部资源」——宁缺毋假
      expect(Array.from((select as HTMLSelectElement).options).map((o) => o.value))
        .toEqual(['all']);
      // 操作维度本来就是自由输入，仍可用
      expect(screen.getByTestId('audit-action-filter')).toBeTruthy();
    });
  });
  it('清空输入并 blur 后不再携带该筛选参数', async () => {
    renderPage();
    const ipInput = await screen.findByTestId('audit-ip-filter');

    fireEvent.change(ipInput, { target: { value: '10.62.8.2' } });
    fireEvent.blur(ipInput);
    await waitFor(() =>
      expect(mocks.auditList).toHaveBeenLastCalledWith(
        0, 50, expect.objectContaining({ ip_address: '10.62.8.2' }),
      ),
    );

    fireEvent.change(ipInput, { target: { value: '' } });
    fireEvent.blur(ipInput);
    await waitFor(() => {
      const calls = mocks.auditList.mock.calls;
      const lastParams = calls[calls.length - 1]?.[2] as Record<string, string>;
      expect(lastParams.ip_address).toBeUndefined();
    });
  });
});
