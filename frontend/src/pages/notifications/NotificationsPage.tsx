import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, NotificationChannel, AlertRule } from '@/utils/api';
import { notificationKeys } from '@/utils/api/queryKeys';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import {
  Plus,
  Trash2,
  Edit2,
  Bell,
  Send,
  ToggleLeft,
  ToggleRight,
  Loader2,
} from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState } from '@/components/ui/empty-state';
import { FORM, INTERACTIVE, MODAL, SEGMENTED, SKELETON_BLOCK, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

type TabKey = 'channels' | 'rules';

const EVENT_LABELS: Record<string, string> = {
  RUN_COMPLETED: '任务完成',
  RUN_FAILED: '任务失败',
  RISK_HIGH: '高风险告警',
  DEVICE_OFFLINE: '设备离线',
};

const CHANNEL_TYPE_LABELS: Record<string, string> = {
  WEBHOOK: 'Webhook',
  EMAIL: '邮件',
  DINGTALK: '钉钉',
};

export default function NotificationsPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabKey>('channels');

  // Channel form
  const [showChannelForm, setShowChannelForm] = useState(false);
  const [editingChannel, setEditingChannel] = useState<NotificationChannel | null>(null);
  const [channelForm, setChannelForm] = useState({ name: '', type: 'WEBHOOK' as string, url: '', enabled: true });

  // Rule form
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [ruleForm, setRuleForm] = useState({ name: '', event_type: 'RUN_FAILED' as string, channel_id: 0, enabled: true });

  const [actionLoading, setActionLoading] = useState(false);

  const channelsQ = useQuery({
    queryKey: notificationKeys.channels(),
    queryFn: async () => {
      const resp = await api.notifications.listChannels(0, 200);
      return resp.data.items;
    },
  });

  const rulesQ = useQuery({
    queryKey: notificationKeys.rules(),
    queryFn: async () => {
      const resp = await api.notifications.listRules(0, 200);
      return resp.data.items;
    },
  });

  const channels = channelsQ.data ?? [];
  const rules = rulesQ.data ?? [];
  const loading = channelsQ.isLoading || rulesQ.isLoading;

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: notificationKeys.channels() });
    qc.invalidateQueries({ queryKey: notificationKeys.rules() });
  };

  // Channel actions
  const handleSaveChannel = async () => {
    setActionLoading(true);
    try {
      const config: Record<string, any> = {};
      if (channelForm.type === 'WEBHOOK' || channelForm.type === 'DINGTALK') {
        config.url = channelForm.url;
      } else if (channelForm.type === 'EMAIL') {
        config.to = channelForm.url;
      }

      if (editingChannel) {
        await api.notifications.updateChannel(editingChannel.id, {
          name: channelForm.name,
          type: channelForm.type,
          config,
          enabled: channelForm.enabled,
        });
      } else {
        await api.notifications.createChannel({
          name: channelForm.name,
          type: channelForm.type,
          config,
          enabled: channelForm.enabled,
        });
      }
      setShowChannelForm(false);
      setEditingChannel(null);
      invalidateAll();
    } catch (err) {
      toast.error('保存失败');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteChannel = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此通知渠道吗？关联的告警规则也会被删除。', variant: 'destructive' }))) return;
    try {
      await api.notifications.deleteChannel(id);
      invalidateAll();
    } catch (err) {
      toast.error('删除失败');
    }
  };

  const handleTestChannel = async (id: number) => {
    try {
      await api.notifications.testChannel(id);
      toast.success('测试通知已发送');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '发送失败');
    }
  };

  const openEditChannel = (ch: NotificationChannel) => {
    setEditingChannel(ch);
    setChannelForm({
      name: ch.name,
      type: ch.type,
      url: ch.config?.url || ch.config?.to || '',
      enabled: ch.enabled,
    });
    setShowChannelForm(true);
  };

  // Rule actions
  const handleSaveRule = async () => {
    setActionLoading(true);
    try {
      if (editingRule) {
        await api.notifications.updateRule(editingRule.id, {
          name: ruleForm.name,
          event_type: ruleForm.event_type,
          channel_id: ruleForm.channel_id,
          enabled: ruleForm.enabled,
        });
      } else {
        await api.notifications.createRule({
          name: ruleForm.name,
          event_type: ruleForm.event_type,
          channel_id: ruleForm.channel_id,
          enabled: ruleForm.enabled,
        });
      }
      setShowRuleForm(false);
      setEditingRule(null);
      invalidateAll();
    } catch (err) {
      toast.error('保存失败');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteRule = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此告警规则吗？', variant: 'destructive' }))) return;
    try {
      await api.notifications.deleteRule(id);
      invalidateAll();
    } catch (err) {
      toast.error('删除失败');
    }
  };

  const openEditRule = (rule: AlertRule) => {
    setEditingRule(rule);
    setRuleForm({
      name: rule.name,
      event_type: rule.event_type,
      channel_id: rule.channel_id,
      enabled: rule.enabled,
    });
    setShowRuleForm(true);
  };

  const tabBtnClass = (active: boolean) =>
    cn(
      'px-4 py-2 text-sm rounded-md transition-colors',
      active ? 'bg-card text-foreground shadow-sm' : SEGMENTED.toggleIdle,
    );

  return (
    <PageContainer width="default">
      <PageHeader title="通知管理" subtitle="配置通知渠道和告警规则" />

      {/* Tabs */}
      <div className={cn(SEGMENTED.track, 'w-fit text-sm bg-muted border-0 p-1')}>
        <button onClick={() => setTab('channels')} className={tabBtnClass(tab === 'channels')}>
          通知渠道 ({channels.length})
        </button>
        <button onClick={() => setTab('rules')} className={tabBtnClass(tab === 'rules')}>
          告警规则 ({rules.length})
        </button>
      </div>

      {loading ? (
        <div className="space-y-3">
          <div className={cn(SKELETON_BLOCK, 'h-32')} />
          <div className={cn(SKELETON_BLOCK, 'h-32')} />
        </div>
      ) : tab === 'channels' ? (
        <div className="space-y-3">
          <div className="flex justify-end">
            <Button
              onClick={() => { setEditingChannel(null); setChannelForm({ name: '', type: 'WEBHOOK', url: '', enabled: true }); setShowChannelForm(true); }}
            >
              <Plus size={16} /> 添加渠道
            </Button>
          </div>

          {channels.length === 0 ? (
            <EmptyState
              title="暂无通知渠道"
              description="添加通知渠道以接收告警"
              icon={<Bell className="w-16 h-16" />}
            />
          ) : (
            channels.map((ch) => (
              <Card key={ch.id} className="px-5 py-4 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <Bell size={16} className={TEXT.subtitle} />
                    <span className={cn('font-medium', TEXT.heading)}>{ch.name}</span>
                    <span className={cn('text-xs px-2 py-0.5 rounded', STATUS_CHIP.muted)}>
                      {CHANNEL_TYPE_LABELS[ch.type] || ch.type}
                    </span>
                    {ch.enabled ? (
                      <ToggleRight size={16} className="text-success" />
                    ) : (
                      <ToggleLeft size={16} className={TEXT.subtitle} />
                    )}
                  </div>
                  <p className={cn('text-xs mt-1', TEXT.subtitle)}>
                    {ch.config?.url || ch.config?.to || '-'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleTestChannel(ch.id)}
                    className={cn('p-1.5 rounded', INTERACTIVE.iconButton, 'hover:text-primary hover:bg-primary/10')}
                    title="测试"
                    aria-label={`测试渠道 ${ch.name}`}
                  >
                    <Send size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => openEditChannel(ch)}
                    className={cn('p-1.5 rounded', INTERACTIVE.iconButton)}
                    title="编辑"
                    aria-label={`编辑渠道 ${ch.name}`}
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteChannel(ch.id)}
                    className={cn('p-1.5 rounded', INTERACTIVE.destructiveMenu)}
                    title="删除"
                    aria-label={`删除渠道 ${ch.name}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </Card>
            ))
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex justify-end">
            <Button
              onClick={() => { setEditingRule(null); setRuleForm({ name: '', event_type: 'RUN_FAILED', channel_id: channels[0]?.id || 0, enabled: true }); setShowRuleForm(true); }}
              disabled={channels.length === 0}
            >
              <Plus size={16} /> 添加规则
            </Button>
          </div>

          {rules.length === 0 ? (
            <EmptyState
              title={channels.length === 0 ? '请先添加通知渠道' : '暂无告警规则'}
              description={channels.length === 0 ? '需要先创建通知渠道才能设置规则' : '添加告警规则以触发通知'}
              icon={<Bell className="w-16 h-16" />}
            />
          ) : (
            rules.map((rule) => (
              <Card key={rule.id} className="px-5 py-4 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className={cn('font-medium', TEXT.heading)}>{rule.name}</span>
                    <span className={cn('text-xs px-2 py-0.5 rounded', STATUS_CHIP.warning)}>
                      {EVENT_LABELS[rule.event_type] || rule.event_type}
                    </span>
                    <span className={cn('text-xs', TEXT.subtitle)}>
                      → {rule.channel_name || `渠道 #${rule.channel_id}`}
                    </span>
                    {rule.enabled ? (
                      <ToggleRight size={16} className="text-success" />
                    ) : (
                      <ToggleLeft size={16} className={TEXT.subtitle} />
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => openEditRule(rule)}
                    className={cn('p-1.5 rounded', INTERACTIVE.iconButton)}
                    title="编辑"
                    aria-label={`编辑规则 ${rule.name}`}
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteRule(rule.id)}
                    className={cn('p-1.5 rounded', INTERACTIVE.destructiveMenu)}
                    title="删除"
                    aria-label={`删除规则 ${rule.name}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </Card>
            ))
          )}
        </div>
      )}

      {/* Channel Form Modal */}
      {showChannelForm && (
        <div className={MODAL.overlay}>
          <div className="fixed inset-0" onClick={() => setShowChannelForm(false)} />
          <div className={cn(MODAL.panelLg, 'relative')}>
            <h3 className={cn(MODAL.title, 'mb-4')}>{editingChannel ? '编辑渠道' : '添加渠道'}</h3>
            <div className="space-y-4">
              <div>
                <label className={FORM.label}>名称</label>
                <input
                  value={channelForm.name}
                  onChange={(e) => setChannelForm({ ...channelForm, name: e.target.value })}
                  className={FORM.input}
                  placeholder="渠道名称"
                />
              </div>
              <div>
                <label className={FORM.label}>类型</label>
                <select
                  value={channelForm.type}
                  onChange={(e) => setChannelForm({ ...channelForm, type: e.target.value })}
                  className={FORM.select}
                >
                  <option value="WEBHOOK">Webhook</option>
                  <option value="DINGTALK">钉钉</option>
                  <option value="EMAIL">邮件</option>
                </select>
              </div>
              <div>
                <label className={FORM.label}>
                  {channelForm.type === 'EMAIL' ? '收件人邮箱' : 'Webhook URL'}
                </label>
                <input
                  value={channelForm.url}
                  onChange={(e) => setChannelForm({ ...channelForm, url: e.target.value })}
                  className={FORM.input}
                  placeholder={channelForm.type === 'EMAIL' ? 'user@example.com' : 'https://...'}
                />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={channelForm.enabled}
                  onChange={(e) => setChannelForm({ ...channelForm, enabled: e.target.checked })}
                />
                启用
              </label>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button variant="outline" onClick={() => setShowChannelForm(false)}>取消</Button>
              <Button
                onClick={handleSaveChannel}
                disabled={actionLoading || !channelForm.name}
              >
                {actionLoading ? <Loader2 size={16} className="animate-spin" /> : '保存'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Rule Form Modal */}
      {showRuleForm && (
        <div className={MODAL.overlay}>
          <div className="fixed inset-0" onClick={() => setShowRuleForm(false)} />
          <div className={cn(MODAL.panelLg, 'relative')}>
            <h3 className={cn(MODAL.title, 'mb-4')}>{editingRule ? '编辑规则' : '添加规则'}</h3>
            <div className="space-y-4">
              <div>
                <label className={FORM.label}>名称</label>
                <input
                  value={ruleForm.name}
                  onChange={(e) => setRuleForm({ ...ruleForm, name: e.target.value })}
                  className={FORM.input}
                  placeholder="规则名称"
                />
              </div>
              <div>
                <label className={FORM.label}>事件类型</label>
                <select
                  value={ruleForm.event_type}
                  onChange={(e) => setRuleForm({ ...ruleForm, event_type: e.target.value })}
                  className={FORM.select}
                >
                  {Object.entries(EVENT_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className={FORM.label}>通知渠道</label>
                <select
                  value={ruleForm.channel_id}
                  onChange={(e) => setRuleForm({ ...ruleForm, channel_id: Number(e.target.value) })}
                  className={FORM.select}
                >
                  {channels.map((ch) => (
                    <option key={ch.id} value={ch.id}>{ch.name} ({CHANNEL_TYPE_LABELS[ch.type]})</option>
                  ))}
                </select>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={ruleForm.enabled}
                  onChange={(e) => setRuleForm({ ...ruleForm, enabled: e.target.checked })}
                />
                启用
              </label>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button variant="outline" onClick={() => setShowRuleForm(false)}>取消</Button>
              <Button
                onClick={handleSaveRule}
                disabled={actionLoading || !ruleForm.name || !ruleForm.channel_id}
              >
                {actionLoading ? <Loader2 size={16} className="animate-spin" /> : '保存'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
