import React, { ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

export interface MoreAction {
  label: string;
  onClick: () => void;
  destructive?: boolean;
}

interface DataListItemProps {
  children: ReactNode;
  actions?: ReactNode;
  moreActions?: MoreAction[];
  onNavigate?: () => void;
  selected?: boolean;
  onSelect?: () => void;
  className?: string;
}

export const DataListItem: React.FC<DataListItemProps> = ({
  children,
  actions,
  moreActions,
  onNavigate,
  selected,
  onSelect,
  className,
}) => {
  const Main = onNavigate ? 'button' : 'div';

  return (
    <div
      className={cn(
        'group flex items-start gap-3 rounded-lg border bg-card p-3 transition-colors hover:border-border/80',
        selected && 'ring-2 ring-primary/20 border-primary/40',
        className,
      )}
    >
      {onSelect && (
        <div className="pt-1">
          <Checkbox checked={selected} onCheckedChange={onSelect} aria-label="选择" />
        </div>
      )}
      <Main
        className={cn(
          'min-w-0 flex-1 text-left',
          onNavigate && 'cursor-pointer',
        )}
        onClick={onNavigate}
        type={onNavigate ? 'button' : undefined}
      >
        {children}
      </Main>
      {(actions || (moreActions && moreActions.length > 0)) && (
        <div className="flex shrink-0 items-center gap-1 pt-0.5">
          {actions}
          {moreActions && moreActions.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="更多操作" className="h-8 w-8">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {moreActions.map((action, idx) => (
                  <DropdownMenuItem
                    key={idx}
                    onClick={action.onClick}
                    className={cn(action.destructive && 'text-destructive focus:text-destructive')}
                  >
                    {action.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}
    </div>
  );
};

export default DataListItem;
