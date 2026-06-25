import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2, AlertCircle } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { useAuthSession } from '@/hooks/useAuthSession';
import { UserTable } from './components/UserTable';
import { UserModal } from './components/UserModal';
import { api, type User } from '@/utils/api';
import { PageContainer, PageHeader } from '@/components/layout';
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
  const { data: users, isLoading, error } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.users.list(0, 200).then(res => res.data.items),
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
    onError: (error: any) => {
      toast.error(`创建用户失败: ${error.response?.data?.detail || error.message}`);
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
    onError: (error: any) => {
      toast.error(`更新用户失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  // Delete user mutation
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.users.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success('用户删除成功');
    },
    onError: (error: any) => {
      toast.error(`删除用户失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  // Toggle user active mutation
  const toggleActiveMutation = useMutation({
    mutationFn: (id: number) => api.users.toggleActive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (error: any) => {
      toast.error(`切换用户状态失败: ${error.response?.data?.detail || error.message}`);
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

  const handleModalSubmit = (data: { username: string; password: string; role: string }) => {
    createMutation.mutate(data);
  };

  const handleModalUpdate = (data: { username?: string; password?: string; role?: string }) => {
    if (editUser) {
      updateMutation.mutate({ id: editUser.id, data });
    }
  };

  if (isLoading) {
    return (
      <PageContainer>
        <PageHeader title="用户管理" subtitle="管理系统用户和权限" />
        <div className="flex items-center justify-center h-64">
          <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.subtitle)} />
        </div>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer>
        <PageHeader title="用户管理" subtitle="管理系统用户和权限" />
        <Card className="p-6">
          <div className={cn('flex items-center gap-3', TEXT.destructive)}>
            <AlertCircle className="w-5 h-5" />
            <div>
              <p className="font-medium">Failed to load users</p>
              <p className={cn('text-sm', TEXT.subtitle)}>
                {error instanceof Error ? error.message : 'Please check if you have admin privileges'}
              </p>
            </div>
          </div>
        </Card>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
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
        <Card className="p-12 text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-muted flex items-center justify-center">
            <svg className={cn('w-8 h-8', TEXT.subtitle)} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          </div>
          <h3 className={cn('text-lg font-medium mb-2', TEXT.heading)}>暂无用户</h3>
          <p className={cn('text-sm mb-4', TEXT.subtitle)}>添加您的第一个用户以开始使用。</p>
          <Button onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加用户
          </Button>
        </Card>
      )}

      {/* Add User Modal */}
      <UserModal
        isOpen={isModalOpen}
        onClose={handleModalClose}
        onSubmit={handleModalSubmit as any}
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
