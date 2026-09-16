import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PlanRunEventStream from './PlanRunEventStream';
import { ApiError } from '@/utils/api';
import type { PlanRunEventsPayload } from '@/utils/api/types';

const events: PlanRunEventsPayload = {
  plan_run_id: 12,
  total: 3,
  events: [
    {
      ts: '2026-05-08T12:01:30Z',
      stage: 'init',
      severity: 'ok',
      category: 'step',
      title: 'check_device 已就绪',
      description: '8 台设备完成 init',
    },
    {
      ts: '2026-05-08T12:30:00Z',
      stage: 'patrol',
      severity: 'err',
      category: 'step',
      title: 'monkey_check 步骤失败',
      description: 'DEV-3064 连续失败 3 次,已进入退避',
      device_serial: 'DEV-3064',
      job_id: 3064,
    },
    {
      ts: '2026-05-08T12:31:00Z',
      stage: 'system',
      severity: 'warn',
      category: 'audit',
      title: '热更新阻塞',
      description: 'Host #2 拒绝热更新 — 存在 RUNNING Job',
    },
  ],
  facets: {
    by_stage: { all: 3, init: 1, patrol: 1, system: 1, trigger: 0, teardown: 0 },
    by_severity: { all: 3, ok: 1, err: 1, warn: 1, info: 0 },
  },
};

