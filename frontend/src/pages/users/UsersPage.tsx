import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2, AlertCircle } from 'lucide-react';
import { CleanCard } from '@/components/ui/clean-card';
import { CleanButton } from '@/components/ui/clean-button';
import { UserTable } from './components/UserTable';
import { UserModal } from './components/UserModal';
import { api, type User } from '@/utils/api';

export default function UsersPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [currentUserId, setCurrentUserId] = useState<number>(0);
  const queryClient = useQueryClient();

  // Get current user info
  useEffect(() => {
    const getCurrentUser = async () => {
      try {
        const res = await api.auth.me();
        setCurrentUserId(res.data.id);
      } catch (error) {
        console.error('Failed to get current user:', error);
      }
    };
    getCurrentUser();
  }, []);

  // Fetch users list
  const { data: users, isLoading, error } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.users.list().then(res => res.data),
  });

  // Create user mutation
  const createMutation = useMutation({
    mutationFn: (data: { username: string; password: string; role: string }) =>
      api.users.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setIsModalOpen(false);
      alert('User created successfully');
    },
    onError: (error: any) => {
      alert(`Failed to create user: ${error.response?.data?.detail || error.message}`);
    },
  });

  // Update user mutation
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { username?: string; password?: string; role?: string } }) =>
      api.users.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setEditUser(null);
      alert('User updated successfully');
    },
    onError: (error: any) => {
      alert(`Failed to update user: ${error.response?.data?.detail || error.message}`);
    },
  });

  // Delete user mutation
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.users.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      alert('User deleted successfully');
    },
    onError: (error: any) => {
      alert(`Failed to delete user: ${error.response?.data?.detail || error.message}`);
    },
  });

  // Toggle user active mutation
  const toggleActiveMutation = useMutation({
    mutationFn: (id: number) => api.users.toggleActive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (error: any) => {
      alert(`Failed to toggle user status: ${error.response?.data?.detail || error.message}`);
    },
  });

  const handleEdit = (user: User) => {
    setEditUser(user);
  };

  const handleDelete = (userId: number) => {
    if (confirm('Are you sure you want to delete this user? This action cannot be undone.')) {
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
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">用户管理</h2>
          <p className="text-sm text-gray-400">管理系统用户和权限</p>
        </div>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">用户管理</h2>
          <p className="text-sm text-gray-400">管理系统用户和权限</p>
        </div>
        <CleanCard className="p-6">
          <div className="flex items-center gap-3 text-red-600">
            <AlertCircle className="w-5 h-5" />
            <div>
              <p className="font-medium">Failed to load users</p>
              <p className="text-sm text-slate-500">
                {error instanceof Error ? error.message : 'Please check if you have admin privileges'}
              </p>
            </div>
          </div>
        </CleanCard>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">用户管理</h2>
          <p className="text-sm text-gray-400">管理系统用户和权限</p>
        </div>
        <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
          <Plus className="w-4 h-4" />
          添加用户
        </CleanButton>
      </div>

      {/* User Table */}
      {users && users.length > 0 ? (
        <UserTable
          users={users}
          currentUserId={currentUserId}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onToggleActive={handleToggleActive}
        />
      ) : (
        <CleanCard className="p-12 text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-slate-50 flex items-center justify-center">
            <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无用户</h3>
          <p className="text-sm text-gray-400 mb-4">添加您的第一个用户以开始使用。</p>
          <CleanButton variant="primary" onClick={() => setIsModalOpen(true)}>
            <Plus className="w-4 h-4" />
            添加用户
          </CleanButton>
        </CleanCard>
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
    </div>
  );
}
