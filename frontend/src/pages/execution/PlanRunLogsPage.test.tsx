import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest';
import PlanRunLogsPage from './PlanRunLogsPage';
import { HeaderSlotProvider, useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { planRunKeys } from '@/utils/api/queryKeys';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getRun: vi.fn(),
  getEvents: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ runId: '12' }),
  };
});

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: {
      get: mocks.getRun,
      getEvents: mocks.getEvents,
    },
  },
}));

function HeaderSlotOutlet() {
  const { headerSlot } = useHeaderSlot();
  return <>{headerSlot}</>;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <HeaderSlotProvider>
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HeaderSlotOutlet />
          <PlanRunLogsPage />
        </QueryClientProvider>
      </MemoryRouter>
    </HeaderSlotProvider>,
  );
  // #823：返回 client 供断言查询配置
  return queryClient;
}

function eventsPayload(overrides: Record<string, unknown> = {}) {
  return {
    plan_run_id: 12,
    total: 150,
    events: [
      {
        ts: '2026-05-08T12:30:00Z',
        stage: 'patrol',
        severity: 'err',
        category: 'step',
        title: 'monkey_check 步骤失败',
        description: 'DEV-3064 连续失败 3 次',
        device_serial: 'DEV-3064',
        job_id: 3064,
      },
    ],
    facets: {
      by_stage: { all: 150, patrol: 150, init: 0, teardown: 0, trigger: 0, system: 0 },
      by_severity: { all: 150, err: 150, warn: 0, info: 0, ok: 0 },
    },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'RUNNING' });
  mocks.getEvents.mockResolvedValue(eventsPayload());
});

describe('PlanRunLogsPage', () => {
  it('renders the logs tab + paginated event stream', async () => {
    renderPage();
    expect(await screen.findByTestId('plan-run-event-stream')).toBeInTheDocument();
    expect(screen.getByTestId('plan-run-tabs')).toBeInTheDocument();
    expect(await screen.findByText('monkey_check 步骤失败')).toBeInTheDocument();
  });

  it('requests the first page with limit/offset, then offsets on next page', async () => {
    renderPage();
    await waitFor(() => expect(mocks.getEvents).toHaveBeenCalled());
    expect(mocks.getEvents).toHaveBeenCalledWith(
      12,
      expect.objectContaining({ limit: 50, offset: 0 }),
    );
    fireEvent.click(await screen.findByTestId('event-page-next'));
    await waitFor(() =>
      expect(mocks.getEvents).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ offset: 50 }),
      ),
    );
  });

  it('resets to the first page and re-queries when a stage filter changes', async () => {
    renderPage();
    await waitFor(() => expect(mocks.getEvents).toHaveBeenCalled());
    fireEvent.click(await screen.findByTestId('event-filter-stage-patrol'));
    await waitFor(() =>
      expect(mocks.getEvents).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ stage: 'patrol', offset: 0 }),
      ),
    );
  });

  it('#823：runQ 非终态慢轮询推进、终态即停（eventsQ 随之停更）', async () => {
    const qc = renderPage();
    await waitFor(() => expect(mocks.getRun).toHaveBeenCalled());

    const opts = qc.getQueryCache().find({ queryKey: planRunKeys.detail(12) })?.options as {
      refetchInterval?: unknown;
    };
    const fn = opts.refetchInterval as (q: { state: { data?: { status?: string } } }) => unknown;
    expect(typeof fn).toBe('function');
    expect(fn({ state: { data: { status: 'RUNNING' } } })).toBe(30_000);
    expect(fn({ state: { data: { status: 'SUCCESS' } } })).toBe(false);
  });
});

// ── #2028：CSV 导出的三条未覆盖行为（分块 / 静默截断 / 在途重复行）+ 下载健壮性 ──

const EXPORT_CHUNK = 500; // 与组件内常量一致（后端 _MAX_EVENTS_LIMIT）
const EXPORT_MAX_ROWS = 20_000;

