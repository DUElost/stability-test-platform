import { useEffect, useState } from 'react';
import { api, type TaskSchedule, type TaskScheduleCreatePayload, type Plan } from '@/utils/api';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { CronExpressionInput } from '@/components/schedule/CronExpressionInput';
import { Plus, Trash2, Edit2, Play, Power, Clock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState } from '@/components/ui/empty-state';
import { INTERACTIVE, PANEL, SKELETON_BLOCK, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';

interface ScheduleForm {
  name: string;
  cron_expression: string;
  plan_id: string;
  device_ids: string;
  enabled: boolean;
}

const DEFAULT_FORM: ScheduleForm = {
  name: '',
  cron_expression: '0 2 * * *',
  plan_id: '',
  device_ids: '',
  enabled: true,
};

function parseDeviceIds(input: string): number[] {
  const values = (input || '')
    .split(',')
    .map(v => Number(v.trim()))
    .filter(v => Number.isInteger(v) && v > 0);
  return Array.from(new Set(values));
}

export default function SchedulesPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [schedules, setSchedules] = useState<TaskSchedule[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<TaskSchedule | null>(null);
  const [form, setForm] = useState<ScheduleForm>(DEFAULT_FORM);

  const loadSchedules = async () => {
    const res = await api.schedules.list(0, 200);
    setSchedules(res.data.items || []);
  };

  const loadPlans = async () => {
    const list = await api.plans.list(0, 200);
    setPlans(list || []);
  };

  const loadAll = async () => {
    try {
      await Promise.all([loadSchedules(), loadPlans()]);
    } catch {
      toast.error('加载定时任务失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleSave = async () => {
    try {
      const planId = Number(form.plan_id);
      const deviceIds = parseDeviceIds(form.device_ids);

      if (!Number.isInteger(planId) || planId <= 0) {
        toast.error('请选择 Plan');
        return;
      }
      if (deviceIds.length === 0) {
        toast.error('请至少填写一个设备 ID');
        return;
      }

      const payload: TaskScheduleCreatePayload = {
        name: form.name,
        cron_expression: form.cron_expression,
        enabled: form.enabled,
        plan_id: planId,
        device_ids: deviceIds,
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
      await loadSchedules();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || err.message || '保存失败');
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此定时任务吗？', variant: 'destructive' }))) return;
    try {
      await api.schedules.delete(id);
      await loadSchedules();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (id: number) => {
    try {
      await api.schedules.toggle(id);
      await loadSchedules();
    } catch {
      toast.error('切换失败');
    }
  };

  const handleRunNow = async (id: number) => {
    try {
      const res = await api.schedules.runNow(id);
      const planRunId = res.data.plan_run_id;
      if (planRunId) {
        toast.success(`Plan 已触发，Run ID: ${planRunId}`);
      } else {
        toast.success('Plan 已触发');
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '执行失败');
    }
  };

  const openEdit = (s: TaskSchedule) => {
    setEditing(s);
    setForm({
      name: s.name,
      cron_expression: s.cron_expression,
      plan_id: s.plan_id ? String(s.plan_id) : '',
      device_ids: (s.device_ids || []).join(','),
      enabled: s.enabled,
    });
    setShowForm(true);
  };

  const openCreate = () => {
    setEditing(null);
    setForm(DEFAULT_FORM);
    setShowForm(true);
  };

  if (loading) {
    return (
      <PageContainer width="default">
        <PageHeader title="定时任务" subtitle="管理 Cron 定时执行的 Plan" />
        <div className="space-y-4">
          <div className={cn('h-32', SKELETON_BLOCK)} />
          <div className={cn('h-64', SKELETON_BLOCK)} />
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer width="default">
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
        <div className={cn('rounded-xl border p-6 max-w-lg', PANEL.root)}>
          <h3 className={cn('text-lg font-medium mb-4', TEXT.heading)}>
            {editing ? '编辑定时任务' : '新建定时任务'}
          </h3>
          <div className="space-y-4">
            <div>
              <label className={cn('block text-sm font-medium mb-1', TEXT.body)}>名称</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded-lg border bg-card px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <div>
              <label className={cn('block text-sm font-medium mb-1', TEXT.body)}>Cron 表达式</label>
              <CronExpressionInput
                value={form.cron_expression}
                onChange={(v) => setForm({ ...form, cron_expression: v })}
              />
            </div>
            <div>
              <label className={cn('block text-sm font-medium mb-1', TEXT.body)}>Plan 蓝图</label>
              <select
                value={form.plan_id}
                onChange={(e) => setForm({ ...form, plan_id: e.target.value })}
                className="w-full rounded-lg border bg-card px-3 py-2 text-sm"
              >
                <option value="">请选择 Plan</option>
                {plans.map(p => (
                  <option key={p.id} value={String(p.id)}>{p.name} (#{p.id})</option>
                ))}
              </select>
            </div>
            <div>
              <label className={cn('block text-sm font-medium mb-1', TEXT.body)}>设备 IDs（逗号分隔）</label>
              <input
                type="text"
                value={form.device_ids}
                onChange={(e) => setForm({ ...form, device_ids: e.target.value })}
                placeholder="例如: 1,2,3"
                className="w-full rounded-lg border bg-card px-3 py-2 text-sm"
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                className="rounded"
              />
              <span className={cn('text-sm', TEXT.body)}>启用</span>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleSave} size="sm">保存</Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => { setShowForm(false); setEditing(null); setForm(DEFAULT_FORM); }}
              >
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
        <div className={cn('overflow-hidden', PANEL.root)}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>名称</th>
                <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>Cron</th>
                <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>执行对象</th>
                <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>状态</th>
                <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>下次执行</th>
                <th className={cn('text-right px-4 py-3 font-medium', TEXT.subtitle)}>操作</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id} className="border-b hover:bg-muted/30">
                  <td className={cn('px-4 py-3 font-medium', TEXT.heading)}>{s.name}</td>
                  <td className={cn('px-4 py-3 font-mono', TEXT.subtitle)}>{s.cron_expression}</td>
                  <td className={cn('px-4 py-3', TEXT.subtitle)}>
                    Plan #{s.plan_id} ({(s.device_ids || []).length} devices)
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn(
                      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium',
                      s.enabled ? STATUS_CHIP.success : STATUS_CHIP.muted,
                    )}>
                      {s.enabled ? '启用' : '禁用'}
                    </span>
                  </td>
                  <td className={cn('px-4 py-3 text-xs', TEXT.subtitle)}>
                    {s.next_run_at ? formatDateTimeFull(s.next_run_at) : '-'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => handleRunNow(s.id)} title="立即执行" className={cn('p-1.5 rounded', INTERACTIVE.iconButton, 'hover:text-primary')}>
                        <Play className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleToggle(s.id)} title="切换状态" className={cn('p-1.5 rounded', INTERACTIVE.iconButton, 'hover:text-warning')}>
                        <Power className="w-4 h-4" />
                      </button>
                      <button onClick={() => openEdit(s)} title="编辑" className={cn('p-1.5 rounded', INTERACTIVE.iconButton)}>
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleDelete(s.id)} title="删除" className={cn('p-1.5 rounded', INTERACTIVE.iconButton, 'hover:text-destructive')}>
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PageContainer>
  );
}
