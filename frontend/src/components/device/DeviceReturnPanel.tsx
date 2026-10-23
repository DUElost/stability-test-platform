import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/useToast';
import { api, toApiError } from '@/utils/api';
import { deviceKeys } from '@/utils/api/queryKeys';
import type {
  DeviceReturnDebtItem,
  DeviceReturnOverrideRequest,
  DeviceReturnOverrideResult,
} from '@/utils/api/types';
import { formatDateTimeShort } from '@/utils/format';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system';
import { DeviceReturnOverrideDialog } from './DeviceReturnOverrideDialog';
import {
  DEVICE_RETURN_AGGREGATE_LABELS,
  REGISTERED_FAMILIES,
  debtSourceKindLabel,
  deviceReturnAggregate,
  disarmResultLabel,
  familyLabel,
  familyReturnStateLabel,
  returnSourceLabel,
  type ReturnStateEntry,
} from './deviceReturn';

interface Props {
  deviceId: number;
  returnState?: Record<string, ReturnStateEntry> | null;
  /** 人工放行入口只对管理员显示（ADR-0061 D5.7）。 */
  isAdmin: boolean;
}

const BADGE: Record<string, string> = {
  debt: 'bg-destructive/15 text-destructive',
  unverified: 'bg-warning/20 text-warning',
  returned: 'bg-success/15 text-success',
  uninitialized: 'bg-muted text-muted-foreground',
};

function familyRank(family: string): number {
  const index = (REGISTERED_FAMILIES as readonly string[]).indexOf(family);
  return index === -1 ? REGISTERED_FAMILIES.length : index;
}

function debtSourceText(row: DeviceReturnDebtItem): string {
  const kind = debtSourceKindLabel(row.source_kind);
  return row.source_job_id != null ? kind + ' #' + row.source_job_id : kind;
}

/**
 * ADR-0061 D1.3/D5.9 / #3723 U6：设备页展开行的归还面板。
 *
 * 归还状态与来源来自 `device.return_state`；未关闭条目按需读
 * `GET /devices/{id}/return-debts`（展开才请求）。激活前三者都为空，
 * 这里必须正常渲染空态而不是报错。
 */
export function DeviceReturnPanel({ deviceId, returnState, isAdmin }: Props) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [overrideResult, setOverrideResult] = useState<DeviceReturnOverrideResult | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const debtsQuery = useQuery({
    queryKey: deviceKeys.returnDebts(deviceId),
    queryFn: () => api.devices.returnDebts(deviceId),
    staleTime: 10_000,
  });

  const mutation = useMutation({
    mutationFn: (payload: DeviceReturnOverrideRequest) =>
      api.devices.returnOverride(deviceId, payload),
    onSuccess: (result) => {
      setOverrideResult(result);
      void queryClient.invalidateQueries({ queryKey: deviceKeys.returnDebts(deviceId) });
      void queryClient.invalidateQueries({ queryKey: deviceKeys.allLists() });
      toast.success('人工放行已提交，归还状态与条目正在刷新');
    },
    onError: (error: unknown) => {
      toast.error('人工放行失败: ' + toApiError(error).message);
    },
  });

  const aggregate = deviceReturnAggregate(returnState);
  const entries = Object.entries(returnState ?? {}).sort(
    ([a], [b]) => familyRank(a) - familyRank(b),
  );
  const debts = debtsQuery.data ?? [];

  return (
    <div className="space-y-3" data-testid={'device-return-panel-' + deviceId}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium text-foreground">设备归还</span>
          <span
            data-testid={'device-return-summary-' + deviceId}
            className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', BADGE[aggregate])}
          >
            {DEVICE_RETURN_AGGREGATE_LABELS[aggregate]}
          </span>
        </div>
        {isAdmin ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={'device-return-override-' + deviceId}
            onClick={() => {
              setOverrideResult(null);
              setDialogOpen(true);
            }}
          >
            人工放行…
          </Button>
        ) : null}
      </div>

      {aggregate === 'uninitialized' ? (
        <p className={cn('text-xs', TEXT.subtitle)} data-testid={'device-return-empty-' + deviceId}>
          未初始化（阶段 B 激活前；不推断为已归还）
        </p>
      ) : (
        <div className="space-y-1" data-testid={'device-return-families-' + deviceId}>
          {entries.map(([family, entry]) => (
            <div
              key={family}
              className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded border px-2 py-1 text-xs"
              data-testid={'device-return-family-' + deviceId + '-' + family}
            >
              <span className="font-medium text-foreground">{familyLabel(family)}</span>
              <span className={cn('rounded-full px-1.5 py-0.5 text-[11px]', BADGE[entry.state] ?? 'bg-muted')}>
                {familyReturnStateLabel(entry.state)}
              </span>
              <span className={TEXT.subtitle}>来源：{returnSourceLabel(entry.source)}</span>
              <span className={TEXT.subtitle}>
                更新：{entry.updated_at ? formatDateTimeShort(entry.updated_at) : '—'}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-1">
        <div className={cn('flex items-center gap-1 text-xs font-medium', TEXT.subtitle)}>
          <AlertTriangle className="h-3.5 w-3.5" />
          未关闭条目（{debts.length}）
        </div>
        {debtsQuery.isLoading ? (
          <p className={cn('text-xs', TEXT.subtitle)}>加载中…</p>
        ) : debtsQuery.isError ? (
          <p className="text-xs text-destructive">条目加载失败</p>
        ) : debts.length === 0 ? (
          <p className={cn('text-xs', TEXT.subtitle)} data-testid={'device-return-debts-empty-' + deviceId}>
            无未关闭条目
          </p>
        ) : (
          <ul className="space-y-1" data-testid={'device-return-debts-' + deviceId}>
            {debts.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded border border-destructive/20 bg-destructive/5 px-2 py-1 text-xs"
                data-testid={'device-return-debt-' + row.id}
              >
                <span className="font-medium text-foreground">{familyLabel(row.family)}</span>
                <span className="text-destructive">{disarmResultLabel(row.cause)}</span>
                <span className={TEXT.subtitle}>来源：{debtSourceText(row)}</span>
                <span className={TEXT.subtitle}>建立：{formatDateTimeShort(row.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <DeviceReturnOverrideDialog
        isOpen={dialogOpen}
        isSubmitting={mutation.isPending}
        result={overrideResult}
        onClose={() => {
          setDialogOpen(false);
          setOverrideResult(null);
        }}
        onSubmit={(payload) => mutation.mutate(payload)}
      />
    </div>
  );
}