function makeEvent(index: number, overrides: Record<string, unknown> = {}) {
  return {
    ts: `2026-05-08T12:${String(Math.floor(index / 60) % 60).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}Z`,
    stage: 'patrol',
    severity: 'err',
    category: 'step',
    title: `event-${index}`,
    description: `desc-${index}`,
    device_serial: `DEV-${index}`,
    job_id: index,
    ...overrides,
  };
}

function pagePayload(events: unknown[], total: number) {
  return {
    plan_run_id: 12,
    total,
    events,
    facets: { by_stage: { all: total }, by_severity: { all: total } },
  };
}

/** 拦住 jsdom 里可能缺失/不落地实现的 blob URL，拿到真实的 Blob 内容。 */
function stubBlobUrl() {
  const captured: { blob: Blob; url: string }[] = [];
  const originalCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  const originalRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true, writable: true,
    value: (blob: Blob) => {
      const url = `blob:mock-${captured.length}`;
      captured.push({ blob, url });
      return url;
    },
  });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  return {
    captured,
    restore: () => {
      for (const [name, desc] of [
        ['createObjectURL', originalCreate] as const,
        ['revokeObjectURL', originalRevoke] as const,
      ]) {
        if (desc) Object.defineProperty(URL, name, desc);
        else delete (URL as unknown as Record<string, unknown>)[name];
      }
    },
  };
}

