import { useState, useEffect } from 'react';
import { api, NotificationChannel, AlertRule } from '@/utils/api';
import { CleanCard } from '@/components/ui/clean-card';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import {
  Plus,
  Trash2,
  Edit2,
  Loader2,
  Bell,
  Send,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';

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
  const [tab, setTab] = useState<TabKey>('channels');
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);

  // Channel form
  const [showChannelForm, setShowChannelForm] = useState(false);
  const [editingChannel, setEditingChannel] = useState<NotificationChannel | null>(null);
  const [channelForm, setChannelForm] = useState({ name: '', type: 'WEBHOOK' as string, url: '', enabled: true });

  // Rule form
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [ruleForm, setRuleForm] = useState({ name: '', event_type: 'RUN_FAILED' as string, channel_id: 0, enabled: true });

  const [actionLoading, setActionLoading] = useState(false);

  const loadData = async () => {
    try {
      const [chResp, ruleResp] = await Promise.all([
        api.notifications.listChannels(0, 200),
        api.notifications.listRules(0, 200),
      ]);
      setChannels(chResp.data.items);
      setRules(ruleResp.data.items);
    } catch (err) {
      console.error('加载通知配置失败:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

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
      loadData();
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
      loadData();
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
      loadData();
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
      loadData();
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900 mb-1">通知管理</h2>
        <p className="text-sm text-gray-400">配置通知渠道和告警规则</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit">
        <button
          onClick={() => setTab('channels')}
          className={`px-4 py-2 text-sm rounded-md transition-colors ${tab === 'channels' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
        >
          通知渠道 ({channels.length})
        </button>
        <button
          onClick={() => setTab('rules')}
          className={`px-4 py-2 text-sm rounded-md transition-colors ${tab === 'rules' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
        >
          告警规则 ({rules.length})
        </button>
      </div>

      {loading ? (
        <CleanCard className="p-8 text-center">
          <Loader2 className="w-8 h-8 mx-auto animate-spin text-gray-400" />
        </CleanCard>
      ) : tab === 'channels' ? (
        <div className="space-y-3">
          <div className="flex justify-end">
            <button
              onClick={() => { setEditingChannel(null); setChannelForm({ name: '', type: 'WEBHOOK', url: '', enabled: true }); setShowChannelForm(true); }}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm"
            >
              <Plus size={16} /> 添加渠道
            </button>
          </div>

          {channels.length === 0 ? (
            <CleanCard className="p-8 text-center text-gray-400">暂无通知渠道</CleanCard>
          ) : (
            channels.map((ch) => (
              <CleanCard key={ch.id} className="px-5 py-4 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <Bell size={16} className="text-gray-500" />
                    <span className="font-medium text-gray-900">{ch.name}</span>
                    <span className="text-xs px-2 py-0.5 bg-gray-100 rounded text-gray-600">
                      {CHANNEL_TYPE_LABELS[ch.type] || ch.type}
                    </span>
                    {ch.enabled ? (
                      <ToggleRight size={16} className="text-green-500" />
                    ) : (
                      <ToggleLeft size={16} className="text-gray-400" />
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-1">
                    {ch.config?.url || ch.config?.to || '-'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => handleTestChannel(ch.id)} className="p-1.5 text-blue-600 hover:bg-blue-50 rounded" title="测试">
                    <Send size={14} />
                  </button>
                  <button onClick={() => openEditChannel(ch)} className="p-1.5 text-gray-500 hover:bg-gray-50 rounded" title="编辑">
                    <Edit2 size={14} />
                  </button>
                  <button onClick={() => handleDeleteChannel(ch.id)} className="p-1.5 text-red-500 hover:bg-red-50 rounded" title="删除">
                    <Trash2 size={14} />
                  </button>
                </div>
              </CleanCard>
            ))
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex justify-end">
            <button
              onClick={() => { setEditingRule(null); setRuleForm({ name: '', event_type: 'RUN_FAILED', channel_id: channels[0]?.id || 0, enabled: true }); setShowRuleForm(true); }}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm"
              disabled={channels.length === 0}
            >
              <Plus size={16} /> 添加规则
            </button>
          </div>

          {rules.length === 0 ? (
            <CleanCard className="p-8 text-center text-gray-400">
              {channels.length === 0 ? '请先添加通知渠道' : '暂无告警规则'}
            </CleanCard>
          ) : (
            rules.map((rule) => (
              <CleanCard key={rule.id} className="px-5 py-4 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-900">{rule.name}</span>
                    <span className="text-xs px-2 py-0.5 bg-orange-50 text-orange-600 rounded">
                      {EVENT_LABELS[rule.event_type] || rule.event_type}
                    </span>
                    <span className="text-xs text-gray-400">→ {rule.channel_name || `Channel #${rule.channel_id}`}</span>
                    {rule.enabled ? (
                      <ToggleRight size={16} className="text-green-500" />
                    ) : (
                      <ToggleLeft size={16} className="text-gray-400" />
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => openEditRule(rule)} className="p-1.5 text-gray-500 hover:bg-gray-50 rounded" title="编辑">
                    <Edit2 size={14} />
                  </button>
                  <button onClick={() => handleDeleteRule(rule.id)} className="p-1.5 text-red-500 hover:bg-red-50 rounded" title="删除">
                    <Trash2 size={14} />
                  </button>
                </div>
              </CleanCard>
            ))
          )}
        </div>
      )}

      {/* Channel Form Modal */}
      {showChannelForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="fixed inset-0 bg-black/40" onClick={() => setShowChannelForm(false)} />
          <div className="relative bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
            <h3 className="text-lg font-semibold mb-4">{editingChannel ? '编辑渠道' : '添加渠道'}</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">名称</label>
                <input
                  value={channelForm.name}
                  onChange={(e) => setChannelForm({ ...channelForm, name: e.target.value })}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                  placeholder="渠道名称"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">类型</label>
                <select
                  value={channelForm.type}
                  onChange={(e) => setChannelForm({ ...channelForm, type: e.target.value })}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                >
                  <option value="WEBHOOK">Webhook</option>
                  <option value="DINGTALK">钉钉</option>
                  <option value="EMAIL">邮件</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {channelForm.type === 'EMAIL' ? '收件人邮箱' : 'Webhook URL'}
                </label>
                <input
                  value={channelForm.url}
                  onChange={(e) => setChannelForm({ ...channelForm, url: e.target.value })}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
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
              <button onClick={() => setShowChannelForm(false)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg">取消</button>
              <button
                onClick={handleSaveChannel}
                disabled={actionLoading || !channelForm.name}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {actionLoading ? <Loader2 size={16} className="animate-spin" /> : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rule Form Modal */}
      {showRuleForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="fixed inset-0 bg-black/40" onClick={() => setShowRuleForm(false)} />
          <div className="relative bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
            <h3 className="text-lg font-semibold mb-4">{editingRule ? '编辑规则' : '添加规则'}</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">名称</label>
                <input
                  value={ruleForm.name}
                  onChange={(e) => setRuleForm({ ...ruleForm, name: e.target.value })}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                  placeholder="规则名称"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">事件类型</label>
                <select
                  value={ruleForm.event_type}
                  onChange={(e) => setRuleForm({ ...ruleForm, event_type: e.target.value })}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                >
                  {Object.entries(EVENT_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">通知渠道</label>
                <select
                  value={ruleForm.channel_id}
                  onChange={(e) => setRuleForm({ ...ruleForm, channel_id: Number(e.target.value) })}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
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
              <button onClick={() => setShowRuleForm(false)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg">取消</button>
              <button
                onClick={handleSaveRule}
                disabled={actionLoading || !ruleForm.name || !ruleForm.channel_id}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {actionLoading ? <Loader2 size={16} className="animate-spin" /> : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
