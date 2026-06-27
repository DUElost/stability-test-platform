import React from 'react';
import { User } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SURFACE, TEXT, INTERACTIVE, ELEVATION } from '@/design-system/tokens';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';

interface UserMenuItem {
  label: string;
  href?: string;
  onClick?: () => void;
  icon?: React.ReactNode;
  destructive?: boolean;
}

interface UserMenuProps {
  username?: string;
  role?: string;
  items: UserMenuItem[];
}

export const UserMenu: React.FC<UserMenuProps> = ({ username, role, items }) => {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className={cn('flex items-center gap-2 p-1.5 h-auto rounded-lg', INTERACTIVE.hover)}
          aria-label="用户菜单"
        >
          <div className={cn('w-8 h-8 rounded-full flex items-center justify-center', SURFACE.subtle)}>
            <User className={cn('w-4 h-4', TEXT.subtitle)} />
          </div>
          <div className="hidden sm:flex flex-col items-start leading-tight">
            <span className={cn('text-sm font-medium', TEXT.heading)}>{username ?? '...'}</span>
            {role && <span className={cn('text-xs', TEXT.caption)}>{role}</span>}
          </div>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={cn('w-48', SURFACE.elevated, ELEVATION.dropdown)}>
        {items.map((item, idx) => {
          const className = item.destructive
            ? cn('text-destructive focus:text-destructive focus:bg-destructive/10', INTERACTIVE.destructiveMenu)
            : INTERACTIVE.menuItem;

          return (
            <React.Fragment key={idx}>
              {item.label === '__SEPARATOR__' ? (
                <DropdownMenuSeparator />
              ) : item.href ? (
                <DropdownMenuItem asChild>
                  <a href={item.href} className={className}>
                    {item.icon}
                    <span className="ml-2">{item.label}</span>
                  </a>
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={item.onClick} className={className}>
                  {item.icon}
                  <span className="ml-2">{item.label}</span>
                </DropdownMenuItem>
              )}
            </React.Fragment>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default UserMenu;