/** 取导出用的 CSV 文本（已去掉 BOM）。 */
async function csvLines(): Promise<string[]> {
  const { captured } = lastStub;
  expect(captured).toHaveLength(1);
  const blob: Blob = captured[0].blob;
  // BOM 只能按**字节**断言：Blob 的 text() 走 UTF-8 decode，规范要求剥掉前导 BOM，
  // 拿它断言 startsWith('\ufeff') 会恒假（本单第一版就被这条假红绊了一次）。
  const bytes = new Uint8Array(await blob.arrayBuffer());
  expect(Array.from(bytes.subarray(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
  return (await blob.text()).split('\r\n');
}

let lastStub: ReturnType<typeof stubBlobUrl>;

/** 只统计导出翻页的请求（页面自身的 limit=50 查询不在此列）。 */
function exportOffsets(): number[] {
  return mocks.getEvents.mock.calls
    .map((call) => call[1] as { limit?: number; offset?: number } | undefined)
    .filter((params) => params?.limit === EXPORT_CHUNK)
    .map((params) => params?.offset ?? -1);
}

function renderAndExport() {
  lastStub = stubBlobUrl();
  renderPage();
  return clickExportAndReadCsv();
}

async function clickExportAndReadCsv() {
  const btn = await screen.findByTestId('event-export-csv');
  await act(async () => {
    fireEvent.click(btn);
  });
  return csvLines();
}

/** 供页：按 (offset, limit) 切一个 total 长度的虚拟列表——页面查询与导出共用。 */
function serveEvents(total: number, row: (index: number) => unknown = makeEvent) {
  mocks.getEvents.mockImplementation(async (_id: number, params: { offset: number; limit: number }) => {
    const n = Math.max(0, Math.min(params.limit, total - params.offset));
    return pagePayload(Array.from({ length: n }, (_, i) => row(params.offset + i)), total);
  });
}

afterEach(async () => {
  // 顺序要紧：先还原被截走的 setTimeout，再排空一个宏任务——否则上一个用例
  // "推迟到下一 tick" 的 revoke 会记到下一个用例的 mock 上（跨用例污染，实测踩过）。
  vi.restoreAllMocks();
  await new Promise((resolve) => setTimeout(resolve, 0));
  lastStub?.restore();
  vi.useRealTimers();
});

describe('PlanRunLogsPage — CSV 导出（#2028）', () => {
  it('终态 run 全量读完：恰好 1 表头 + N 数据行，且没有任何"如实报告"尾注', async () => {
    mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'SUCCESS' });
    serveEvents(1_234);
    const lines = await renderAndExport();
    // 分块：500 + 500 + 234，第三块不足一块即停
    expect(exportOffsets()).toEqual([0, 500, 1_000]);
    expect(lines).toHaveLength(1 + 1_234);
    expect(lines[0]).toContain('"时间"');
    expect(lines[1 + 1_233]).toContain('"event-1233"');
  }, 15_000);

  it('CSV 转义：引号翻倍、逗号/换行不破坏列，空字段留空串', async () => {
    mocks.getEvents.mockResolvedValue(pagePayload([
      makeEvent(0, { title: 'a"b,c', description: '多行\n说明', device_serial: null, job_id: null }),
    ], 1));
    const lines = await renderAndExport();
    expect(lines[1]).toBe(
      '"2026-05-08T12:00:00Z","patrol","err","step","a""b,c","多行\n说明","",""',
    );
  });

  it('命中 20 000 行上限：不再继续翻页，且 CSV 里明确写了被截断', async () => {
    mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'SUCCESS' });
    serveEvents(EXPORT_MAX_ROWS + 5_000); // 后端还有 5 000 行没给
    const lines = await renderAndExport();
    expect(exportOffsets()).toHaveLength(EXPORT_MAX_ROWS / EXPORT_CHUNK);
    expect(lines).toHaveLength(1 + EXPORT_MAX_ROWS + 1); // 表头 + 上限行 + 截断说明
    expect(lines[lines.length - 1]).toContain('命中导出上限 20000 行');
  }, 30_000);

  it('在途 run 的窗口位移：重复行去重并如实报告条数', async () => {
    mocks.getEvents.mockImplementation(
      async (_id: number, params: { offset: number; limit: number }) => {
        if (params.limit < EXPORT_CHUNK) return pagePayload([makeEvent(0)], 1_000); // 页面查询
        // 第二块整体下移 1 行：新事件插到 ts DESC 队首，窗口位移 → 边界行重复
        const start = Math.max(0, params.offset - 1);
        return pagePayload(
          Array.from({ length: EXPORT_CHUNK }, (_, i) => makeEvent(start + i)),
          1_000,
        );
      });
    const lines = await renderAndExport();
    expect(exportOffsets()).toEqual([0, 500]);
    expect(lines).toHaveLength(1 + 999 + 2); // 表头 + 去重后 999 行 + 去重说明 + 非终态快照说明
    expect(lines.filter((l) => l.includes('去重 1 行'))).toHaveLength(1);
    expect(lines[lines.length - 1]).toContain('尚未进入终态');
    expect(new Set(lines.slice(1, 1_000)).size).toBe(999); // 数据行无重复
  }, 15_000);

  it('非终态 run 即使无重复也声明"导出期快照"', async () => {
    serveEvents(1); // status 由 beforeEach 置为 RUNNING
    const lines = await renderAndExport();
    expect(lines).toHaveLength(1 + 1 + 1);
    expect(lines[lines.length - 1]).toContain('尚未进入终态');
    expect(lines.join('\n')).not.toContain('去重');
  });

  it('下载：先入文档再 click，撤销留给稍后的定时任务', async () => {
    serveEvents(1);
    lastStub = stubBlobUrl();
    const appendChild = vi.spyOn(document.body, 'appendChild');
    renderPage();
    const btn = await screen.findByTestId('event-export-csv');
    // 不吃 fake timers，只把「被调度的回调」截下来手动触发：断言的是**撤销被推迟**这件事
    // 本身（同一 tick 撤销会取消下载，Firefox 的已知失败模式），而不是时钟语义。
    const scheduled: ((...args: unknown[]) => void)[] = [];
    vi.spyOn(globalThis, 'setTimeout').mockImplementation((fn: (...a: unknown[]) => void) => {
      if (typeof fn === 'function') scheduled.push(fn);
      return 0 as unknown as ReturnType<typeof setTimeout>;
    });
    await act(async () => {
      fireEvent.click(btn);
    });
    const anchor = appendChild.mock.calls
      .map((call) => call[0] as HTMLElement)
      .find((el) => el.tagName === 'A');
    expect(anchor?.isConnected).toBe(true); // 必须先入文档：脱离文档的 <a> 部分内核不触发下载
    expect(lastStub.captured).toHaveLength(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(scheduled.length).toBeGreaterThan(0);
    scheduled.forEach((fn) => fn());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-0');
    expect(anchor?.isConnected).toBe(false); // 撤销时顺带把临时节点摘掉，不留垃圾
  });

});

