/**
 * LogEventsCard — #529 终态 PlanRun DLE 事件视图（归档权威）。
 *
 * 跑测中 watcher-summary 读 job_log_signal 是为及时性；PlanRun **终态后**
 * 用户关心的是事件是否已上中心、extract 路径、LOCAL/REMOTE/ARCHIVED 状态。
 * 这些以 ``device_log_event`` 为权威（ADR-0028），本卡只读该端点。
 *
 * 只在终态启用（isTerminal），RUNNING 时不强制切 DLE（不变量见 Epic #527）。
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileStack, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import { PANEL, SEGMENTED, STATUS_CHIP, TEXT } from '@/design-system';
import { InlineEmpty } from '@/components/ui/empty-state';
import { InlineError } from '@/components/ui/error-state';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { formatLocalDateTime } from '@/utils/format';
import { SLOW_REFETCH_MS } from '@/hooks/plan-run/planRunDetailUtils';

interface Props {
  runId: number;
  /** 仅终态启用；RUNNING 时组件不触发请求。 */
  isTerminal: boolean;
}

/** #1194：单页条数；「加载更多」按页放大 limit（后端 skip/limit 窗口读）。 */
const PAGE_SIZE = 200;
/** 后端 `GET /plan-runs/{id}/log-events` 的 `limit` 硬上限（`Query(..., le=500)`）。 */
const MAX_LIMIT = 500;

/**
 * DLE 状态 → chip 语义色（#2184 补齐四态）。
 *
 * 补齐的四态此前全部落到 `STATUS_CHIP.muted` 兜底 = "没信息"，而它们恰好回答
 * 「卡在哪 / 要不要处理」这个唯一的问题：
 * - `UPLOADING` 在途——与 `LOCAL` 同属"尚未上中心、但非异常"的中性态；
 * - `UPLOAD_FAILED` / `PULL_FAILED` 真失败——destructive；
 * - `PRUNED` 已按 retention 清理——终态**不是异常**，故意用 muted 而非 destructive，
 *   否则清理动作会被读成故障。
 */
/**
 * 状态口径说明（#2150 裁决 B）：`REMOTE` 是否终态取决于**该 run 有无 merge 产物**。
 *
 * 归档（`REMOTE → ARCHIVED`）由 run 级 extract 唯一驱动，而 extract 以本轮存在
 * `merge_result_xls` 为前置——无 scan 产物的平台（UNIVIEW）因此恒不归档。
 * 判据与两段式排查口径见 `docs/design/2026-scan-upload-merge-contract.md`
 * （「DLE 归档的触发与判据」一节）。文案面向运维：说清「不是卡住」。
 */
const STATE_HINT: Record<string, string> = {
  REMOTE:
    '已上送中心，等待 run 级归档。注意：归档由该 run 的 extract 执行——若该 run 无 merge 产物'
    + '（如 UNIVIEW 无 scan 产物的轮次）则不会归档，此时 REMOTE 即终态，不是卡住。',
  ARCHIVED: '已复制进该 run 的归档包（中心存储 jira/{run_id}/）。',
};

const STATE_CHIP: Record<string, string> = {
  DETECTED: STATUS_CHIP.muted,
  LOCAL: STATUS_CHIP.primary,
  UPLOAD_PENDING: STATUS_CHIP.warning,
  UPLOADING: STATUS_CHIP.primary,
  UPLOAD_FAILED: STATUS_CHIP.destructive,
  PULL_FAILED: STATUS_CHIP.destructive,
  REMOTE: STATUS_CHIP.success,
  ARCHIVED: STATUS_CHIP.success,
  PRUNED: STATUS_CHIP.muted,
};

/** 路径展示优先 remote_path，回落 local_path（#529 口径）。 */
function displayPath(event: { remote_path?: string | null; local_path?: string | null }): string {
  return event.remote_path || event.local_path || '—';
}

