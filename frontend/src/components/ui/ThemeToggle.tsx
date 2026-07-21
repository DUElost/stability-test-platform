import { Monitor, Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTheme, type ThemePreference } from '@/contexts/ThemeContext';
import { cn } from '@/lib/utils';
import { INTERACTIVE } from '@/design-system/tokens';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

const LABEL: Record<ThemePreference, string> = {
  light: '浅色',
  dark: '深色',
  system: '跟随系统',
};

const NEXT: Record<ThemePreference, string> = {
  light: '切换到深色',
  dark: '切换到跟随系统',
  system: '切换到浅色',
};

interface ThemeToggleProps {
  className?: string;
  /** 紧凑：仅图标（顶栏）；宽松：图标+文案（登录页） */
  showLabel?: boolean;
}

export function ThemeToggle({ className, showLabel = false }: ThemeToggleProps) {
  const { theme, cycleTheme } = useTheme();
  const Icon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor;
  const aria = `${LABEL[theme]}（${NEXT[theme]}）`;

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size={showLabel ? 'sm' : 'icon'}
            onClick={cycleTheme}
            aria-label={aria}
            className={cn(INTERACTIVE.iconButton, showLabel && 'gap-1.5 px-2', className)}
          >
            <Icon className="h-4 w-4" />
            {showLabel ? <span className="text-xs">{LABEL[theme]}</span> : null}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{NEXT[theme]}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export default ThemeToggle;
