import { useEffect, useMemo, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Smartphone,
  ListTodo,
  Server,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Zap,
  X,
  TestTube2,
  FileBox,
  AlertCircle,
  Rocket,
  Code2,
  CalendarClock,
  HardDrive,
  Wifi,
  FolderKanban,
  Layers,
  Sparkles,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAuthSession } from '@/hooks/useAuthSession';
import {
  BORDER,
  INTERACTIVE,
  SURFACE,
  TEXT,
  navIconClass,
  navLinkClass,
} from '@/design-system/tokens';

interface NavItem {
  path: string;
  label: string;
  icon: React.ElementType;
  /** 与 router AdminRoute 对齐，非 admin 不展示 */
  adminOnly?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

// FRONTEND_NAV_IA_REDESIGN v1.3（2026-08-28 二次反馈：按使用频率分层，非组内重排）：
// 一级=高频常驻（工作区/分析报告）；二级=中频折叠组（资源）；三级=低频收角落
// （「更多功能」折叠组 + admin 管理页移入右上角 UserMenu 下拉）。路由 path 全保持。
const navGroups: NavGroup[] = [
  {
    label: '工作区',
    items: [
      { path: '/', label: '仪表盘', icon: LayoutDashboard },
      { path: '/projects', label: '项目登记簿', icon: FolderKanban },
      { path: '/orchestration/plans', label: 'Plan 管理', icon: FileBox },
      { path: '/execution/plan-execute', label: '执行 Plan', icon: Rocket },
      { path: '/execution/plan-runs', label: '执行记录', icon: ListTodo },
    ],
  },
  {
    label: '分析报告',
    items: [
      { path: '/results', label: '测试结果', icon: TestTube2 },
      { path: '/issue-tracker', label: '问题追踪', icon: AlertCircle },
    ],
  },
  {
    // 中频：排查/维护期使用，默认折叠
    label: '资源',
    items: [
      { path: '/hosts', label: '主机集群', icon: Server },
      { path: '/devices', label: '物理设备', icon: Smartphone },
      { path: '/script-management', label: '脚本库', icon: Code2 },
      { path: '/test-suites', label: '用例套件', icon: Layers },
      { path: '/storage', label: '文件服务器', icon: HardDrive, adminOnly: true },
    ],
  },
  {
    // 低频长尾：有但不必显眼
    label: '更多功能',
    items: [
      { path: '/wifi', label: 'WiFi 资源池', icon: Wifi },
      { path: '/schedules', label: '定时调度', icon: CalendarClock },
    ],
  },
];

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
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const assistantActive = location.pathname.startsWith('/assistant');

  const isItemActive = (path: string) =>
    location.pathname === path || (path !== '/' && location.pathname.startsWith(path));

  // 方案 B：默认折叠非活跃组（首屏一级只有 3 个组名），活跃组自动展开。
  // 仅挂载时初始化一次；此后用户手动开合优先。
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const group of navGroups) {
      map[group.label] = !group.items.some((item) => isItemActive(item.path));
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
    <div className={cn('flex flex-col h-full', SURFACE.elevated)}>
      {/* Logo */}
      <div className={cn('h-20 flex items-center px-5 border-b', BORDER.default)}>
        <div className="flex items-center gap-3 overflow-hidden">
          <div
            className={cn(
              'flex items-center justify-center rounded-lg transition-all duration-300 w-8 h-8',
              SURFACE.subtle,
            )}
          >
            <Zap size={18} className={TEXT.heading} />
          </div>
          <span
            className={cn(
              'font-semibold text-base whitespace-nowrap transition-all duration-300',
              TEXT.heading,
              collapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100',
            )}
          >
            北极星目标
          </span>
        </div>
        {isMobile && onCloseMobile && (
          <button
            onClick={onCloseMobile}
            className={cn('ml-auto lg:hidden p-1', INTERACTIVE.iconButton)}
            aria-label="关闭侧边栏"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* ADR-0031 / FRONTEND_NAV_IA_REDESIGN §5.1：AI 助手 pinned 入口——
          横切能力不占业务组坑位；与业务项的区分 = 描边 + 图标 + 文案（不依赖纯颜色） */}
      <div className="px-3 pt-3">
        <NavLink
          to="/assistant"
          aria-label="AI 助手"
          className={cn(
            'flex items-center gap-3 rounded-lg border px-3 py-2 text-sm font-medium transition-all duration-200',
            assistantActive
              ? 'border-primary/60 bg-primary/10 text-primary'
              : 'border-primary/25 bg-primary/5 text-primary hover:bg-primary/10',
            collapsed && !isMobile && 'justify-center px-2',
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
        </NavLink>
      </div>

      {/* Navigation Groups */}
      <nav className="p-3 overflow-y-auto flex-1 sidebar-scroll">
        {visibleGroups.map((group) => {
          const isGroupCollapsed = !collapsed && !!collapsedGroups[group.label];
          return (
            <div
              key={group.label}
              className={cn('pb-3 mb-3 border-b last:mb-0 last:border-b-0 last:pb-0', BORDER.default)}
            >
              {!collapsed && (
                <button
                  type="button"
                  onClick={() => toggleGroup(group.label)}
                  aria-expanded={!isGroupCollapsed}
                  className={cn(
                    'w-full flex items-center justify-between px-3 mb-2 text-xs font-medium uppercase tracking-wider transition-colors',
                    TEXT.caption,
                    INTERACTIVE.hoverText,
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
                className={cn(
                  'space-y-1 overflow-hidden transition-all duration-200',
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
        <div className={cn('p-3 border-t', BORDER.default)}>
          <button
            onClick={onToggleCollapse}
            className={navLinkClass(false, collapsed)}
            aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
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
