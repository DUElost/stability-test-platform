import { useState } from 'react';
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
  Folder,
  AlertCircle,
  Rocket,
  Code2,
  CalendarClock,
  BellRing,
} from 'lucide-react';
import { cn } from '@/lib/utils';

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
    label: '概览',
    items: [
      { path: '/', label: '仪表盘', icon: LayoutDashboard },
    ],
  },
  {
    label: '测试编排',
    items: [
      { path: '/orchestration/plans', label: 'Plan 管理', icon: FileBox },
      { path: '/execution/plan-execute', label: '触发执行', icon: Rocket },
      { path: '/execution/plan-runs', label: '执行记录', icon: ListTodo },
    ],
  },
  {
    label: '测试资产',
    items: [
      { path: '/script-management', label: '脚本库', icon: Code2 },
      { path: '/resources', label: '环境资源', icon: Folder },
    ],
  },
  {
    label: '主机与设备',
    items: [
      { path: '/hosts', label: '主机集群', icon: Server },
      { path: '/devices', label: '物理设备', icon: Smartphone },
    ],
  },
  {
    label: '分析报告',
    items: [
      { path: '/results', label: '测试结果', icon: TestTube2 },
      { path: '/issue-tracker', label: '问题追踪', icon: AlertCircle },
      { path: '/schedules', label: '定时调度', icon: CalendarClock },
      { path: '/notifications', label: '通知管理', icon: BellRing },
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
 * 侧边栏 - 源自 web 样板设计风格
 */
export default function Sidebar({
  onNavigate,
  collapsed = false,
  onToggleCollapse,
  isMobile = false,
  onCloseMobile
}: SidebarProps) {
  const location = useLocation();
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  const toggleGroup = (label: string) =>
    setCollapsedGroups((prev) => ({ ...prev, [label]: !prev[label] }));

  const NavItemContent = ({ item, isActive }: { item: NavItem; isActive: boolean }) => (
    <>
      <item.icon className={cn(
        "w-4 h-4 flex-shrink-0 transition-colors",
        isActive ? "text-gray-900" : "text-gray-500 group-hover:text-gray-900"
      )} />
      <span className={cn(
        "font-medium transition-all duration-200 truncate",
        collapsed ? "opacity-0 w-0 overflow-hidden" : "opacity-100"
      )}>
        {item.label}
      </span>
    </>
  );

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Logo */}
      <div className="h-20 flex items-center px-5 border-b border-gray-100">
        <div className="flex items-center gap-3 overflow-hidden">
          <div className={cn(
            "flex items-center justify-center rounded-lg bg-gray-50 transition-all duration-300",
            collapsed ? "w-8 h-8" : "w-8 h-8"
          )}>
            <Zap size={18} className="text-gray-700" />
          </div>
          <span className={cn(
            "font-semibold text-base text-gray-900 whitespace-nowrap transition-all duration-300",
            collapsed ? "opacity-0 w-0 overflow-hidden" : "opacity-100"
          )}>
            北极星目标
          </span>
        </div>
        {isMobile && onCloseMobile && (
          <button
            onClick={onCloseMobile}
            className="ml-auto lg:hidden p-1 text-gray-400 hover:text-gray-600"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Navigation Groups */}
      <nav className="p-3 overflow-y-auto flex-1 sidebar-scroll">
        {navGroups.map((group) => {
          const isGroupCollapsed = !collapsed && !!collapsedGroups[group.label];
          return (
          <div key={group.label} className="pb-3 mb-3 border-b border-gray-100 last:mb-0 last:border-b-0 last:pb-0">
            {!collapsed && (
              <button
                type="button"
                onClick={() => toggleGroup(group.label)}
                className="w-full flex items-center justify-between px-3 mb-2 text-xs font-medium text-gray-400 uppercase tracking-wider hover:text-gray-600 transition-colors"
              >
                <span>{group.label}</span>
                <ChevronDown
                  className={cn(
                    "w-3.5 h-3.5 transition-transform duration-200",
                    isGroupCollapsed ? "-rotate-90" : ""
                  )}
                />
              </button>
            )}
            <div className={cn(
              "space-y-1 overflow-hidden transition-all duration-200",
              isGroupCollapsed ? "max-h-0 opacity-0" : "max-h-96 opacity-100"
            )}>
              {group.items.map((item) => {
                const isActive = location.pathname === item.path ||
                  (item.path !== '/' && location.pathname.startsWith(item.path));

                const linkContent = (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={onNavigate}
                    aria-label={collapsed ? item.label : undefined}
                    className={cn(
                      "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 group",
                      isActive
                        ? "bg-gray-50 text-gray-900 font-medium"
                        : "text-gray-500 hover:bg-gray-50 hover:text-gray-900"
                    )}
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
        <div className="p-3 border-t border-gray-100">
          <button
            onClick={onToggleCollapse}
            className={cn(
              "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-gray-500 hover:bg-gray-50 hover:text-gray-900 transition-all duration-200",
              collapsed && "justify-center px-2"
            )}
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
