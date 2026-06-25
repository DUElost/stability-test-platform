import { Pencil, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import type { User } from '@/utils/api';
import { INTERACTIVE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';

interface UserTableProps {
  users: User[];
  currentUserId: number;
  onEdit: (user: User) => void;
  onDelete: (userId: number) => void;
  onToggleActive: (userId: number) => void;
}

export function UserTable({ users, currentUserId, onEdit, onDelete, onToggleActive }: UserTableProps) {
  return (
    <div className="rounded-md border border-border overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted">
            <TableHead className="w-16">ID</TableHead>
            <TableHead>用户名</TableHead>
            <TableHead>角色</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>创建时间</TableHead>
            <TableHead>最近登录</TableHead>
            <TableHead className="text-right w-48">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className={cn('text-center py-8', TEXT.subtitle)}>
                暂无用户
              </TableCell>
            </TableRow>
          ) : (
            users.map((user) => (
              <TableRow key={user.id}>
                <TableCell className="font-mono text-sm">{user.id}</TableCell>
                <TableCell className="font-medium">{user.username}</TableCell>
                <TableCell>
                  <Badge variant={user.role === 'admin' ? 'default' : 'secondary'}>
                    {user.role === 'admin' ? '管理员' : '普通用户'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={user.is_active === 'Y' ? 'success' : 'destructive'}>
                    {user.is_active === 'Y' ? '启用' : '禁用'}
                  </Badge>
                </TableCell>
                <TableCell className={TEXT.subtitle}>{formatDateTimeFull(user.created_at)}</TableCell>
                <TableCell className={TEXT.subtitle}>{formatDateTimeFull(user.last_login)}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => onEdit(user)}
                      className={cn('p-2 rounded transition-colors', INTERACTIVE.iconButton, 'hover:text-primary hover:bg-primary/10')}
                      title="编辑用户"
                      aria-label="编辑用户"
                    >
                      <Pencil size={16} />
                    </button>
                    {user.id !== currentUserId && (
                      <>
                        <button
                          onClick={() => onToggleActive(user.id)}
                          className={cn(
                            'p-2 rounded transition-colors',
                            INTERACTIVE.iconButton,
                            user.is_active === 'Y'
                              ? 'hover:text-warning hover:bg-warning/10'
                              : 'hover:text-success hover:bg-success/10',
                          )}
                          title={user.is_active === 'Y' ? '禁用用户' : '启用用户'}
                          aria-label={user.is_active === 'Y' ? '禁用用户' : '启用用户'}
                        >
                          {user.is_active === 'Y' ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                        </button>
                        <button
                          onClick={() => onDelete(user.id)}
                          className={cn('p-2 rounded transition-colors', INTERACTIVE.iconButton, 'hover:text-destructive hover:bg-destructive/10')}
                          title="删除用户"
                          aria-label="删除用户"
                        >
                          <Trash2 size={16} />
                        </button>
                      </>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
