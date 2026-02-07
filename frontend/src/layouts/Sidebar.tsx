import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Smartphone,
  ListTodo,
  Server,
  Settings,
  FileText,
  ChevronLeft,
  ChevronRight,
  Zap
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useState, useEffect } from 'react';

interface NavItem {
  path: string;
  label: string;
  icon: React.ElementType;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const navGroups: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      { path: '/', label: 'Dashboard', icon: LayoutDashboard },
    ],
  },
  {
    label: 'Infrastructure',
    items: [
      { path: '/hosts', label: 'Hosts', icon: Server },
      { path: '/devices', label: 'Devices', icon: Smartphone },
    ],
  },
  {
    label: 'Operations',
    items: [
      { path: '/tasks', label: 'Tasks', icon: ListTodo },
    ],
  },
];

const bottomNavItems: NavItem[] = [
  { path: '/docs', label: 'Documentation', icon: FileText },
  { path: '/settings', label: 'Settings', icon: Settings },
];

interface SidebarProps {
  onNavigate?: () => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export default function Sidebar({ onNavigate, collapsed = false, onToggleCollapse }: SidebarProps) {
  const location = useLocation();
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 1024);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const NavItemContent = ({ item, isActive }: { item: NavItem; isActive: boolean }) => (
    <>
      <item.icon size={18} className={cn(
        "flex-shrink-0 transition-colors",
        isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground"
      )} />
      <span className={cn(
        "font-medium transition-all duration-200",
        collapsed ? "opacity-0 w-0 overflow-hidden" : "opacity-100"
      )}>
        {item.label}
      </span>
    </>
  );

  return (
    <TooltipProvider delayDuration={0}>
      <div className="flex flex-col h-full">
        {/* Logo */}
        <div className="h-16 flex items-center px-4 border-b border-border/50">
          <div className="flex items-center gap-3 overflow-hidden">
            <div className={cn(
              "flex items-center justify-center rounded-lg bg-primary/10 transition-all duration-300",
              collapsed ? "w-8 h-8" : "w-8 h-8"
            )}>
              <Zap size={18} className="text-primary" />
            </div>
            <span className={cn(
              "font-bold text-lg text-foreground whitespace-nowrap transition-all duration-300",
              collapsed ? "opacity-0 w-0 overflow-hidden" : "opacity-100"
            )}>
              StabilityPro
            </span>
          </div>
        </div>

        {/* Navigation Groups */}
        <nav className="flex-1 py-4 px-3 space-y-6 overflow-y-auto custom-scrollbar">
          {navGroups.map((group) => (
            <div key={group.label}>
              <div className={cn(
                "px-3 mb-2 text-xs font-medium text-muted-foreground/70 uppercase tracking-wider transition-all duration-200",
                collapsed ? "opacity-0 h-0 overflow-hidden" : "opacity-100"
              )}>
                {group.label}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const isActive = location.pathname === item.path ||
                    (item.path !== '/' && location.pathname.startsWith(item.path));

                  const linkContent = (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      onClick={onNavigate}
                      className={cn(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group",
                        isActive
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground hover:bg-accent hover:text-foreground"
                      )}
                    >
                      <NavItemContent item={item} isActive={isActive} />
                    </NavLink>
                  );

                  if (collapsed) {
                    return (
                      <Tooltip key={item.path}>
                        <TooltipTrigger asChild>
                          {linkContent}
                        </TooltipTrigger>
                        <TooltipContent side="right">
                          {item.label}
                        </TooltipContent>
                      </Tooltip>
                    );
                  }

                  return linkContent;
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Bottom Navigation */}
        <div className="p-3 border-t border-border/50 space-y-1">
          {bottomNavItems.map((item) => {
            const isActive = location.pathname === item.path;

            const linkContent = (
              <NavLink
                key={item.path}
                to={item.path}
                onClick={onNavigate}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground"
                )}
              >
                <item.icon size={18} className={cn(
                  "flex-shrink-0 transition-colors",
                  isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground"
                )} />
                <span className={cn(
                  "font-medium transition-all duration-200",
                  collapsed ? "opacity-0 w-0 overflow-hidden" : "opacity-100"
                )}>
                  {item.label}
                </span>
              </NavLink>
            );

            if (collapsed) {
              return (
                <Tooltip key={item.path}>
                  <TooltipTrigger asChild>
                    {linkContent}
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    {item.label}
                  </TooltipContent>
                </Tooltip>
              );
            }

            return linkContent;
          })}

          {/* Collapse Toggle Button (Desktop only) */}
          {!isMobile && onToggleCollapse && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onToggleCollapse}
              className={cn(
                "w-full mt-2 flex items-center gap-2 text-muted-foreground hover:text-foreground",
                collapsed && "justify-center px-2"
              )}
            >
              {collapsed ? (
                <ChevronRight size={16} />
              ) : (
                <>
                  <ChevronLeft size={16} />
                  <span className="text-xs">Collapse</span>
                </>
              )}
            </Button>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}
