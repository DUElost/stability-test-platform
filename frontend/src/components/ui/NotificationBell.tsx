import { useState, useRef, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCheck, AlertTriangle, Info, AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { api } from '@/utils/api';
import type { NotificationLog } from '@/utils/api/types';
import { formatDateTimeLocale } from '@/utils/format';
import { useSocketIO } from '@/hooks/useSocketIO';
import { DASHBOARD_SUBSCRIPTION } from '@/config';
import { BORDER, ELEVATION, INTERACTIVE, SURFACE, TEXT } from '@/design-system/tokens';
import { notificationTarget } from '@/pages/notifications/notificationTarget';

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
  const navigate = useNavigate();

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

  useSocketIO(DASHBOARD_SUBSCRIPTION, {
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

  /** 点击条目：未读则标记已读；有可解析上下文（run_id 等）则跳转详情 */
  const handleItemClick = async (log: NotificationLog) => {
    if (!log.read) {
      try {
        await api.notifications.markRead(log.id);
        qc.invalidateQueries({ queryKey: ['notification-unread-count'] });
        qc.invalidateQueries({ queryKey: ['notification-logs-recent'] });
      } catch {
        // 已读标记失败不阻塞跳转
      }
    }
    const target = notificationTarget(log);
    if (target) {
      setOpen(false);
      navigate(target.to);
    }
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
          'absolute right-0 top-full mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border overflow-hidden',
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
                const target = notificationTarget(log);
                return (
                  <button
                    key={log.id}
                    type="button"
                    onClick={() => void handleItemClick(log)}
                    className={cn(
                      'block w-full text-left px-4 py-3 border-b last:border-b-0 transition-colors',
                      BORDER.default,
                      !log.read && 'bg-primary/5',
                      INTERACTIVE.hover,
                    )}
                    title={target ? `${target.label}：` + log.title : '点击标记已读'}
                  >
                    <div className="flex gap-3">
                      <Icon className={cn('w-4 h-4 mt-0.5 shrink-0', SEVERITY_COLOR[log.severity])} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={cn('text-xs font-medium truncate', TEXT.heading)}>{log.title}</span>
                          <span className={cn('text-[11px] px-1.5 py-0.5 rounded shrink-0', SURFACE.subtle, TEXT.caption)}>
                            {SOURCE_LABEL[log.source as keyof typeof SOURCE_LABEL] ?? log.source}
                          </span>
                        </div>
                        {log.message && (
                          <p className={cn('text-xs mt-1 line-clamp-2', TEXT.caption)}>{log.message}</p>
                        )}
                        <span className={cn('text-[11px] mt-1', TEXT.caption)}>
                          {formatDateTimeLocale(log.created_at, '')}
                        </span>
                      </div>
                      {!log.read && <div className="w-2 h-2 rounded-full bg-primary shrink-0 mt-1.5" />}
                    </div>
                  </button>
                );
              })
            )}
          </div>

          <Link
            to="/notifications?tab=logs"
            onClick={() => setOpen(false)}
            className={cn(
              'block text-center py-2.5 text-xs border-t transition-colors',
              BORDER.default,
              INTERACTIVE.hover,
              TEXT.caption,
            )}
          >
            查看全部通知
          </Link>
        </div>
      )}
    </div>
  );
}
