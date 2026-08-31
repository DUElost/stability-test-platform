import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, Search, X } from 'lucide-react';
import { api } from '@/utils/api';
import { deviceKeys } from '@/utils/api/queryKeys';
import { FORM, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface DeviceMultiSelectProps {
  selectedIds: number[];
  onChange: (ids: number[]) => void;
}

/**
 * 设备多选（定时任务表单）——替代手填 ID 串。按 serial/model 过滤、勾选即选；
 * 已选以 chip 呈现可单独移除。全量拉取（fleet 当前规模 ~百台，虚滚无必要）。
 */
export function DeviceMultiSelect({ selectedIds, onChange }: DeviceMultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');

  const devicesQ = useQuery({
    queryKey: deviceKeys.all(),
    queryFn: () => api.devices.list(0, 1200).then((r) => r.items),
    staleTime: 60_000,
  });
  const devices = useMemo(() => devicesQ.data ?? [], [devicesQ.data]);
  const byId = useMemo(() => new Map(devices.map((d) => [d.id, d])), [devices]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter(
      (d) =>
        d.serial.toLowerCase().includes(q) ||
        (d.model ?? '').toLowerCase().includes(q),
    );
  }, [devices, search]);

  const toggle = (id: number) => {
    if (selectedIds.includes(id)) onChange(selectedIds.filter((v) => v !== id));
    else onChange([...selectedIds, id]);
  };

  return (
    <div data-testid="device-multi-select">
      {/* 已选 chips */}
      {selectedIds.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {selectedIds.map((id) => (
            <span
              key={id}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
            >
              {byId.get(id)?.serial ?? `#${id}`}
              <button
                type="button"
                onClick={() => toggle(id)}
                aria-label={`移除设备 ${byId.get(id)?.serial ?? id}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(FORM.input, 'flex items-center justify-between text-left')}
      >
        <span className={selectedIds.length ? '' : 'text-muted-foreground'}>
          {selectedIds.length ? `已选 ${selectedIds.length} 台设备` : '点击选择设备…'}
        </span>
        <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="mt-1 rounded-md border border-border bg-background">
          <div className="relative border-b border-border p-2">
            <Search className={cn('absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2', TEXT.subtitle)} />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索 serial / 型号…"
              aria-label="搜索设备"
              className={cn(FORM.input, 'py-1.5 pl-8 text-xs')}
            />
          </div>
          <div className="max-h-48 overflow-y-auto p-1">
            {devicesQ.isLoading ? (
              <p className={cn('px-3 py-2 text-xs', TEXT.subtitle)}>设备列表加载中…</p>
            ) : filtered.length === 0 ? (
              <p className={cn('px-3 py-2 text-xs', TEXT.subtitle)}>
                {devices.length === 0 ? '暂无设备' : '无匹配设备'}
              </p>
            ) : (
              filtered.map((d) => (
                <label
                  key={d.id}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-accent"
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(d.id)}
                    onChange={() => toggle(d.id)}
                    className="rounded"
                  />
                  <span className="font-mono">{d.serial}</span>
                  {d.model && <span className={TEXT.subtitle}>{d.model}</span>}
                  <span className={cn('ml-auto', TEXT.subtitle)}>#{d.id}</span>
                </label>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
