import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Smartphone,
  ListTodo,
  Server,
  ChevronLeft,
  ChevronRight,
  Zap,
  X,
  TestTube2,
  Network,
  Wifi,
  FileSearch,
  Users,
  Workflow
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

// 样板风格的导航配置
const navGroups: NavGroup[] = [
  {
    label: '概览',
    items: [
      { path: '/', label: '仪表盘', icon: LayoutDashboard },
    ],
  },
  {
    label: '基础设施',
    items: [
      { path: '/hosts', label: '主机管理', icon: Server },
      { path: '/devices', label: '设备管理', icon: Smartphone },
      { path: '/wifi', label: 'WiFi管理', icon: Wifi },
    ],
  },
  {
    label: '运营',
    items: [
      { path: '/tasks', label: '任务管理', icon: ListTodo },
      { path: '/workflows', label: '工作流管理', icon: Workflow },
      { path: '/results', label: '测试结果', icon: TestTube2 },
      { path: '/logs', label: '日志监控', icon: FileSearch },
    ],
  },
  {
    label: '系统',
    items: [
      { path: '/mapreduce', label: 'Map-Reduce', icon: Network },
      { path: '/users', label: '用户管理', icon: Users },
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
      <div className="h-16 flex items-center px-5 border-b border-gray-100">
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
      <nav className="p-3 space-y-0.5 overflow-y-auto flex-1">
        {navGroups.map((group) => (
          <div key={group.label}>
            <div className={cn(
              "px-3 mb-2 text-xs font-medium text-gray-400 uppercase tracking-wider transition-all duration-200",
              collapsed ? "opacity-0 h-0 overflow-hidden" : "opacity-100"
            )}>
              {group.label}
            </div>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = location.pathname === item.path ||
                  (item.path !== '/' && location.pathname.startsWith(item.path));

                const linkContent = (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={onNavigate}
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
        ))}
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