// 已知缺口（本单不修，记入 Note 的 Revisit）：导出失败时 handleExportCsv 只有
// try/finally、没有 catch —— 失败变成一次 unhandled rejection，界面上没有任何提示。
// 补齐需要先定「前端如何报告动作失败」（本仓尚无统一 toast 约定），不混在 #2028 里做。


describe('PlanRunLogsPage — #2087', () => {
  it('分块导出失败：提示错误且不产出文件（不再 unhandled rejection 静默失败）', async () => {
    mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'SUCCESS' });
    // 页面自身的 limit=50 查询照常（否则事件流渲染成错误态、导出按钮不出现），
    // 只让**导出分块**（limit=EXPORT_CHUNK）失败。
    mocks.getEvents.mockImplementation(
      async (_id: number, params: { limit?: number }) => {
        if (params?.limit === EXPORT_CHUNK) throw new Error('500 Internal Server Error');
        return eventsPayload();
      },
    );
    const stub = stubBlobUrl();

    renderPage();
    const btn = await screen.findByTestId('event-export-csv');
    await act(async () => {
      fireEvent.click(btn);
    });

    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error.mock.calls[0][0]).toContain('导出失败');
    expect(stub.captured).toHaveLength(0);
    // 按钮必须恢复可用（finally 语义），否则一次失败就再也导不出
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it('导出用输入框可见值：防抖窗口内点导出不得用上一个关键词', async () => {
    mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'SUCCESS' });
    serveEvents(1);
    stubBlobUrl();
    renderPage();
    const input = await screen.findByTestId('event-search-input');

    // 输入新词后**不**等防抖生效（`search` 仍是空串）直接导出
    fireEvent.change(input, { target: { value: 'crash' } });
    const btn = screen.getByTestId('event-export-csv');
    await act(async () => {
      fireEvent.click(btn);
    });

    const exportCall = mocks.getEvents.mock.calls
      .map((call) => call[1] as { limit?: number; search?: string })
      .find((params) => params?.limit === EXPORT_CHUNK);
    expect(exportCall?.search).toBe('crash');
  });

  it('搜索值未变化（改回原词）不得复位分页', async () => {
    mocks.getRun.mockResolvedValue({ id: 12, plan_id: 7, status: 'SUCCESS' });
    serveEvents(150);
    renderPage();
    const input = await screen.findByTestId('event-search-input');

    // 1) 输入 foo 并等防抖生效
    fireEvent.change(input, { target: { value: 'foo' } });
    await waitFor(() =>
      expect(mocks.getEvents).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ search: 'foo', offset: 0 }),
      ),
    );

    // 2) 翻到第 2 页
    fireEvent.click(await screen.findByTestId('event-page-next'));
    await waitFor(() =>
      expect(mocks.getEvents).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ offset: 50 }),
      ),
    );

    // 3) 输入 foo␣ 再删回 foo：净变化为零，防抖触发时值未变 → 不得回退分页
    fireEvent.change(input, { target: { value: 'foo ' } });
    fireEvent.change(input, { target: { value: 'foo' } });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
    });

    const offsets = mocks.getEvents.mock.calls.map(
      (call) => (call[1] as { offset?: number }).offset,
    );
    expect(offsets[offsets.length - 1]).toBe(50);
  });
});
