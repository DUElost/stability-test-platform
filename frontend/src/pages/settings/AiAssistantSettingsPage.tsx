import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, PlugZap, Save } from 'lucide-react';
import { api, toApiError } from '@/utils/api';
import type { AiAssistantConfig, AiAssistantConfigUpdate } from '@/utils/api/types';
import { aiAssistantKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { FORM, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

/**
 * 仅 T2 级低危工具可入免确认白名单（与后端工具注册表校验对齐；
 * scan_script_catalog / reload_agent_config 恒需审批，不在选项内）。
 */
const WHITELISTABLE_TOOLS: ReadonlyArray<{ name: string; label: string }> = [
  { name: 'test_notification_channel', label: '通知通道测试发送' },
];

interface FormState {
  base_url: string;
  model: string;
  api_key: string;
  enabled: boolean;
  temperature: number;
  max_turns: number;
  request_timeout_seconds: number;
  t1_require_confirm: boolean;
  auto_approve_tools: string[];
}

function toFormState(config: AiAssistantConfig): FormState {
  return {
    base_url: config.base_url,
    model: config.model,
    api_key: '',
    enabled: config.enabled,
    temperature: config.temperature,
    max_turns: config.max_turns,
    request_timeout_seconds: config.request_timeout_seconds,
    t1_require_confirm: config.t1_require_confirm,
    auto_approve_tools: [...config.auto_approve_tools],
  };
}

export default function AiAssistantSettingsPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const configQ = useQuery({
    queryKey: aiAssistantKeys.config(),
    queryFn: api.aiAssistant.getConfig,
  });

  // 载入后一次性回填表单；此后编辑以本地 state 为准（不在输入时被轮询覆盖）。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (configQ.data && form == null) {
      setForm(toFormState(configQ.data));
    }
  }, [configQ.data, form]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const saveMutation = useMutation({
    mutationFn: (payload: AiAssistantConfigUpdate) => api.aiAssistant.updateConfig(payload),
    onSuccess: () => {
      toast.success('AI 助手配置已保存');
      qc.invalidateQueries({ queryKey: aiAssistantKeys.config() });
    },
    onError: (err) => toast.error(toApiError(err).message || '保存失败'),
  });

  const testMutation = useMutation({
    mutationFn: () => api.aiAssistant.testConnection(),
    onSuccess: (result) => {
      if (result.ok) {
        setTestResult({ ok: true, text: `连接成功（${result.latency_ms ?? '?'} ms，模型 ${result.model}）` });
        toast.success('LLM 连接成功');
      } else {
        setTestResult({ ok: false, text: `连接失败：${result.error ?? '未知错误'}` });
      }
    },
    onError: (err) => {
      setTestResult({ ok: false, text: `连接失败：${toApiError(err).message}` });
    },
  });

  const handleSave = () => {
    if (!form) return;
    if (!form.base_url.trim() || !form.model.trim()) {
      toast.error('API URL 与模型为必填项');
      return;
    }
    const payload: AiAssistantConfigUpdate = {
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      enabled: form.enabled,
      temperature: form.temperature,
      max_turns: form.max_turns,
      request_timeout_seconds: form.request_timeout_seconds,
      t1_require_confirm: form.t1_require_confirm,
      auto_approve_tools: form.auto_approve_tools,
    };
    // api_key 留空 = 不变更（不上送字段）。
    if (form.api_key.trim()) {
      payload.api_key = form.api_key.trim();
    }
    saveMutation.mutate(payload);
  };

  if (configQ.isLoading) {
    return (
      <PageContainer>
        <PageHeader title="AI 助手设置" subtitle="配置 OpenAI 兼容的 LLM 接入与自治边界" />
        <PageSkeleton>
          <PageSkeleton.Block size="lg" />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
      </PageContainer>
    );
  }

  if (configQ.isError || !form) {
    return (
      <PageContainer>
        <PageHeader title="AI 助手设置" subtitle="配置 OpenAI 兼容的 LLM 接入与自治边界" />
        <ErrorState
          title="配置加载失败"
          description={configQ.isError ? toApiError(configQ.error).message : '配置未就绪'}
          onRetry={() => configQ.refetch()}
        />
      </PageContainer>
    );
  }

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const toggleWhitelist = (tool: string) =>
    setForm((prev) => {
      if (!prev) return prev;
      const has = prev.auto_approve_tools.includes(tool);
      return {
        ...prev,
        auto_approve_tools: has
          ? prev.auto_approve_tools.filter((t) => t !== tool)
          : [...prev.auto_approve_tools, tool],
      };
    });

  return (
    <PageContainer>
      <PageHeader title="AI 助手设置" subtitle="配置 OpenAI 兼容的 LLM 接入与自治边界（变更写入审计）" />

      <div className="max-w-3xl space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">LLM 接入</CardTitle>
            <CardDescription>
              OpenAI 兼容的 /chat/completions 端点，需支持 function calling。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className={FORM.label} htmlFor="ai-base-url">
                API URL
              </label>
              <input
                id="ai-base-url"
                value={form.base_url}
                onChange={(e) => update('base_url', e.target.value)}
                placeholder="https://api.deepseek.com/v1"
                className={FORM.input}
              />
              <p className={FORM.hint}>填到版本根即可（含或不含 /v1 均可，自动拼接 /chat/completions）。</p>
            </div>

            <div>
              <label className={FORM.label} htmlFor="ai-api-key">
                API Key
              </label>
              <input
                id="ai-api-key"
                type="password"
                value={form.api_key}
                onChange={(e) => update('api_key', e.target.value)}
                placeholder={
                  configQ.data?.api_key_masked
                    ? `已配置（${configQ.data.api_key_masked}），留空则不变更`
                    : '尚未配置，输入新 Key'
                }
                className={FORM.input}
                autoComplete="new-password"
              />
              <p className={FORM.hint}>Key 加密存储；保存后不可再查看明文。</p>
            </div>

            <div>
              <label className={FORM.label} htmlFor="ai-model">
                模型
              </label>
              <input
                id="ai-model"
                value={form.model}
                onChange={(e) => update('model', e.target.value)}
                placeholder="例如 deepseek-chat / glm-4-plus"
                className={FORM.input}
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="ai-enabled"
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => update('enabled', e.target.checked)}
                className="h-4 w-4"
              />
              <label htmlFor="ai-enabled" className={cn('text-sm', TEXT.body)}>
                启用 AI 助手（关闭后所有用户不可用，配置保留）
              </label>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">自治边界（ADR-0031 D1）</CardTitle>
            <CardDescription>
              T0 观测诊断恒自动；T1 测试门禁默认自动执行；T2 运维动作默认需 admin 审批。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2">
              <input
                id="ai-t1-confirm"
                type="checkbox"
                checked={form.t1_require_confirm}
                onChange={(e) => update('t1_require_confirm', e.target.checked)}
                className="h-4 w-4"
              />
              <label htmlFor="ai-t1-confirm" className={cn('text-sm', TEXT.body)}>
                收回 T1 自动执行（测试与门禁改为逐次审批）
              </label>
            </div>

            <div>
              <p className={FORM.label}>免确认白名单（仅低危运维动作）</p>
              {WHITELISTABLE_TOOLS.map((tool) => (
                <div key={tool.name} className="mt-1 flex items-center gap-2">
                  <input
                    id={`ai-wl-${tool.name}`}
                    type="checkbox"
                    checked={form.auto_approve_tools.includes(tool.name)}
                    onChange={() => toggleWhitelist(tool.name)}
                    className="h-4 w-4"
                  />
                  <label htmlFor={`ai-wl-${tool.name}`} className={cn('text-sm', TEXT.body)}>
                    {tool.label}
                    <span className={cn('ml-1 font-mono text-xs', TEXT.caption)}>{tool.name}</span>
                  </label>
                </div>
              ))}
              <p className={FORM.hint}>
                hot-update、生产库写操作、任意 shell 为硬排除（T3），不提供配置入口。
              </p>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className={FORM.label} htmlFor="ai-temperature">
                  temperature
                </label>
                <input
                  id="ai-temperature"
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={form.temperature}
                  onChange={(e) => update('temperature', Number(e.target.value))}
                  className={FORM.input}
                />
              </div>
              <div>
                <label className={FORM.label} htmlFor="ai-max-turns">
                  单轮工具迭代上限
                </label>
                <input
                  id="ai-max-turns"
                  type="number"
                  min="1"
                  max="20"
                  value={form.max_turns}
                  onChange={(e) => update('max_turns', Number(e.target.value))}
                  className={FORM.input}
                />
              </div>
              <div>
                <label className={FORM.label} htmlFor="ai-timeout">
                  请求超时（秒）
                </label>
                <input
                  id="ai-timeout"
                  type="number"
                  min="10"
                  max="600"
                  value={form.request_timeout_seconds}
                  onChange={(e) => update('request_timeout_seconds', Number(e.target.value))}
                  className={FORM.input}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="flex items-center gap-2">
          <Button onClick={handleSave} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-1 h-4 w-4" />
            )}
            保存配置
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setTestResult(null);
              testMutation.mutate();
            }}
            disabled={testMutation.isPending}
          >
            {testMutation.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <PlugZap className="mr-1 h-4 w-4" />
            )}
            测试连接
          </Button>
          {testResult && (
            <span
              className={cn('text-sm', testResult.ok ? 'text-success' : 'text-destructive')}
              role="status"
            >
              {testResult.text}
            </span>
          )}
        </div>
      </div>
    </PageContainer>
  );
}
