/**
 * ArchiveStatusCard — ADR-0025 Sprint 3: 运行日志归档状态展示 + 立即归档按钮。
 *
 * 复用 watcher-summary 的 archive 字段（archived_jobs/total_jobs/bundles）。
 * 立即归档触发 POST /plan-runs/{id}/archive → SocketIO archive_now → Agent grace=0。
 */
import { useState } from 'react';
import { Archive, Copy, Check } from 'lucide-react';
import type { WatcherArchive } from '@/utils/api/types';

interface Props {
  archive: WatcherArchive | null | undefined;
  onArchiveNow: () => Promise<unknown>;
  isLoading?: boolean;
}

function formatSize(bytes?: number | null): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function BundleRow({ job_id, size_bytes, storage_uri }: { job_id: number; size_bytes?: number | null; storage_uri?: string | null }) {
  const [copied, setCopied] = useState(false);
  const uri = storage_uri ?? '';

  const onCopy = () => {
    if (!uri) return;
    navigator.clipboard?.writeText(uri).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="flex items-center gap-2 py-0.5 text-[11px]">
      <span className="font-mono text-gray-500">#{job_id}</span>
      <span className="text-gray-400">{formatSize(size_bytes)}</span>
      <span className="flex-1 truncate font-mono text-gray-400" title={uri}>
        {uri || '—'}
      </span>
      {uri && (
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex items-center gap-0.5 rounded border border-gray-200 bg-white px-1 py-0.5 text-[10px] text-gray-500 hover:bg-gray-50"
          data-testid="archive-copy-uri"
          title="复制归档路径"
        >
          {copied ? <Check className="h-3 w-3 text-green-600" /> : <Copy className="h-3 w-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      )}
    </div>
  );
}

export default function ArchiveStatusCard({ archive, onArchiveNow, isLoading }: Props) {
  const [archiving, setArchiving] = useState(false);

  if (!archive || archive.total_jobs === 0) {
    return null;
  }

  const pct = archive.total_jobs > 0 ? Math.round((archive.archived_jobs / archive.total_jobs) * 100) : 0;

  const handleArchiveNow = async () => {
    setArchiving(true);
    try {
      await onArchiveNow();
    } finally {
      setArchiving(false);
    }
  };

  return (
    <section
      className="rounded-xl border border-gray-200 bg-white"
      data-testid="archive-status-card"
    >
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-gray-800">
          <Archive className="h-4 w-4 text-gray-500" />
          运行日志归档
        </span>
        <button
          type="button"
          onClick={handleArchiveNow}
          disabled={archiving || isLoading}
          className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          data-testid="archive-now-button"
          title="立即归档已完成 Job 的运行日志(grace=0)"
        >
          {archiving ? '归档中...' : '立即归档'}
        </button>
      </div>

      <div className="space-y-2 p-4">
        {/* 进度条 */}
        <div className="flex items-center gap-2">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-blue-500 transition-all"
              style={{ width: `${pct}%` }}
              data-testid="archive-progress-bar"
            />
          </div>
          <span className="font-mono text-xs text-gray-600" data-testid="archive-progress">
            {archive.archived_jobs}/{archive.total_jobs} ({pct}%)
          </span>
        </div>

        {/* 归档产物列表 */}
        {archive.bundles.length > 0 && (
          <div className="space-y-0.5" data-testid="archive-bundles">
            {archive.bundles.slice(0, 10).map((b) => (
              <BundleRow key={b.artifact_id} {...b} />
            ))}
            {archive.bundles.length > 10 && (
              <p className="py-0.5 text-[11px] text-gray-400">
                ...还有 {archive.bundles.length - 10} 条
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
