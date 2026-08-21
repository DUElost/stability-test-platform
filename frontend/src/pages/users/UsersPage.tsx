import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2, Users as UsersIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { useAuthSession } from '@/hooks/useAuthSession';
import { UserTable } from './components/UserTable';
import { UserModal } from './components/UserModal';
import { api, toApiError, type User } from '@/utils/api';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState } from '@/components/ui/empty-state';
import { InlineError } from '@/components/ui/error-state';
import { TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

export default function UsersPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editUser, setEditUser] = useState<User | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const { data: currentUser } = useAuthSession();

  // Fetch users list
  const { data: users, isLoading, error, refetch } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.users.list(0, 200).then(res => res.items),
  });

  // Create user mutation
  const createMutation = useMutation({
    mutationFn: (data: { username: string; password: string; role: string }) =>
      api.users.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setIsModalOpen(false);
      toast.success('用户创建成功');
    },
    onError: (error: unknown) => {
      toast.error(`创建用户失败: ${toApiError(error).message}`);
    },
  });

  // Update user mutation
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { username?: string; password?: string; role?: string } }) =>
      api.users.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setEditUser(null);
      toast.success('用户更新成功');
    },
    onError: (error: unknown) => {
      toast.error(`更新用户失败: ${toApiError(error).message}`);
    },
  });

  // Delete user mutation
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.users.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success('用户删除成功');
    },
    onError: (error: unknown) => {
      toast.error(`删除用户失败: ${toApiError(error).message}`);
    },
  });

  // Toggle user active mutation
  const toggleActiveMutation = useMutation({
    mutationFn: (id: number) => api.users.toggleActive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (error: unknown) => {
      toast.error(`切换用户状态失败: ${toApiError(error).message}`);
    },
  });

  const handleEdit = (user: User) => {
    setEditUser(user);
  };

  const handleDelete = async (userId: number) => {
    const ok = await confirmDialog({ description: '确定要删除此用户吗？此操作无法撤销。', variant: 'destructive' });
    if (ok) {
      deleteMutation.mutate(userId);
    }
  };

  const handleToggleActive = (userId: number) => {
    toggleActiveMutation.mutate(userId);
  };

  const handleModalClose = () => {
    setIsModalOpen(false);
    setEditUser(null);
  };

  const handleModalSubmit = (data: { username: string; password?: string; role: string }) => {
    createMutation.mutate({ ...data, password: data.password || '' });
  };

  const handleModalUpdate = (data: { username?: string; password?: string; role?: string }) => {
    if (editUser) {
      updateMutation.mutate({ id: editUser.id, data });
    }
  };

  if (isLoading) {
    return (
      <PageContainer width="content">
        <PageHeader title="用户管理" subtitle="管理系统用户和权限" />
        <div className="flex items-center justify-center h-64">
          <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.subtitle)} />
        </div>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer width="content">
        <PageHeader title="用户管理" subtitle="管理系统用户和权限" />
        <InlineError
          message={
            error
              ? `加载用户失败：${toApiError(error).message}`
              : '加载用户失败，请确认已使用管理员账号登录'
          }
          onRetry={() => void refetch()}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="content">
      <PageHeader
        title="用户管理"
        subtitle="管理系统用户和权限"
        action={
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加用户
          </Button>
        }
      />

      {/* User Table */}
      {users && users.length > 0 ? (
        <UserTable
          users={users}
          currentUserId={currentUser?.id ?? 0}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onToggleActive={handleToggleActive}
        />
      ) : (
        <EmptyState
          title="还没有用户"
          description="添加您的第一个用户以开始使用"
          icon={<UsersIcon />}
          action={
            <Button onClick={() => setIsModalOpen(true)}>
              <Plus className="w-4 h-4" />
              添加用户
            </Button>
          }
        />
      )}

      {/* Add User Modal */}
      <UserModal
        isOpen={isModalOpen}
        onClose={handleModalClose}
        onSubmit={handleModalSubmit}
        isSubmitting={createMutation.isPending}
      />

      {/* Edit User Modal */}
      <UserModal
        isOpen={!!editUser}
        onClose={() => setEditUser(null)}
        onSubmit={(data) => createMutation.mutate({ ...data, password: data.password || '' })}
        onUpdate={handleModalUpdate}
        isSubmitting={updateMutation.isPending}
        editUser={editUser}
      />
    </PageContainer>
  );
}
