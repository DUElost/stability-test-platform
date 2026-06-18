import { useEffect, useMemo, useState } from 'react';
import { Archive, Check, Copy } from 'lucide-react';
import { planRuns } from '@/utils/api/planRuns';
import type {
  WatcherArchive,
  WatcherArchiveBundle,
  WatcherTimeScope,
} from '@/utils/api/types';

interface Props {
  archive: WatcherArchive;
  runId?: number;
  timeScope?: WatcherTimeScope;
}

function fmtSize(bytes: number | null | undefined): string {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function ArchiveBundleRow({ bundle }: { bundle: WatcherArchiveBundle }) {
  const [copied, setCopied] = useState(false);
  const uri = bundle.storage_uri ?? '';

  const onCopy = () => {
    if (!uri) return;
    void navigator.clipboard?.writeText(uri).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div
      className="flex items-center gap-1.5 text-[11px] text-gray-600"
      data-testid="archive-bundle-row"
    >
      <Archive className="h-3 w-3 shrink-0 text-gray-400" />
      <span className="shrink-0 font-mono">Job #{bundle.job_id}</span>
      {bundle.size_bytes != null && (
        <span className="shrink-0 text-gray-400">({fmtSize(bundle.size_bytes)})</span>
      )}
      <span
        className="truncate font-mono text-gray-400"
        title={uri}
        data-testid="archive-bundle-uri"
      >
        {uri || '—'}
      </span>
      {uri && (
        <button
          type="button"
          onClick={onCopy}
          className="ml-auto inline-flex shrink-0 items-center gap-1 rounded border border-gray-300 bg-white px-1.5 py-0.5 text-gray-600 hover:bg-gray-100"
          data-testid="archive-bundle-copy"
          title="复制归档路径"
        >
          {copied ? <Check className="h-3 w-3 text-green-600" /> : <Copy className="h-3 w-3" />}
          {copied ? '已复制' : '复制路径'}
        </button>
      )}
    </div>
  );
}

/**
 * 运行日志归档列表（ADR-0025 Sprint 3 / #14）。
 * 支持 watcher-summary archive 分页「加载更多」。
 */
export default function RunLogArchiveSection({
  archive,
  runId,
  timeScope = 'all',
}: Props) {
  const [extraBundles, setExtraBundles] = useState<WatcherArchiveBundle[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);

  // 父级 refetch（Socket invalidate / react-query）会替换 archive.bundles 第一页；
  // 用第一页 artifact_id 序列作签名，内容变化时清空「加载更多」累积，避免与陈旧分页叠加。
  const parentPageSignature = useMemo(
    () =>
      [
        archive.bundles_total ?? 0,
        archive.archived_jobs,
        archive.bundles_offset ?? 0,
        archive.bundles_limit ?? 20,
        ...archive.bundles.map((b) => b.artifact_id),
      ].join(','),
    [archive],
  );

  useEffect(() => {
    setExtraBundles([]);
  }, [runId, timeScope, parentPageSignature]);

  const allBundles = useMemo(
    () => [...archive.bundles, ...extraBundles],
    [archive.bundles, extraBundles],
  );
  const bundlesTotal = archive.bundles_total ?? allBundles.length;
  const hasMore = allBundles.length < bundlesTotal;
  const canLoadMore = hasMore && runId != null;

  const onLoadMore = async () => {
    if (!runId || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await planRuns.getWatcherSummary(runId, timeScope, {
        archive_offset: allBundles.length,
        archive_limit: archive.bundles_limit ?? 20,
      });
      if (next.archive?.bundles?.length) {
        setExtraBundles((prev) => [...prev, ...next.archive!.bundles]);
      }
    } finally {
      setLoadingMore(false);
    }
  };

  if (archive.total_jobs <= 0) return null;

  return (
    <div className="border-t bg-gray-50 px-4 py-2" data-testid="watcher-archive-section">
      <div className="mb-1 flex items-center justify-between text-[11px] text-gray-500">
        <span className="flex items-center gap-1">
          <Archive className="h-3 w-3" />
          运行日志归档
        </span>
        <span className="font-mono" data-testid="archive-progress">
          {archive.archived_jobs}/{archive.total_jobs} 已归档
        </span>
      </div>
      {allBundles.length > 0 && (
        <div className="flex flex-col gap-1">
          {allBundles.map((b) => (
            <ArchiveBundleRow key={b.artifact_id} bundle={b} />
          ))}
        </div>
      )}
      {canLoadMore && (
        <button
          type="button"
          data-testid="archive-load-more"
          disabled={loadingMore}
          onClick={() => void onLoadMore()}
          className="mt-2 w-full rounded border border-dashed border-gray-300 py-1 text-[11px] text-gray-500 hover:border-gray-400 hover:text-gray-700 disabled:opacity-50"
        >
          {loadingMore
            ? '加载中…'
            : `加载更多 (${allBundles.length}/${bundlesTotal})`}
        </button>
      )}
    </div>
  );
}
