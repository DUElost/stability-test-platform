import { useState, useRef, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCheck, AlertTriangle, Info, AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { api } from '@/utils/api';
import { useSocketIO } from '@/hooks/useSocketIO';
import { WS_DASHBOARD_ENDPOINT } from '@/config';
import { BORDER, ELEVATION, INTERACTIVE, SURFACE, TEXT } from '@/design-system/tokens';

const SEVERITY_ICON = {
  critical: AlertCircle,
  warning: AlertTriangle,
  info: Info,
} as const;

const SEVERITY_COLOR = {
  critical: 'text-destructive',
  warning: 'text-warning',
  info: 'text-info',
} as const;

const SOURCE_LABEL = {
  PLATFORM: '平台',
  ALERTMANAGER: '监控',
} as const;

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  const unreadQ = useQuery({
    queryKey: ['notification-unread-count'],
    queryFn: () => api.notifications.unreadCount(),
    refetchInterval: 30000,
  });

  const logsQ = useQuery({
    queryKey: ['notification-logs-recent'],
    queryFn: () => api.notifications.listLogs(0, 8),
    enabled: open,
  });

  useSocketIO(WS_DASHBOARD_ENDPOINT, {
    onMessage: (msg) => {
      if (msg.type === 'notification:new') {
        qc.invalidateQueries({ queryKey: ['notification-unread-count'] });
        if (open) {
          qc.invalidateQueries({ queryKey: ['notification-logs-recent'] });
        }
      }
    },
  });

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const unread = unreadQ.data?.unread ?? 0;
  const logs = logsQ.data?.items ?? [];

  const handleMarkAllRead = async () => {
    await api.notifications.markAllRead();
    qc.invalidateQueries({ queryKey: ['notification-unread-count'] });
    qc.invalidateQueries({ queryKey: ['notification-logs-recent'] });
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={cn('relative p-2 rounded-lg transition-colors', INTERACTIVE.iconButton)}
        aria-label="通知"
      >
        <Bell className="w-5 h-5" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-bold text-destructive-foreground bg-destructive rounded-full">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className={cn(
          'absolute right-0 top-full mt-2 w-96 rounded-xl border overflow-hidden',
          SURFACE.elevated,
          BORDER.default,
          ELEVATION.lg,
        )}>
          <div className={cn('flex items-center justify-between px-4 py-3 border-b', BORDER.default)}>
            <span className={cn('text-sm font-semibold', TEXT.heading)}>通知</span>
            {unread > 0 && (
              <button
                onClick={handleMarkAllRead}
                className={cn('flex items-center gap-1 text-xs', INTERACTIVE.hoverText, TEXT.caption)}
              >
                <CheckCheck className="w-3.5 h-3.5" />
                全部已读
              </button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto">
            {logs.length === 0 ? (
              <div className={cn('flex items-center justify-center py-12 text-sm', TEXT.caption)}>
                暂无通知
              </div>
            ) : (
              logs.map((log) => {
                const Icon = SEVERITY_ICON[log.severity] ?? Info;
                return (
                  <div
                    key={log.id}
                    className={cn(
                      'flex gap-3 px-4 py-3 border-b last:border-b-0 transition-colors',
                      BORDER.default,
                      !log.read && 'bg-primary/5',
                      INTERACTIVE.hover,
                    )}
                  >
                    <Icon className={cn('w-4 h-4 mt-0.5 shrink-0', SEVERITY_COLOR[log.severity])} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={cn('text-xs font-medium truncate', TEXT.heading)}>{log.title}</span>
                        <span className={cn('text-[10px] px-1.5 py-0.5 rounded shrink-0', SURFACE.subtle, TEXT.caption)}>
                          {SOURCE_LABEL[log.source as keyof typeof SOURCE_LABEL] ?? log.source}
                        </span>
                      </div>
                      {log.message && (
                        <p className={cn('text-xs mt-1 line-clamp-2', TEXT.caption)}>{log.message}</p>
                      )}
                      <span className={cn('text-[10px] mt-1', TEXT.caption)}>
                        {log.created_at ? new Date(log.created_at).toLocaleString('zh-CN') : ''}
                      </span>
                    </div>
                    {!log.read && <div className="w-2 h-2 rounded-full bg-primary shrink-0 mt-1.5" />}
                  </div>
                );
              })
            )}
          </div>

          <a
            href="/notifications?tab=logs"
            onClick={() => setOpen(false)}
            className={cn(
              'block text-center py-2.5 text-xs border-t transition-colors',
              BORDER.default,
              INTERACTIVE.hover,
              TEXT.caption,
            )}
          >
            查看全部通知
          </a>
        </div>
      )}
    </div>
  );
}
