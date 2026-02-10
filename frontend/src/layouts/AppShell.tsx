import { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';
import { X, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

export default function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  // 监听窗口大小变化
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 1024);
      if (window.innerWidth >= 1024) {
        setSidebarOpen(false);
      }
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // ESC 键关闭侧边栏
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setSidebarOpen(false);
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, []);

  const toggleSidebar = () => setSidebarOpen(!sidebarOpen);
  const toggleSidebarCollapse = () => setSidebarCollapsed(!sidebarCollapsed);

  return (
    <TooltipProvider>
      <div className="flex h-screen bg-background overflow-hidden">
        {/* 移动端遮罩层 */}
        {isMobile && sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/50 z-20 transition-opacity duration-300"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Sidebar - 桌面端 */}
        <aside
          className={cn(
            "hidden lg:flex flex-col border-r border-border/50 bg-card",
            "transition-all duration-300 ease-in-out"
          )}
          style={{ width: sidebarCollapsed ? 72 : 256 }}
        >
          <Sidebar
            onNavigate={() => isMobile && setSidebarOpen(false)}
            collapsed={sidebarCollapsed}
            onToggleCollapse={toggleSidebarCollapse}
          />
        </aside>

        {/* Sidebar - 移动端抽屉模式 */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-30 w-64 bg-card border-r border-border/50 transform transition-transform duration-300 ease-in-out lg:hidden",
            sidebarOpen ? 'translate-x-0' : '-translate-x-full'
          )}
        >
          <div className="h-full flex flex-col">
            {/* 移动端关闭按钮 */}
            <div className="absolute top-4 right-4 lg:hidden">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setSidebarOpen(false)}
                className="text-muted-foreground hover:text-foreground"
              >
                <X size={20} />
              </Button>
            </div>
            <Sidebar
              onNavigate={() => setSidebarOpen(false)}
              collapsed={false}
            />
          </div>
        </aside>

        {/* 桌面端悬浮展开按钮 (当侧边栏折叠时) */}
        {!isMobile && sidebarCollapsed && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={toggleSidebarCollapse}
                className="fixed left-[72px] top-6 z-40 h-6 w-6 rounded-full border border-border/50 bg-card shadow-sm hover:bg-accent"
              >
                <ChevronRight size={14} />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Expand sidebar</TooltipContent>
          </Tooltip>
        )}

        {/* 主内容区 */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <Header
            onMenuClick={toggleSidebar}
            showMenuButton={isMobile}
            sidebarCollapsed={sidebarCollapsed}
          />
          <main className="flex-1 overflow-x-hidden overflow-y-auto custom-scrollbar">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 page-enter">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </TooltipProvider>
  );
}
