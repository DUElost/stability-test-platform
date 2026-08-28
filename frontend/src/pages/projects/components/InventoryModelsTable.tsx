import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState } from '@/components/ui/error-state';
import { Skeleton } from '@/components/ui/skeleton';
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
import { formatModelLabel, isMapped, selectableModel } from '../inventoryDisplay';

type Props = {
  models: InventoryModel[] | undefined;
  summary: InventorySummary | undefined;
  selectedModels: string[];
  onSelectedModelsChange: (models: string[]) => void;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
};

function MappingCell({ row }: { row: InventoryModel }) {
  if (!isMapped(row)) {
    return (
      <span className={cn('text-xs', TEXT.subtitle)} data-testid="mapping-pending">
        未映射
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

function facetChipClass(active: boolean): string {
  return cn(
    'rounded-full border px-2.5 py-0.5 text-xs transition-colors',
    active
      ? 'border-primary/40 bg-accent font-medium text-foreground'
      : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
  );
}

export default function InventoryModelsTable({
  models,
  summary,
  selectedModels,
  onSelectedModelsChange,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: Props) {
  const [platformFilter, setPlatformFilter] = useState<string | undefined>();
  const [unmappedOnly, setUnmappedOnly] = useState(false);

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
      if (unmappedOnly && isMapped(row)) return false;
      return true;
    });
  }, [models, platformFilter, unmappedOnly]);

  const selectableFiltered = filtered
    .map(selectableModel)
    .filter((model): model is string => model !== null);

  const allFilteredSelected =
    selectableFiltered.length > 0 &&
    selectableFiltered.every((model) => selectedModels.includes(model));

  const toggleOne = (model: string) => {
    if (selectedModels.includes(model)) {
      onSelectedModelsChange(selectedModels.filter((item) => item !== model));
      return;
    }
    onSelectedModelsChange([...selectedModels, model]);
  };

  const toggleFiltered = () => {
    if (allFilteredSelected) {
      onSelectedModelsChange(
        selectedModels.filter((model) => !selectableFiltered.includes(model)),
      );
      return;
    }
    onSelectedModelsChange(Array.from(new Set([...selectedModels, ...selectableFiltered])));
  };

  return (
    <Card data-testid="inventory-models">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-sm font-medium">Fleet 型号分布</CardTitle>
            <p className={cn('mt-1 text-xs', TEXT.subtitle)}>
              设备心跳采集的型号清单，勾选行后可批量归入上方项目。
              {summary
                ? ` 共 ${summary.distinct_models} 种型号 · ${summary.user_mapped_devices} 台已映射 · ${summary.unmapped_models.length} 种待映射`
                : null}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div
              role="group"
              aria-label="平台筛选"
              data-testid="inventory-platform"
              className="flex flex-wrap items-center gap-1.5"
            >
              <button
                type="button"
                data-testid="inventory-platform-all"
                aria-pressed={!platformFilter}
                onClick={() => setPlatformFilter(undefined)}
                className={facetChipClass(!platformFilter)}
              >
                全部
              </button>
              {platformOptions.map((platform) => (
                <button
                  key={platform}
                  type="button"
                  data-testid={`inventory-platform-${platform}`}
                  aria-pressed={platformFilter === platform}
                  onClick={() =>
                    setPlatformFilter(platformFilter === platform ? undefined : platform)
                  }
                  className={facetChipClass(platformFilter === platform)}
                >
                  {platform}
                </button>
              ))}
            </div>
            <label
              className={cn(
                'inline-flex h-8 cursor-pointer select-none items-center gap-1.5 rounded-lg border bg-card px-2.5 text-xs',
                unmappedOnly && 'border-primary/40 bg-primary/10 text-primary',
              )}
            >
              <input
                type="checkbox"
                className="accent-primary"
                data-testid="inventory-unmapped-only"
                checked={unmappedOnly}
                onChange={(event) => setUnmappedOnly(event.target.checked)}
              />
              仅未映射
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
                <TableHead className="w-10">
                  <input
                    type="checkbox"
                    className="accent-primary"
                    data-testid="inventory-select-all"
                    checked={allFilteredSelected}
                    disabled={selectableFiltered.length === 0}
                    onChange={toggleFiltered}
                    aria-label="选择当前筛选下的全部型号"
                  />
                </TableHead>
                <TableHead>型号</TableHead>
                <TableHead className="w-20">台数</TableHead>
                <TableHead className="w-28">platform</TableHead>
                <TableHead>已映射项目</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.length === 0 ? (
                <TableEmptyRow colSpan={5}>
                  {models?.length ? '没有匹配的型号' : '暂无设备上报型号'}
                </TableEmptyRow>
              ) : (
                filtered.map((row) => {
                  const model = selectableModel(row);
                  const checked = model !== null && selectedModels.includes(model);
                  return (
                    <TableRow key={row.model ?? '__blank__'} data-testid="inventory-model-row">
                      <TableCell>
                        <input
                          type="checkbox"
                          className="accent-primary"
                          data-testid="inventory-model-check"
                          disabled={model === null}
                          checked={checked}
                          onChange={() => model && toggleOne(model)}
                          aria-label={formatModelLabel(row.model)}
                        />
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {formatModelLabel(row.model)}
                      </TableCell>
                      <TableCell>{row.device_count}</TableCell>
                      <TableCell className={cn('text-xs', TEXT.subtitle)}>
                        {row.platforms.length ? row.platforms.join(', ') : '—'}
                      </TableCell>
                      <TableCell>
                        <MappingCell row={row} />
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
