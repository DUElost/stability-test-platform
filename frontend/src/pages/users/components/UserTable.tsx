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
import { INTERACTIVE, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface UserTableProps {
  users: User[];
  currentUserId: number;
  onEdit: (user: User) => void;
  onDelete: (userId: number) => void;
  onToggleActive: (userId: number) => void;
}

export function UserTable({ users, currentUserId, onEdit, onDelete, onToggleActive }: UserTableProps) {
  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="rounded-md border border-border overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted">
            <TableHead className="w-16">ID</TableHead>
            <TableHead>Username</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Last Login</TableHead>
            <TableHead className="text-right w-48">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className={cn('text-center py-8', TEXT.subtitle)}>
                No users found
              </TableCell>
            </TableRow>
          ) : (
            users.map((user) => (
              <TableRow key={user.id}>
                <TableCell className="font-mono text-sm">{user.id}</TableCell>
                <TableCell className="font-medium">{user.username}</TableCell>
                <TableCell>
                  <Badge variant={user.role === 'admin' ? 'default' : 'secondary'}>
                    {user.role === 'admin' ? 'Admin' : 'User'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={user.is_active === 'Y' ? 'success' : 'destructive'}>
                    {user.is_active === 'Y' ? 'Active' : 'Disabled'}
                  </Badge>
                </TableCell>
                <TableCell className={TEXT.subtitle}>{formatDate(user.created_at)}</TableCell>
                <TableCell className={TEXT.subtitle}>{formatDate(user.last_login)}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => onEdit(user)}
                      className={cn('p-2 rounded transition-colors', INTERACTIVE.iconButton, 'hover:text-primary hover:bg-primary/10')}
                      title="Edit user"
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
                          title={user.is_active === 'Y' ? 'Disable user' : 'Enable user'}
                        >
                          {user.is_active === 'Y' ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                        </button>
                        <button
                          onClick={() => onDelete(user.id)}
                          className={cn('p-2 rounded transition-colors', INTERACTIVE.iconButton, 'hover:text-destructive hover:bg-destructive/10')}
                          title="Delete user"
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
