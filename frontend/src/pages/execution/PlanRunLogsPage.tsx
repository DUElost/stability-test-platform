import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle } from 'lucide-react';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import type { EventSeverity, EventStage, PlanRunEvent, PlanRunStatus } from '@/utils/api/types';
import PlanRunEventStream from '@/components/plan-run/PlanRunEventStream';
import { PageContainer } from '@/components/layout';
import { TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { usePlanRunHeaderSlot } from '@/hooks/plan-run/usePlanRunHeaderSlot';

const PAGE_SIZE = 50;
const SLOW_REFETCH_MS = 30_000;
const SEARCH_DEBOUNCE_MS = 300;
/** 后端单页上限（_MAX_EVENTS_LIMIT），导出按此分块拉取 */
const EXPORT_CHUNK = 500;
/** 导出行数硬上限——防误触全量拉取拖垮浏览器 */
const EXPORT_MAX_ROWS = 20_000;
const TERMINAL: ReadonlyArray<PlanRunStatus> = [
  'SUCCESS',
  'PARTIAL_SUCCESS',
  'FAILED',
];

function csvCell(value: string | number | null | undefined): string {
  const s = value == null ? '' : String(value);
  return `"${s.replace(/"/g, '""')}"`;
}

const CSV_HEADER = ['时间', '阶段', '严重度', '类别', '标题', '描述', '设备序列号', 'Job ID'];

function csvRow(values: (string | number | null | undefined)[]): string {
  return values.map(csvCell).join(',');
}

/**
 * 事件行身份（#2028 去重用）。
 *
 * 后端事件是多源合成的**投影**，没有 id 列（`backend/api/routes/plan_runs.py` 的
 * `/events` 逐段拼装），所以只能按「渲染进 CSV 的全部字段 + 来源行标识」取键：
 * `ref` 带来源表的行 id（audit_log 等），能区分「内容相同但确是两条」的情形。
 * 真撞键的两种行在 CSV 里逐字节相同，去掉后一条不损失任何信息。
 */
function eventRowKey(e: PlanRunEvent): string {
  return JSON.stringify([
    e.ts, e.stage, e.severity, e.category, e.title, e.description ?? '',
    e.device_serial ?? '', e.job_id ?? '', e.device_id ?? '',
    e.ref?.type ?? '', e.ref?.id ?? '',
  ]);
}

/** 巡检日志页面 — 阶段/严重度过滤 + 关键字搜索 + 分页事件流(多源融合) + CSV 导出。 */
export default function PlanRunLogsPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const qc = useQueryClient();

  const [stageFilter, setStageFilter] = useState<EventStage | 'all'>('all');
  const [severityFilter, setSeverityFilter] = useState<EventSeverity | 'all'>('all');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState(''); // 防抖后的实际查询关键字
  const [page, setPage] = useState(0); // 0-based,与 PlanRunEventStream 对齐
  const [isExporting, setIsExporting] = useState(false);

  // 300ms 防抖：输入即时回显，查询按稳定值发起
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setSearch(searchInput);
      setPage(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [searchInput]);

  const runQ = useQuery({
    queryKey: planRunKeys.detail(id),
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
    // #823：runQ 一次性读取会让 isTerminal 永不推进——run 已结束后 eventsQ 仍每 30s
    // 对终态 run 拉取。非终态慢轮询推进，终态即停（与 eventsQ 停更条件对齐）。
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && TERMINAL.includes(status) ? false : SLOW_REFETCH_MS;
    },
  });
  const isTerminal = !!runQ.data && TERMINAL.includes(runQ.data.status);

  const eventsQ = useQuery({
    queryKey: planRunKeys.logs(id, stageFilter, severityFilter, page, search),
    queryFn: () =>
      api.planRuns.getEvents(id, {
        stage: stageFilter,
        severity: severityFilter,
        search: search.trim() || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  const refreshAll = useCallback(() => {
    qc.invalidateQueries({ queryKey: planRunKeys.detail(id) });
    qc.invalidateQueries({ queryKey: planRunKeys.logsByRun(id) });
  }, [qc, id]);

  const isAnyFetching = runQ.isFetching || eventsQ.isFetching;
  const dataUpdatedAt = Math.max(runQ.dataUpdatedAt, eventsQ.dataUpdatedAt);

  usePlanRunHeaderSlot({
    runId: id,
    active: 'logs',
    dataUpdatedAt,
    isAnyFetching,
    refreshAll,
  });

  useDocumentTitle(
    runQ.data?.plan_name
      ? `${runQ.data.plan_name} · 日志`
      : id
        ? `Plan Run #${id} · 日志`
        : 'Plan Run 日志',
  );

  const handleStageChange = useCallback((s: EventStage | 'all') => {
    setStageFilter(s);
    setPage(0);
  }, []);

  const handleSeverityChange = useCallback((s: EventSeverity | 'all') => {
    setSeverityFilter(s);
    setPage(0);
  }, []);

  const handleExportCsv = useCallback(async () => {
    if (!id || isExporting) return;
    setIsExporting(true);
    try {
      const kw = search.trim();
      const rows: PlanRunEvent[] = [];
      const seen = new Set<string>();
      let duplicates = 0;
      // 是否"读完了"。false = 被 EXPORT_MAX_ROWS 挡住，即 CSV 少了一截（#2028 静默截断）
      let exhausted = false;
      for (let offset = 0; offset < EXPORT_MAX_ROWS; offset += EXPORT_CHUNK) {
        const payload = await api.planRuns.getEvents(id, {
          stage: stageFilter,
          severity: severityFilter,
          search: kw || undefined,
          limit: EXPORT_CHUNK,
          offset,
        });
        for (const e of payload.events) {
          const key = eventRowKey(e);
          if (seen.has(key)) {
            // 后端是 `ts DESC` + offset 分页，run 在途时新事件插到头部会使窗口位移，
            // 于是同一条在相邻两块里各出现一次（#2028 重复行）。
            duplicates += 1;
            continue;
          }
          seen.add(key);
          rows.push(e);
        }
        if (payload.events.length < EXPORT_CHUNK || offset + EXPORT_CHUNK >= payload.total) {
          exhausted = true;
          break;
        }
      }

      const lines = [csvRow(CSV_HEADER)];
      for (const e of rows) {
        lines.push(csvRow([
          e.ts, e.stage, e.severity, e.category, e.title,
          e.description ?? '', e.device_serial ?? '', e.job_id ?? '',
        ]));
      }
      // 三行"如实报告"尾注（#2028）：宁可让文件里多一行说明，也不要让使用者以为
      // 拿到的是全量——上限命中此前完全静默。
      if (!exhausted) {
        lines.push(csvCell(`注意：命中导出上限 ${EXPORT_MAX_ROWS} 行，其余事件未导出——请缩小筛选范围或按时间分批导出`));
      }
      if (duplicates > 0) {
        lines.push(csvCell(`注意：导出期间去重 ${duplicates} 行（该 run 仍在写入，分页窗口位移导致同一条重复出现）`));
      }
      if (!isTerminal) {
        lines.push(csvCell('注意：该 run 尚未进入终态，本文件是导出期间的快照，末尾可能不含最新事件'));
      }
      // BOM 让 Excel 正确识别 UTF-8 中文
      const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `planrun-${id}-events-${stamp}.csv`;
      a.style.display = 'none';
      // 必须先入文档再 click：脱离文档的 <a> 在部分内核里不触发下载
      document.body.appendChild(a);
      a.click();
      // 同一 tick 撤销会让下载被取消（Firefox 已知失败模式）——留到下一个 tick
      setTimeout(() => {
        a.remove();
        URL.revokeObjectURL(url);
      }, 0);
    } finally {
      setIsExporting(false);
    }
  }, [id, search, stageFilter, severityFilter, isExporting, isTerminal]);

  if (!id || Number.isNaN(id)) {
    return (
      <div className={cn('flex h-64 items-center justify-center text-sm', TEXT.subtitle)}>
        <AlertCircle className="mr-2 h-4 w-4" /> 无效 PlanRun ID
      </div>
    );
  }

  return (
    <PageContainer width="bleed" scrollable={false} className="min-h-0">
      <PlanRunEventStream
        events={eventsQ.data}
        stageFilter={stageFilter}
        severityFilter={severityFilter}
        onStageFilterChange={handleStageChange}
        onSeverityFilterChange={handleSeverityChange}
        search={searchInput}
        onSearchChange={setSearchInput}
        onExportCsv={handleExportCsv}
        isExporting={isExporting}
        isLoading={eventsQ.isLoading}
        isError={eventsQ.isError}
        page={page}
        pageSize={PAGE_SIZE}
        onPageChange={setPage}
      />
    </PageContainer>
  );
}
