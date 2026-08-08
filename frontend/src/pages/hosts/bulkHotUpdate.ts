import type { Host } from '@/utils/api/types';

export interface BulkHotUpdateTarget {
  id: string | number;
  label: string;
}

export type BulkHotUpdateSkipReason =
  | 'offline'
  | 'not_installed'
  | 'active_jobs'
  | 'precheck_failed'
  | 'state_changed';

export interface BulkHotUpdateSkipped extends BulkHotUpdateTarget {
  reason: BulkHotUpdateSkipReason;
}

export const BULK_HOT_UPDATE_SKIP_LABEL: Record<BulkHotUpdateSkipReason, string> = {
  offline: '主机离线',
  not_installed: '未安装 Agent',
  active_jobs: '存在活跃 Job',
  precheck_failed: '预检失败',
  state_changed: '执行期间状态已变化',
};

async function mapConcurrent<T>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(Math.max(1, concurrency), items.length) }, async () => {
    while (cursor < items.length) {
      const item = items[cursor++];
      await worker(item);
    }
  });
  await Promise.all(workers);
}

export async function precheckBulkHotUpdate(
  targets: BulkHotUpdateTarget[],
  getDetail: (id: string | number) => Promise<Host>,
  onProgress?: (completed: number, total: number) => void,
): Promise<{ eligible: BulkHotUpdateTarget[]; skipped: BulkHotUpdateSkipped[] }> {
  const eligible: BulkHotUpdateTarget[] = [];
  const skipped: BulkHotUpdateSkipped[] = [];
  let completed = 0;

  await mapConcurrent(targets, 5, async (target) => {
    try {
      const detail = await getDetail(target.id);
      const activeCount = detail.active_job_count ?? detail.active_jobs?.length ?? 0;
      if (detail.status !== 'ONLINE') {
        skipped.push({ ...target, reason: 'offline' });
      } else if (!detail.agent_installed) {
        skipped.push({ ...target, reason: 'not_installed' });
      } else if (activeCount > 0) {
        skipped.push({ ...target, reason: 'active_jobs' });
      } else {
        eligible.push(target);
      }
    } catch {
      skipped.push({ ...target, reason: 'precheck_failed' });
    } finally {
      completed += 1;
      onProgress?.(completed, targets.length);
    }
  });

  return { eligible, skipped };
}