describe('PlanRunEventStream', () => {
  it('renders events with severity badges and stage chips', () => {
    render(<PlanRunEventStream events={events} />);
    const list = screen.getByTestId('event-list');
    expect(list).toHaveTextContent('check_device 已就绪');
    expect(list).toHaveTextContent('monkey_check 步骤失败');
    expect(list).toHaveTextContent('DEV-3064');
    expect(list).toHaveTextContent('Job #3064');
  });

  it('scrolls the event list inside the panel (not the page shell)', () => {
    render(<PlanRunEventStream events={events} />);
    const root = screen.getByTestId('plan-run-event-stream');
    expect(root.className).toMatch(/\bh-full\b/);
    expect(root.className).toMatch(/\bmin-h-0\b/);
    expect(screen.getByTestId('event-list').className).toMatch(/overflow-y-auto/);
  });

  it('lifts stage and severity filter changes to parent', () => {
    const onStage = vi.fn();
    const onSev = vi.fn();
    render(
      <PlanRunEventStream
        events={events}
        onStageFilterChange={onStage}
        onSeverityFilterChange={onSev}
      />,
    );
    fireEvent.click(screen.getByTestId('event-filter-stage-patrol'));
    expect(onStage).toHaveBeenCalledWith('patrol');
    fireEvent.click(screen.getByTestId('event-filter-sev-err'));
    expect(onSev).toHaveBeenCalledWith('err');
  });

  it('shows facet counts on the filter buttons', () => {
    render(<PlanRunEventStream events={events} />);
    expect(screen.getByTestId('event-filter-stage-patrol')).toHaveTextContent('1');
    expect(screen.getByTestId('event-filter-sev-err')).toHaveTextContent('1');
  });

  it('renders empty state when there are no events under filter', () => {
    render(
      <PlanRunEventStream
        events={{
          plan_run_id: 12,
          total: 0,
          events: [],
          facets: { by_stage: { all: 0 }, by_severity: { all: 0 } },
        }}
      />,
    );
    expect(screen.getByTestId('event-list')).toHaveTextContent('该过滤条件下暂无事件');
  });

  // #2027：断言不能只停在「class 存在」——回归发生时 `line-clamp-2` 一直挂在那儿，
  // 失效的是它与同元素 display 工具类（`block`）的争夺。所以这里两头都钉：
  // 截断类挂在**没有 display 工具类**的元素上（jsdom 不算样式，这条等价判据才可测）。
  it('keeps the clamp class off any element that also carries a display utility', () => {
    render(<PlanRunEventStream events={events} />);
    const btn = screen.getByTestId('event-desc-2026-05-08T12:30:00Z-step');
    const clampEl = screen.getByTestId('event-desc-text-2026-05-08T12:30:00Z-step');

    expect(clampEl).toHaveClass('line-clamp-2');
    // 同元素不得同时出现任何 display 工具类（`block` 曾在这里把 -webkit-box 压掉）
    const DISPLAY_UTILITIES = /\b(block|inline-block|flex|inline-flex|grid|inline-grid|inline|contents|table)\b/;
    expect(clampEl.className).not.toMatch(DISPLAY_UTILITIES);
    expect(btn).not.toHaveClass('line-clamp-2');
  });

  it('expands a long event description on click', () => {
    render(<PlanRunEventStream events={events} />);
    const desc = screen.getByTestId('event-desc-2026-05-08T12:30:00Z-step');
    const clampEl = screen.getByTestId('event-desc-text-2026-05-08T12:30:00Z-step');
    expect(clampEl).toHaveClass('line-clamp-2');
    fireEvent.click(desc);
    expect(clampEl).toHaveClass('whitespace-pre-wrap');
    expect(clampEl).not.toHaveClass('line-clamp-2');
  });

  it('paginates: shows range/total and fires onPageChange on next', () => {
    const onPageChange = vi.fn();
    render(
      <PlanRunEventStream
        events={{ ...events, total: 150 }}
        page={0}
        pageSize={50}
        onPageChange={onPageChange}
      />,
    );
    const pag = screen.getByTestId('event-pagination');
    expect(pag).toHaveTextContent('150');
    expect(pag).toHaveTextContent('1-50');
    expect(screen.getByTestId('event-page-prev')).toBeDisabled();
    fireEvent.click(screen.getByTestId('event-page-next'));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it('disables next on the last page', () => {
    render(
      <PlanRunEventStream
        events={{ ...events, total: 150 }}
        page={2}
        pageSize={50}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('event-page-next')).toBeDisabled();
    expect(screen.getByTestId('event-page-prev')).not.toBeDisabled();
  });

  it('marks the active filter chips with aria-pressed (GUI 评测 a11y)', () => {
    render(
      <PlanRunEventStream
        events={events}
        stageFilter="patrol"
        severityFilter="err"
      />,
    );
    expect(screen.getByTestId('event-filter-stage-patrol')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('event-filter-stage-all')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('event-filter-sev-err')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('event-filter-sev-ok')).toHaveAttribute('aria-pressed', 'false');
  });

  it('reports search input changes and offers inline clear', () => {
    const onSearch = vi.fn();
    render(<PlanRunEventStream events={events} search="monkey" onSearchChange={onSearch} />);
    const input = screen.getByTestId('event-search-input');
    fireEvent.change(input, { target: { value: 'AEE' } });
    expect(onSearch).toHaveBeenLastCalledWith('AEE');
    // 防抖前的受控值仍在输入框（回显即时）
    expect(input).toHaveValue('monkey');
    fireEvent.click(screen.getByTestId('event-search-clear'));
    expect(onSearch).toHaveBeenLastCalledWith('');
  });

  it('keeps the list scroll gutter stable (scrollbar-gutter)', () => {
    render(<PlanRunEventStream events={events} />);
    expect(screen.getByTestId('event-list').className).toMatch(/scrollbar-gutter/);
  });

  it('description toggle is a real button with aria-expanded (原生语义自带键盘激活)', () => {
    render(<PlanRunEventStream events={events} />);
    const desc = screen.getByTestId('event-desc-2026-05-08T12:30:00Z-step');
    expect(desc.tagName).toBe('BUTTON');
    expect(desc).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(desc);
    expect(desc).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(desc);
    expect(desc).toHaveAttribute('aria-expanded', 'false');
  });

  it('empty state offers a clear-all-filters action only when filters are active', () => {
    const onStage = vi.fn();
    const onSev = vi.fn();
    const onSearch = vi.fn();
    const emptyPayload = {
      plan_run_id: 12,
      total: 0,
      events: [],
      facets: { by_stage: { all: 0 }, by_severity: { all: 0 } },
    };

    const { rerender } = render(
      <PlanRunEventStream events={emptyPayload} />,
    );
    expect(screen.queryByTestId('event-clear-filters')).not.toBeInTheDocument();

    rerender(
      <PlanRunEventStream
        events={emptyPayload}
        stageFilter="system"
        severityFilter="all"
        search=""
        onStageFilterChange={onStage}
        onSeverityFilterChange={onSev}
        onSearchChange={onSearch}
      />,
    );
    fireEvent.click(screen.getByTestId('event-clear-filters'));
    expect(onStage).toHaveBeenCalledWith('all');
    expect(onSev).toHaveBeenCalledWith('all');
    expect(onSearch).toHaveBeenCalledWith('');
  });

  it('renders CSV export button only when handler provided and honors pending state', () => {
    const onExport = vi.fn();
    const { rerender } = render(
      <PlanRunEventStream events={events} onExportCsv={onExport} />,
    );
    const btn = screen.getByTestId('event-export-csv');
    expect(btn).toHaveTextContent('导出 CSV');
    fireEvent.click(btn);
    expect(onExport).toHaveBeenCalledTimes(1);

    rerender(<PlanRunEventStream events={events} onExportCsv={onExport} isExporting />);
    expect(screen.getByTestId('event-export-csv')).toBeDisabled();
    expect(screen.getByTestId('event-export-csv')).toHaveTextContent('导出中');

    rerender(<PlanRunEventStream events={events} />);
    expect(screen.queryByTestId('event-export-csv')).not.toBeInTheDocument();
  });

  // #2361：失败分支此前只有「请检查网络连接或稍后重试」一句——404（记录被回收）
  // 也被写成了网络故障，把排查方向带偏。
  it('blames the missing record (not the network) when the error is a 404', () => {
    render(
      <PlanRunEventStream
        events={undefined}
        isError
        error={new ApiError('HTTP_404', 'plan run not found', { status: 404 })}
      />,
    );
    expect(screen.getByText('执行记录不存在或已被清理，日志无法读取。')).toBeInTheDocument();
    expect(screen.queryByText(/网络连接/)).not.toBeInTheDocument();
  });

  it('keeps the connection hint for layer-level failures without status', () => {
    render(
      <PlanRunEventStream
        events={undefined}
        isError
        error={new ApiError('NETWORK_ERROR', '网络请求失败')}
      />,
    );
    expect(screen.getByText('请检查网络连接或稍后重试')).toBeInTheDocument();
  });

  // #2027：错误面整条（图标/标题/正文）走 AA 变体令牌——原来的 `--destructive`
  // 白底 3.76:1，正文再叠 `/70` 只有 2.62:1。取值本身由
  // `src/design-system/contrast.test.ts` 按 WCAG 公式守着，这里只钉「用没用对令牌」。
  it('renders the whole error face with the AA token, never alpha-reduced destructive', () => {
    const { container } = render(
      <PlanRunEventStream events={undefined} isError error={new ApiError('TIMEOUT', '请求超时，请重试')} />,
    );
    const marked = container.querySelectorAll('.text-destructive-text');
    // 图标 + 「加载失败」标题 + 说明文字
    expect(marked.length).toBeGreaterThanOrEqual(3);
    expect(container.innerHTML).not.toMatch(/text-destructive\/\d/);
  });
});
