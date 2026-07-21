import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { BookmarkPlus, Trash2 } from 'lucide-react';
import type { PlanExecutePreset } from './planExecutePresets';

interface SelectionPresetsProps {
  presets: PlanExecutePreset[];
  selectedCount: number;
  onSave: (name: string) => void;
  onApply: (preset: PlanExecutePreset) => void;
  onDelete: (presetId: string) => void;
}

export function SelectionPresets({
  presets,
  selectedCount,
  onSave,
  onApply,
  onDelete,
}: SelectionPresetsProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');

  const defaultName = useMemo(() => {
    const today = new Date();
    const md = `${today.getMonth() + 1}/${today.getDate()}`;
    return `选机方案 ${md} · ${selectedCount} 台`;
  }, [selectedCount]);

  const openSave = () => {
    setName(defaultName);
    setOpen(true);
  };

  const confirmSave = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onSave(trimmed);
    setOpen(false);
  };

  return (
    <div className="rounded-lg border p-3" data-testid="selection-presets">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium">我的选机方案</div>
          <div className={cn('text-xs', TEXT.subtitle)}>
            个人常用组合（localStorage）· 应用时与可调度集求交
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 gap-1 px-2 text-xs"
          disabled={selectedCount === 0}
          onClick={openSave}
        >
          <BookmarkPlus className="h-3 w-3" />
          存为方案
        </Button>
      </div>

      {presets.length === 0 ? (
        <div className={cn('py-3 text-center text-xs', TEXT.subtitle)}>
          暂无方案。圈选后点「存为方案」可快速复用。
        </div>
      ) : (
        <ul className="space-y-1">
          {presets.map((preset) => (
            <li
              key={preset.id}
              className="flex items-center gap-2 rounded-md border border-transparent px-2 py-1.5 hover:border-border hover:bg-muted/40"
              data-testid={`selection-preset-${preset.id}`}
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => onApply(preset)}
              >
                <div className="truncate text-sm font-medium">{preset.name}</div>
                <div className={cn('text-[11px]', TEXT.subtitle)}>{preset.deviceIds.length} 台</div>
              </button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => onApply(preset)}
              >
                应用
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                aria-label={`删除方案 ${preset.name}`}
                onClick={() => onDelete(preset.id)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>保存选机方案</DialogTitle>
            <DialogDescription>
              将当前已选的 {selectedCount} 台样机保存为个人方案，便于下次一键应用。
            </DialogDescription>
          </DialogHeader>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：周五回归 · ELA 30 台"
            maxLength={40}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                confirmSave();
              }
            }}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button type="button" disabled={!name.trim()} onClick={confirmSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
