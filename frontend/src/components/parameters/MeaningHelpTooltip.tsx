import { CircleHelp } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

export interface MeaningHelpTooltipProps {
  /** 术语/字段标签；用于 aria-label，不单独展示。 */
  label: string;
  /** 悬停/聚焦时展示的完整释义。空串不渲染图标。 */
  meaning: string;
  className?: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
}

/**
 * 标签旁「?」帮助图标：长释义进 Tooltip，避免常显灰字挤占布局。
 * 触发器可键盘聚焦；Radix Tooltip 在 focus 时打开，便于读屏与键盘到达。
 */
export function MeaningHelpTooltip({
  label,
  meaning,
  className,
  side = 'top',
}: MeaningHelpTooltipProps) {
  const text = meaning.trim();
  if (!text) return null;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm',
              'text-muted-foreground hover:text-foreground',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              className,
            )}
            aria-label={`${label}的说明`}
            data-testid="meaning-help"
          >
            <CircleHelp className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
          </button>
        </TooltipTrigger>
        <TooltipContent
          side={side}
          className="max-w-xs whitespace-pre-wrap break-words bg-popover text-popover-foreground"
        >
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
