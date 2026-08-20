import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState } from '@/components/ui/error-state';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableEmptyRow,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { InventoryModel, InventorySummary } from '@/utils/api/types';
import {
  backfillLabelKeys,
  formatModelLabel,
  hasManualMapping,
  unassignedDeviceCount,
} from '../inventoryDisplay';

type Props = {
  models: InventoryModel[] | undefined;
  summary: InventorySummary | undefined;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
};

function BackfillCell({ row }: { row: InventoryModel }) {
  const keys = backfillLabelKeys(row);
  const showLegacy = row.legacy_device_count > 0;
  const showNull = row.null_device_count > 0;
  if (keys.length === 0 && !showLegacy && !showNull) {
    return <span className={TEXT.subtitle}>—</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {keys.map((key) => (
        <Badge
          key={key}
          variant="secondary"
          className="font-mono text-[11px] font-normal"
          title="P1 回填标签：非正式编组，不代表客户、项目或机型"
        >
          {key}
        </Badge>
      ))}
      {showLegacy && (
        <Badge variant="warning" className="text-[11px] font-normal">
          未分配（LEGACY）
        </Badge>
      )}
      {showNull && (
        <Badge variant="warning" className="text-[11px] font-normal">
          无归属（NULL）
        </Badge>
      )}
    </div>
  );
}

function MappingCell({ row }: { row: InventoryModel }) {
  if (!hasManualMapping(row)) {
    return (
      <span className={cn('text-xs', TEXT.subtitle)} data-testid="mapping-pending">
        待手动填写
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {row.mapped_project_keys.map((key) => (
        <Badge key={key} variant="outline" className="font-mono text-[11px] font-normal">
          {key}
        </Badge>
      ))}
    </div>
  );
}

export default function InventoryModelsTable({
  models,
  summary,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: Props) {
  const [platformFilter, setPlatformFilter] = useState<string | undefined>();
  const [unassignedOnly, setUnassignedOnly] = useState(false);

  const platformOptions = useMemo(() => {
    const values = new Set<string>();
    for (const row of models ?? []) {
      for (const platform of row.platforms) values.add(platform);
    }
    return Array.from(values).sort();
  }, [models]);

  const filtered = useMemo(() => {
    return (models ?? []).filter((row) => {
      if (platformFilter && !row.platforms.includes(platformFilter)) return false;
      if (unassignedOnly && unassignedDeviceCount(row) === 0) return false;
      return true;
    });
  }, [models, platformFilter, unassignedOnly]);

  return (
    <Card data-testid="inventory-models">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm font-medium">Fleet 型号分布</CardTitle>
            <p className={cn('mt-1 text-xs', TEXT.subtitle)}>
              型号 / platform 来自设备心跳。已映射项目需人工填写；HONOR-MLD、ZTE-Z258
              等只是系统回填标签，不能代表客户、项目或机型。
              {summary
                ? ` ${summary.distinct_models} 种型号 · ${summary.legacy_devices} 台 LEGACY · ${summary.null_devices} 台无归属`
                : null}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={platformFilter ?? 'all'}
              onValueChange={(value) =>
                setPlatformFilter(value === 'all' ? undefined : value)
              }
            >
              <SelectTrigger data-testid="inventory-platform" className="h-8 w-[140px]">
                <SelectValue placeholder="平台" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部平台</SelectItem>
                {platformOptions.map((platform) => (
                  <SelectItem key={platform} value={platform}>
                    {platform}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <label
              className={cn(
                'inline-flex h-8 cursor-pointer select-none items-center gap-1.5 rounded-lg border bg-card px-2.5 text-xs',
                unassignedOnly && 'border-primary/40 bg-primary/10 text-primary',
              )}
            >
              <input
                type="checkbox"
                className="accent-primary"
                data-testid="inventory-unassigned-only"
                checked={unassignedOnly}
                onChange={(event) => setUnassignedOnly(event.target.checked)}
              />
              仅未分配
            </label>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : isError ? (
          <ErrorState
            title="加载型号分布失败"
            description={errorMessage || '请检查网络连接或稍后重试'}
            onRetry={onRetry}
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>型号</TableHead>
                <TableHead className="w-20">台数</TableHead>
                <TableHead className="w-28">platform</TableHead>
                <TableHead>回填标签（非正式）</TableHead>
                <TableHead>已映射项目</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.length === 0 ? (
                <TableEmptyRow colSpan={5}>
                  {models?.length ? '没有匹配的型号' : '暂无设备上报型号'}
                </TableEmptyRow>
              ) : (
                filtered.map((row) => (
                  <TableRow key={row.model ?? '__blank__'} data-testid="inventory-model-row">
                    <TableCell className="font-mono text-xs">
                      {formatModelLabel(row.model)}
                    </TableCell>
                    <TableCell>{row.device_count}</TableCell>
                    <TableCell className={cn('text-xs', TEXT.subtitle)}>
                      {row.platforms.length ? row.platforms.join(', ') : '—'}
                    </TableCell>
                    <TableCell>
                      <BackfillCell row={row} />
                    </TableCell>
                    <TableCell>
                      <MappingCell row={row} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
