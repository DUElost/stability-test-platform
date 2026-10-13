import { useRef, useState } from 'react';
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
  /** 悬停/聚焦/触屏点按时展示的完整释义。空串不渲染图标。 */
  meaning: string;
  className?: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
}

/**
 * 标签旁「?」帮助图标：长释义进 Tooltip，避免常显灰字挤占布局。
 * 桌面：悬停 / 键盘聚焦打开；触屏：点按切换打开（Radix 默认忽略 touch pointermove，
 * 且 pointerdown 会抑制 focus-open、click 只关不开）。
 */
export function MeaningHelpTooltip({
  label,
  meaning,
  className,
  side = 'top',
}: MeaningHelpTooltipProps) {
  const text = meaning.trim();
  const [open, setOpen] = useState(false);
  /** 触屏 toggle 后若仍合成 click，阻止 Radix Trigger 的 onClick→onClose。 */
  const suppressClickCloseRef = useRef(false);

  if (!text) return null;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip open={open} onOpenChange={setOpen}>
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
            onPointerDown={(event) => {
              if (event.pointerType !== 'touch') {
                suppressClickCloseRef.current = false;
                return;
              }
              // 跳过 Radix 默认触屏路径，由本组件切换受控 open。
              event.preventDefault();
              suppressClickCloseRef.current = true;
              setOpen((current) => !current);
            }}
            onClick={(event) => {
              if (!suppressClickCloseRef.current) return;
              event.preventDefault();
              suppressClickCloseRef.current = false;
            }}
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
