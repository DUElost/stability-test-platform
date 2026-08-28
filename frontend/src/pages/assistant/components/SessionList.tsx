import { Loader2, Plus, Trash2, MessagesSquare } from 'lucide-react';
import type { AiChatSession } from '@/utils/api/types';
import { BORDER, INTERACTIVE, SURFACE, TEXT } from '@/design-system/tokens';
import { formatDateTimeLocale } from '@/utils/format';
import { cn } from '@/lib/utils';

interface SessionListProps {
  sessions: AiChatSession[];
  activeId: number | null;
  loading: boolean;
  creating: boolean;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onDelete: (session: AiChatSession) => void;
  className?: string;
}

/** 左栏会话列表（新建 / 选择 / 删除；删除确认在父级处理）。 */
export function SessionList({
  sessions,
  activeId,
  loading,
  creating,
  onSelect,
  onCreate,
  onDelete,
  className,
}: SessionListProps) {
  return (
    <aside
      className={cn(
        'flex w-72 shrink-0 flex-col rounded-xl border',
        SURFACE.elevated,
        BORDER.default,
        className,
      )}
      aria-label="会话列表"
    >
      <div className={cn('flex items-center justify-between px-3 py-3', BORDER.subtle, 'border-b')}>
        <h2 className={cn('text-sm font-medium', TEXT.heading)}>会话</h2>
        <button
          type="button"
          onClick={onCreate}
          disabled={creating}
          className={cn(
            'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium',
            'text-primary hover:bg-primary/10',
            INTERACTIVE.hoverText,
          )}
          aria-label="新建会话"
        >
          {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          新建
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : sessions.length === 0 ? (
          <div className={cn('flex flex-col items-center gap-2 py-10 text-center', TEXT.caption)}>
            <MessagesSquare className="h-8 w-8 opacity-40" />
            <p className="text-xs">还没有会话</p>
          </div>
        ) : (
          <ul className="space-y-1">
            {sessions.map((session) => {
              const isActive = session.id === activeId;
              return (
                <li key={session.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelect(session.id)}
                    className={cn(
                      'w-full rounded-lg px-3 py-2 pr-8 text-left transition-colors',
                      isActive
                        ? 'bg-accent'
                        : cn(INTERACTIVE.hover, TEXT.subtitle),
                    )}
                    aria-current={isActive ? 'true' : undefined}
                  >
                    <span
                      className={cn(
                        'block truncate text-sm',
                        isActive ? TEXT.heading : '',
                      )}
                    >
                      {session.title || '未命名会话'}
                    </span>
                    <span className={cn('block truncate text-xs', TEXT.caption)}>
                      {formatDateTimeLocale(session.updated_at)}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(session);
                    }}
                    className={cn(
                      'absolute right-2 top-2 rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100',
                      INTERACTIVE.iconDanger,
                    )}
                    aria-label={`删除会话 ${session.title || session.id}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}

export default SessionList;
