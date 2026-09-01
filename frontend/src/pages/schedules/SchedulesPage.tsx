import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, toApiError, type TaskSchedule, type TaskScheduleCreatePayload } from '@/utils/api';
import { planKeys, scheduleKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { CronExpressionInput } from '@/components/schedule/CronExpressionInput';
import { DeviceMultiSelect } from '@/components/schedule/DeviceMultiSelect';
import { Plus, Trash2, Edit2, Play, Power, Clock, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { FORM, INTERACTIVE, LAYOUT, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';

interface ScheduleForm {
  name: string;
  cron_expr: string;
  plan_id: string;
  deviceIds: number[];
  enabled: boolean;
}

const DEFAULT_FORM: ScheduleForm = {
  name: '',
  cron_expr: '0 2 * * *',
  plan_id: '',
  deviceIds: [],
  enabled: true,
};

export default function SchedulesPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<TaskSchedule | null>(null);
  const [form, setForm] = useState<ScheduleForm>(DEFAULT_FORM);
  const formRef = useRef<HTMLDivElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // C1：数据获取迁移 react-query（缓存/重试/去重与全站一致）
  const schedulesQ = useQuery({
    queryKey: scheduleKeys.list(),
    queryFn: async () => {
      const res = await api.schedules.list(0, 200);
      return res.items || [];
    },
  });
  const plansQ = useQuery({
    queryKey: planKeys.list(200),
    queryFn: () => api.plans.list(0, 200),
  });
  const schedules = schedulesQ.data ?? [];
  const plans = plansQ.data ?? [];
  const loading = schedulesQ.isLoading || plansQ.isLoading;
  const loadError = schedulesQ.isError
    ? (schedulesQ.error as Error)?.message ?? '加载失败'
    : null;
  const loadAll = () => {
    void schedulesQ.refetch();
    void plansQ.refetch();
  };

  const handleSave = async () => {
    try {
      if (!form.name.trim()) {
        toast.error('请填写任务名称');
        return;
      }
      if (!form.cron_expr.trim()) {
        toast.error('请填写 Cron 表达式');
        return;
      }
      const planId = Number(form.plan_id);

      if (!Number.isInteger(planId) || planId <= 0) {
        toast.error('请选择 Plan');
        return;
      }
      if (form.deviceIds.length === 0) {
        toast.error('请至少选择一台设备');
        return;
      }

      const payload: TaskScheduleCreatePayload = {
        name: form.name,
        cron_expr: form.cron_expr,
        enabled: form.enabled,
        plan_id: planId,
        device_ids: form.deviceIds,
      };

      if (editing) {
        await api.schedules.update(editing.id, payload);
        toast.success('定时任务更新成功');
      } else {
        await api.schedules.create(payload);
        toast.success('定时任务创建成功');
      }

      setShowForm(false);
      setEditing(null);
      setForm(DEFAULT_FORM);
      qc.invalidateQueries({ queryKey: scheduleKeys.list() });
    } catch (err: unknown) {
      toast.error(toApiError(err).message);
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此定时任务吗？', variant: 'destructive' }))) return;
    try {
      await api.schedules.delete(id);
      qc.invalidateQueries({ queryKey: scheduleKeys.list() });
    } catch (err: unknown) {
      // C5：错误文案带后端详情，与 handleSave 粒度一致
      toast.error(toApiError(err).message);
    }
  };

  const handleToggle = async (id: number) => {
    try {
      await api.schedules.toggle(id);
      qc.invalidateQueries({ queryKey: scheduleKeys.list() });
    } catch (err: unknown) {
      // C5：错误文案带后端详情
      toast.error(toApiError(err).message);
    }
  };

  const handleRunNow = async (id: number) => {
    try {
      const res = await api.schedules.runNow(id);
      const planRunId = res.plan_run_id;
      if (planRunId) {
        toast.success(`Plan 已触发，Run ID: ${planRunId}`);
      } else {
        toast.success('Plan 已触发');
      }
    } catch (err: unknown) {
      toast.error(toApiError(err).message);
    }
  };

  const openEdit = (s: TaskSchedule) => {
    setEditing(s);
    setForm({
      name: s.name,
      cron_expr: s.cron_expr,
      plan_id: s.plan_id ? String(s.plan_id) : '',
      deviceIds: s.device_ids || [],
      enabled: s.enabled,
    });
    setShowForm(true);
  };

  const openCreate = () => {
    setEditing(null);
    setForm(DEFAULT_FORM);
    setShowForm(true);
  };

  const closeForm = useCallback(() => {
    setShowForm(false);
    setEditing(null);
    setForm(DEFAULT_FORM);
  }, []);

  // D5：表单是内联卡片，从列表下方点「编辑」时页面不动、焦点也不动，
  // 用户常以为没点上。打开时滚到表单并聚焦首个字段。
  useEffect(() => {
    if (!showForm) return;
    formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    nameInputRef.current?.focus();
  }, [showForm, editing]);

  // D5：内联表单此前没有 ESC 关闭，退出只能滚回顶部点「取消」。
  useEffect(() => {
    if (!showForm) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeForm();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [showForm, closeForm]);

  if (loading) {
    return (
      <PageContainer width="content">
        <PageHeader title="定时任务" subtitle="管理 Cron 定时执行的 Plan" />
        <PageSkeleton>
          <PageSkeleton.Block size="md" />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
      </PageContainer>
    );
  }

  if (loadError) {
    return (
      <PageContainer width="content">
        <PageHeader
          title="定时任务"
          subtitle="管理 Cron 定时执行的 Plan"
          action={
            <Button onClick={loadAll} size="sm">
              <RefreshCw className="w-4 h-4" />
              重试
            </Button>
          }
        />
        <ErrorState
          title="加载定时任务失败"
          description={loadError}
          onRetry={loadAll}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="content" scrollable={false} className={cn(LAYOUT.pageGap, 'min-h-0')}>
      <PageHeader
        title="定时任务"
        subtitle="管理 Cron 定时执行的 Plan"
        action={
          <Button onClick={openCreate} size="sm">
            <Plus className="w-4 h-4" />
            新建定时任务
          </Button>
        }
      />

      {showForm && (
        <div
          ref={formRef}
          role="group"
          aria-label={editing ? '编辑定时任务' : '新建定时任务'}
          className={cn('shrink-0 rounded-xl border p-6 max-w-lg', PANEL.root, 'overflow-visible')}
        >
          <h3 className={cn('text-lg font-medium mb-4', TEXT.heading)}>
            {editing ? '编辑定时任务' : '新建定时任务'}
          </h3>
          <div className="space-y-4">
            <div>
              <label htmlFor="schedule-name" className={cn('block text-sm font-medium mb-1', TEXT.body)}>名称</label>
              <input
                id="schedule-name"
                ref={nameInputRef}
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className={FORM.input}
              />
            </div>
            <div>
              <label htmlFor="schedule-cron" className={cn('block text-sm font-medium mb-1', TEXT.body)}>Cron 表达式</label>
              <CronExpressionInput
                value={form.cron_expr}
                onChange={(v) => setForm({ ...form, cron_expr: v })}
              />
            </div>
            <div>
              <label htmlFor="schedule-plan" className={cn('block text-sm font-medium mb-1', TEXT.body)}>Plan 蓝图</label>
              <select
                id="schedule-plan"
                value={form.plan_id}
                onChange={(e) => setForm({ ...form, plan_id: e.target.value })}
                className={FORM.select}
              >
                <option value="">请选择 Plan</option>
                {plans.map(p => (
                  <option key={p.id} value={String(p.id)}>{p.name} (#{p.id})</option>
                ))}
              </select>
            </div>
            <div>
              <span className={cn('block text-sm font-medium mb-1', TEXT.body)}>设备（可多选）</span>
              <DeviceMultiSelect
                selectedIds={form.deviceIds}
                onChange={(ids) => setForm({ ...form, deviceIds: ids })}
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                id="schedule-enabled"
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                className="rounded"
              />
              <label htmlFor="schedule-enabled" className={cn('text-sm', TEXT.body)}>启用</label>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleSave} size="sm">保存</Button>
              <Button variant="outline" size="sm" onClick={closeForm}>
                取消
              </Button>
            </div>
          </div>
        </div>
      )}

      {schedules.length === 0 ? (
        <EmptyState
          title="暂无定时任务"
          description="创建定时任务以自动执行 Plan"
          icon={<Clock className="w-16 h-16" />}
        />
      ) : (
        <div className={cn(PANEL.root, 'min-h-0 flex-1 overflow-auto')}>
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow className="border-b bg-muted/50">
                <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>名称</TableHead>
                <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>Cron</TableHead>
                <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>执行对象</TableHead>
                <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>状态</TableHead>
                <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>下次执行</TableHead>
                <TableHead className={cn('text-right px-4 py-3 font-medium', TEXT.subtitle)}>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {schedules.map((s) => (
                <TableRow key={s.id} className="border-b hover:bg-muted/30">
                  <TableCell className={cn('px-4 py-3 font-medium', TEXT.heading)}>{s.name}</TableCell>
                  <TableCell className={cn('px-4 py-3 font-mono', TEXT.subtitle)}>{s.cron_expr}</TableCell>
                  <TableCell className={cn('px-4 py-3', TEXT.subtitle)}>
                    Plan #{s.plan_id}（{(s.device_ids || []).length} 台设备）
                  </TableCell>
                  <TableCell className="px-4 py-3">
                    <span className={cn(
                      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium',
                      s.enabled ? STATUS_CHIP.success : STATUS_CHIP.muted,
                    )}>
                      {s.enabled ? '启用' : '禁用'}
                    </span>
                  </TableCell>
                  <TableCell className={cn('px-4 py-3 text-xs', TEXT.subtitle)}>
                    {s.next_run_at ? formatDateTimeFull(s.next_run_at) : '-'}
                  </TableCell>
                  <TableCell className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => handleRunNow(s.id)} title="立即执行" aria-label="立即执行" className={cn('p-1.5 rounded', INTERACTIVE.iconButton, 'hover:text-primary')}>
                        <Play className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleToggle(s.id)} title="切换状态" aria-label="切换状态" className={cn('p-1.5 rounded', INTERACTIVE.iconButton, 'hover:text-warning')}>
                        <Power className="w-4 h-4" />
                      </button>
                      <button onClick={() => openEdit(s)} title="编辑" aria-label="编辑" className={cn('p-1.5 rounded', INTERACTIVE.iconButton)}>
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleDelete(s.id)} title="删除" aria-label="删除" className={cn('p-1.5 rounded', INTERACTIVE.iconDanger)}>
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </PageContainer>
  );
}
