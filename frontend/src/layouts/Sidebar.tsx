import { useContext, useEffect, useMemo, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { QueryClientContext, useQuery } from '@tanstack/react-query';
import {
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  X,
  Sparkles,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { useAuthSession } from '@/hooks/useAuthSession';
import { api } from '@/utils/api';
import { aiAssistantKeys } from '@/utils/api/queryKeys';
import { BrandLogo } from '@/components/brand/BrandLogo';
import {
  SIDEBAR,
  navIconClass,
  navLinkClass,
} from '@/design-system/tokens';

import { navGroups, type NavItem } from './navItems';

function PendingApprovalsBadge({ collapsed }: { collapsed: boolean }) {
  const pendingQ = useQuery({
    queryKey: aiAssistantKeys.pendingActions(),
    queryFn: api.aiAssistant.listPendingActions,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
  const count = pendingQ.data?.length ?? 0;
  if (count <= 0) return null;
  return (
    <span
      data-testid="sidebar-assistant-pending-badge"
      aria-label={`${count} 条待审批`}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full bg-warning text-[10px] font-semibold leading-none text-warning-foreground tabular-nums',
        collapsed ? 'absolute right-1 top-1 h-4 min-w-4 px-1' : 'ml-auto h-5 min-w-5 px-1.5',
      )}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

// FRONTEND_NAV_IA_REDESIGN v1.3（2026-08-28 二次反馈：按使用频率分层，非组内重排）：
// 一级=高频常驻（工作区/分析报告）；二级=中频折叠组（资源）；三级=低频收角落
// （「更多功能」折叠组 + admin 管理页移入右上角 UserMenu 下拉）。路由 path 全保持。

interface SidebarProps {
  onNavigate?: () => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  isMobile?: boolean;
  onCloseMobile?: () => void;
}

/**
 * 侧边栏导航
 */
export default function Sidebar({
  onNavigate,
  collapsed = false,
  onToggleCollapse,
  isMobile = false,
  onCloseMobile,
}: SidebarProps) {
  const location = useLocation();
  const queryClient = useContext(QueryClientContext);
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const assistantActive = location.pathname.startsWith('/assistant');

  const isItemActive = (path: string) =>
    location.pathname === path || (path !== '/' && location.pathname.startsWith(path));

  // 默认展开高频核心组（工作区 / 分析报告 / 资源），仅默认折叠标记 defaultCollapsed 的低频组；
  // 若低频组含当前活跃路由则自动展开。此后用户手动开合优先。
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const group of navGroups) {
      const hasActive = group.items.some((item) => isItemActive(item.path));
      map[group.label] = Boolean(group.defaultCollapsed) && !hasActive;
    }
    return map;
  });

  // 路由切换后自动展开新活跃组（不收起其他组）。条件守卫的一次性 setState，
  // 不会循环（对齐 NotificationsPage 先例）。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const activeGroup = navGroups.find((group) =>
      group.items.some((item) => isItemActive(item.path)),
    );
    if (activeGroup && collapsedGroups[activeGroup.label]) {
      setCollapsedGroups((prev) => ({ ...prev, [activeGroup.label]: false }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  const visibleGroups = useMemo(
    () =>
      navGroups
        .map((group) => ({
          ...group,
          items: group.items.filter((item) => !item.adminOnly || isAdmin),
        }))
        .filter((group) => group.items.length > 0),
    [isAdmin],
  );

  const toggleGroup = (label: string) =>
    setCollapsedGroups((prev) => ({ ...prev, [label]: !prev[label] }));

  const NavItemContent = ({ item, isActive }: { item: NavItem; isActive: boolean }) => (
    <>
      <item.icon className={navIconClass(isActive)} />
      <span
        className={cn(
          'font-medium transition-all duration-200 truncate',
          collapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100',
        )}
      >
        {item.label}
      </span>
    </>
  );

  return (
    <div className={cn('flex flex-col h-full', SIDEBAR.root)}>
      {/* Logo（与 AppShell h-12 顶栏严格等高） */}
      <div
        className={cn(
          'h-12 shrink-0 flex items-center border-b',
          collapsed && !isMobile ? 'justify-center px-2' : 'px-4',
          SIDEBAR.border,
        )}
      >
        <div className="flex items-center gap-2.5 overflow-hidden">
          <BrandLogo size={32} className="h-6 w-6 shrink-0 rounded-md" />
          <span
            className={cn(
              'font-semibold text-[15px] whitespace-nowrap transition-all duration-300 tracking-[-0.02em]',
              'text-sidebar-foreground',
              collapsed && !isMobile ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100',
            )}
          >
            稳定性测试平台
          </span>
        </div>
        {isMobile && onCloseMobile && (
          <button
            onClick={onCloseMobile}
            className={cn(
              'ml-auto lg:hidden p-1 text-sidebar-foreground/60 hover:text-sidebar-foreground',
            )}
            aria-label="关闭侧边栏"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* ADR-0031 / FRONTEND_NAV_IA_REDESIGN §5.1：AI 助手 pinned 入口——
          横切能力不占业务组坑位；与业务项的区分 = 描边 + 图标 + 文案（不依赖纯颜色） */}
      <div className={cn('pt-2.5 pb-1', collapsed && !isMobile ? 'px-2' : 'px-2.5')}>
        <NavLink
          to="/assistant"
          onClick={onNavigate}
          aria-label="AI 助手"
          title={collapsed && !isMobile ? 'AI 助手' : undefined}
          className={cn(
            'relative flex items-center gap-2.5 rounded-lg border px-2.5 py-1.5 text-[13px] font-medium transition-all duration-200',
            assistantActive
              ? 'border-primary/60 bg-primary/10 text-primary'
              : 'border-primary/25 bg-primary/5 text-primary hover:bg-primary/10',
            collapsed && !isMobile && 'justify-center px-0',
          )}
        >
          <Sparkles className="h-4 w-4 shrink-0" />
          <span
            className={cn(
              'truncate transition-all duration-200',
              collapsed && !isMobile && 'w-0 overflow-hidden opacity-0',
            )}
          >
            AI 助手
          </span>
          {isAdmin && queryClient && (
            <PendingApprovalsBadge collapsed={collapsed && !isMobile} />
          )}
        </NavLink>
      </div>

      {/* Navigation Groups */}
      <nav className={cn('py-2 overflow-y-auto flex-1 sidebar-scroll', collapsed && !isMobile ? 'px-2' : 'px-2.5')}>
        {visibleGroups.map((group) => {
          const isGroupCollapsed = !collapsed && !!collapsedGroups[group.label];
          return (
            <div
              key={group.label}
              className={cn('pb-2.5 mb-2.5 border-b last:mb-0 last:border-b-0 last:pb-0', SIDEBAR.border)}
            >
              {!collapsed && (
                <button
                  type="button"
                  onClick={() => toggleGroup(group.label)}
                  aria-expanded={!isGroupCollapsed}
                  className={cn(
                    'w-full flex items-center justify-between px-2.5 mb-1 text-[11px] font-medium uppercase tracking-wider transition-colors',
                    'text-sidebar-foreground/45 hover:text-sidebar-foreground/80',
                  )}
                >
                  <span>{group.label}</span>
                  <ChevronDown
                    className={cn(
                      'w-3.5 h-3.5 transition-transform duration-200',
                      isGroupCollapsed ? '-rotate-90' : '',
                    )}
                  />
                </button>
              )}
              <div
                // #1197：折叠组内链接退出键盘焦点顺序（inert 同时移出焦点与辅助技术树）
                inert={isGroupCollapsed}
                className={cn(
                  'space-y-0.5 overflow-hidden transition-all duration-200',
                  isGroupCollapsed ? 'max-h-0 opacity-0' : 'max-h-96 opacity-100',
                )}
              >
                {group.items.map((item) => {
                  const isActive =
                    location.pathname === item.path ||
                    (item.path !== '/' && location.pathname.startsWith(item.path));

                  const linkContent = (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      onClick={onNavigate}
                      aria-label={collapsed ? item.label : undefined}
                      className={navLinkClass(isActive)}
                    >
                      <NavItemContent item={item} isActive={isActive} />
                    </NavLink>
                  );

                  if (collapsed && !isMobile) {
                    return (
                      <div key={item.path} title={item.label}>
                        {linkContent}
                      </div>
                    );
                  }

                  return linkContent;
                })}
              </div>
            </div>
          );
        })}
      </nav>

      {/* Collapse Toggle Button - Desktop only */}
      {!isMobile && onToggleCollapse && (
        <div className={cn('py-2 border-t', collapsed ? 'px-2' : 'px-2.5', SIDEBAR.border)}>
          <button
            onClick={onToggleCollapse}
            className={navLinkClass(false, collapsed)}
            aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
            title={collapsed ? '展开侧边栏' : '收起侧边栏'}
          >
            {collapsed ? (
              <ChevronRight className="w-4 h-4" />
            ) : (
              <>
                <ChevronLeft className="w-4 h-4" />
                <span className="font-medium">收起</span>
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