export default function LogEventsCard({ runId, isTerminal }: Props) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  /** #2184：平台筛选（`undefined` = 全部）。**服务端**过滤，见 `services/device_log_event`。 */
  const [platform, setPlatform] = useState<string | undefined>(undefined);
  const q = useQuery({
    queryKey: planRunKeys.logEvents(runId, { limit, platform }),
    queryFn: () => api.planRuns.getLogEvents(runId, { skip: 0, limit, platform }),
    enabled: !!runId && isTerminal,
    // #1193：终态后 scan/upload/merge/extract 仍可能继续投递，慢轮询保持可见；
    // 标签页失焦时 React Query 默认暂停轮询，页面关闭即停止。
    refetchInterval: SLOW_REFETCH_MS,
  });

  const total = q.data?.total ?? 0;
  const loaded = q.data?.items.length ?? 0;
  const hasMore = loaded < total;
  const atLimitCap = limit >= MAX_LIMIT;

  /**
   * 平台筛选项 = **服务端返回的全集**（#2288）∪ 当前筛选值，无记忆态。
   *
   * 旧做法是从「已加载行」派生：某 run 最新 500 条恰好都是同一平台（单平台主导很常见，
   * 排序 `detected_at DESC`）时只剩一个选项 → 整行筛选**隐藏**，而「加载更多」受
   * `MAX_LIMIT=500` 所限也翻不到另一平台 → 筛选恰在最需要的时候不可用。全集由
   * `list_plan_run_device_log_event_platforms` 从库里 DISTINCT 出来，既不会漏平台，
   * 也不会出现"点了某平台却 0 条"的死选项（集合里每个平台都至少有一行）。
   * `platforms` 缺失（响应未带该键）时退回按已加载行派生，不塌成空筛选。
   *
   * 下面的渲染条件 `platformOptions.length > 1` **保持不变且现在才成立**：真全集只有
   * 一个平台时确实无事可筛；旧集合派生自己加载窗口时，同一条件会在多平台 run 上
   * 误判成「只有一个平台」而整行隐藏——那才是缺陷本体（条件不是，集合来源才是）。
   */
  const platformOptions = [
    ...new Set<string>(
      [
        ...(q.data?.platforms ?? (q.data?.items ?? []).map((ev) => ev.platform)),
        ...(platform ? [platform] : []),
      ].filter(Boolean),
    ),
  ].sort();

  /** 切平台时把分页窗口收回首页，避免把上一筛选放大过的 limit 带过去。 */
  const selectPlatform = (next: string | undefined) => {
    if (next === platform) return;
    setPlatform(next);
    setLimit(PAGE_SIZE);
  };

  return (
    <section className={PANEL.root} data-testid="log-events-card">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className={cn('flex items-center gap-1.5 text-sm font-semibold', TEXT.heading)}>
          <FileStack className={cn('h-4 w-4', TEXT.subtitle)} />
          日志事件归档（DLE）
        </span>
        <span className="flex items-center gap-2">
          {total > 0 && (
            <span className={cn('text-[11px]', TEXT.subtitle)} data-testid="log-events-count">
              已显示 {loaded} / {total}
            </span>
          )}
          {q.isFetching && (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> 刷新中
            </span>
          )}
        </span>
      </div>

      {(platform || platformOptions.length > 1) && (
        <div
          className="flex items-center gap-2 border-b border-border/50 px-3 py-1.5"
          data-testid="log-events-platform-filter"
        >
          <span className={cn('text-[11px]', TEXT.subtitle)}>平台</span>
          <div className={SEGMENTED.track}>
            <button
              type="button"
              className={cn(SEGMENTED.item, !platform && SEGMENTED.itemActive)}
              onClick={() => selectPlatform(undefined)}
            >
              全部
            </button>
            {platformOptions.map((p) => (
              <button
                key={p}
                type="button"
                className={cn(SEGMENTED.item, platform === p && SEGMENTED.itemActive)}
                onClick={() => selectPlatform(p)}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
      )}

      {q.isError ? (
        <div className="px-3 py-2.5">
          <InlineError message="日志事件归档加载失败" onRetry={() => void q.refetch()} />
        </div>
      ) : q.isLoading ? (
        <p className="px-3 py-2.5 text-xs text-muted-foreground">加载中…</p>
      ) : !q.data || total === 0 ? (
        <div className="px-3 py-2.5">
          {/* #2184：筛到 0 条与"本来就没有"必须可区分，否则会被读成"这份 run 没日志"。 */}
          <InlineEmpty>
            {platform ? `平台 ${platform} 无 device_log_event 记录` : '无 device_log_event 记录'}
          </InlineEmpty>
        </div>
      ) : (
        <>
          <div className="max-h-[360px] overflow-y-auto">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-muted/50">
                <tr className={cn('text-left', TEXT.subtitle)}>
                  <th className="px-4 py-1.5 font-medium">序列号</th>
                  <th className="px-2 py-1.5 font-medium">平台</th>
                  <th className="px-2 py-1.5 font-medium">类型</th>
                  <th className="px-2 py-1.5 font-medium">状态</th>
                  <th className="px-2 py-1.5 font-medium">路径</th>
                  <th className="px-4 py-1.5 font-medium text-right">检测时间</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((ev) => (
                  <tr key={ev.id} className="border-t border-border/50">
                    <td className="px-4 py-1.5 font-mono">{ev.serial}</td>
                    <td className="px-2 py-1.5 font-mono" data-testid="log-event-platform">
                      {ev.platform}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {ev.event_type}
                      {ev.event_subtype ? ` · ${ev.event_subtype}` : ''}
                    </td>
                    <td className="px-2 py-1.5" title={STATE_HINT[ev.state]}>
                      <span className={cn(
                        'inline-flex items-center px-1.5 py-px rounded-full text-[11px] font-bold',
                        STATE_CHIP[ev.state] ?? STATUS_CHIP.muted,
                      )}>
                        {ev.state}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 font-mono max-w-[260px] truncate" title={displayPath(ev)}>
                      {displayPath(ev)}
                    </td>
                    <td className="px-4 py-1.5 text-right whitespace-nowrap">
                      {formatLocalDateTime(ev.detected_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore && !atLimitCap && (
            <div className="border-t border-border/50 p-1.5 text-center">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setLimit((l) => Math.min(l + PAGE_SIZE, MAX_LIMIT))}
              >
                加载更多（还有 {total - loaded} 条）
              </Button>
            </div>
          )}
          {hasMore && atLimitCap && (
            <p className="border-t border-border/50 p-1.5 text-center text-[11px] text-muted-foreground">
              已达接口单次上限 {MAX_LIMIT} 条（共 {total} 条），无法继续加载
            </p>
          )}
        </>
      )}
    </section>
  );
}
