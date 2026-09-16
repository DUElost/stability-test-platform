/**
 * #2363 —— 标题的单一写入点：路由默认兜住「页面没调标题」，页面 claim 只覆盖不互斗。
 */
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';

import { RouteTitle, useDocumentTitle } from './useDocumentTitle';

function QuietPage({ title }: { title?: string }) {
  useDocumentTitle(title);
  return null;
}

function renderRoute(path: string, pageTitle?: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RouteTitle />
      <QuietPage title={pageTitle} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.title = '测试起点标题';
});

describe('useDocumentTitle (#2363)', () => {
  it('页面完全不调标题时，路由默认仍写进标签（旧的失效形态）', async () => {
    renderRoute('/hosts');
    await waitFor(() => expect(document.title).toBe('主机集群 | STP'));
  });

  it('页面 claim 赢过路由默认（详情页动态标题）', async () => {
    renderRoute('/hosts', '主机 h1');
    await waitFor(() => expect(document.title).toBe('主机 h1 | STP'));
  });

  it('动态标题尚为空时退回路由默认，而不是清空标签', async () => {
    renderRoute('/execution/plan-runs/409', '');
    await waitFor(() => expect(document.title).toBe('Plan Run 详情 | STP'));
  });

  it('页面不再 claim 时回到路由默认，不残留上一页的标题', async () => {
    function Harness({ pageTitle }: { pageTitle?: string }) {
      return (
        <MemoryRouter initialEntries={['/issue-tracker']}>
          <RouteTitle />
          <QuietPage title={pageTitle} />
        </MemoryRouter>
      );
    }
    const { rerender } = render(<Harness pageTitle="STABILITY-1" />);
    await waitFor(() => expect(document.title).toBe('STABILITY-1 | STP'));
    rerender(<Harness />);
    await waitFor(() => expect(document.title).toBe('问题追踪 | STP'));
  });

});
