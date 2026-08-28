import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Send, Settings } from 'lucide-react';
import { api, toApiError } from '@/utils/api';
import type { AiChatSession } from '@/utils/api/types';
import { aiAssistantKeys } from '@/utils/api/queryKeys';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { BORDER, FORM, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { SessionList } from './components/SessionList';
import { MessageBubble } from './components/MessageBubble';
import { ActionCard } from './components/ActionCard';

/** 未配置错误码（后端 409 ai_not_configured）——命中时切换引导横幅。 */
const NOT_CONFIGURED_CODE = 'ai_not_configured';

export default function AssistantPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const qc = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';

  const [activeId, setActiveId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [showNotConfigured, setShowNotConfigured] = useState(false);

  const sessionsQ = useQuery({
    queryKey: aiAssistantKeys.sessions(),
    queryFn: api.aiAssistant.listSessions,
  });
  const sessions = useMemo(() => sessionsQ.data ?? [], [sessionsQ.data]);

  // admin 可读配置用于渲染「未启用」横幅；普通用户不调 admin 端点，
  // 由发送失败（ai_not_configured）触发横幅。
  const configQ = useQuery({
    queryKey: aiAssistantKeys.config(),
    queryFn: api.aiAssistant.getConfig,
    enabled: isAdmin,
  });

  const messagesQ = useQuery({
    queryKey: aiAssistantKeys.messages(activeId ?? 0),
    queryFn: () => api.aiAssistant.listMessages(activeId as number),
    enabled: activeId != null,
    // ADR-0031 D5：有 pending/running 消息时 2s 轮询，空闲即关。
    refetchInterval: (query) => hasPending(query.state.data) ? 2000 : false,
  });
  const messages = messagesQ.data ?? [];

  const createMutation = useMutation({
    mutationFn: () => api.aiAssistant.createSession(),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: aiAssistantKeys.sessions() });
      setActiveId(session.id);
    },
    onError: (err) => toast.error(toApiError(err).message || '创建会话失败'),
  });

  const deleteMutation = useMutation({
    mutationFn: (session: AiChatSession) => api.aiAssistant.deleteSession(session.id),
    onSuccess: (_data, session) => {
      qc.invalidateQueries({ queryKey: aiAssistantKeys.sessions() });
      if (session.id === activeId) setActiveId(null);
      toast.success('会话已删除');
    },
    onError: (err) => toast.error(toApiError(err).message || '删除会话失败'),
  });

  const sendMutation = useMutation({
    mutationFn: (content: string) => api.aiAssistant.sendMessage(activeId as number, content),
    onSuccess: () => {
      setDraft('');
      qc.invalidateQueries({ queryKey: aiAssistantKeys.messages(activeId!) });
      qc.invalidateQueries({ queryKey: aiAssistantKeys.sessions() });
    },
    onError: (err) => {
      const message = toApiError(err).message || '发送失败';
      if (message.includes(NOT_CONFIGURED_CODE)) {
        setShowNotConfigured(true);
      } else {
        toast.error(message);
      }
    },
  });

  // 自动选中首个会话：条件守卫的一次性 setState，不会循环（对齐 NotificationsPage 先例）。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (activeId == null && sessions.length > 0) {
      setActiveId(sessions[0].id);
    }
  }, [activeId, sessions]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const scrollRef = useRef<HTMLDivElement>(null);
  const messageCount = messages.length;
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messageCount, activeId]);

  const handleDelete = async (session: AiChatSession) => {
    const ok = await confirmDialog({
      title: '删除会话',
      description: `确认删除「${session.title || '未命名会话'}」？消息记录将一并删除。`,
      variant: 'destructive',
      confirmText: '删除',
    });
    if (ok) deleteMutation.mutate(session);
  };

  const submit = () => {
    const content = draft.trim();
    if (!content || activeId == null || sendMutation.isPending) return;
    sendMutation.mutate(content);
  };

  if (sessionsQ.isLoading) {
    return (
      <PageContainer>
        <PageHeader title="AI 助手" subtitle="对话式查询平台状态、运行测试门禁与日常运维" />
        <PageSkeleton>
          <PageSkeleton.List count={2} />
        </PageSkeleton>
      </PageContainer>
    );
  }

  if (sessionsQ.isError) {
    return (
      <PageContainer>
        <PageHeader title="AI 助手" subtitle="对话式查询平台状态、运行测试门禁与日常运维" />
        <ErrorState
          title="会话列表加载失败"
          description={toApiError(sessionsQ.error).message}
          onRetry={() => sessionsQ.refetch()}
        />
      </PageContainer>
    );
  }

  const configDisabled =
    isAdmin && configQ.data && (!configQ.data.enabled || !configQ.data.api_key_masked);
  const showBanner = configDisabled || showNotConfigured;

  return (
    <PageContainer>
      <PageHeader
        title="AI 助手"
        subtitle="对话式查询平台状态、运行测试门禁与日常运维（有副作用的操作需按风险分级审批）"
        action={
          isAdmin ? (
            <Button variant="ghost" size="icon" asChild>
              <Link to="/settings/ai-assistant" aria-label="AI 助手设置">
                <Settings className="h-4 w-4" />
              </Link>
            </Button>
          ) : undefined
        }
      />

      {showBanner && (
        <div
          className={cn(
            'mb-4 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-4 py-2.5 text-sm',
          )}
          role="status"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
          {isAdmin ? (
            <>
              平台 AI 助手尚未启用或未完成配置。
              <Link to="/settings/ai-assistant" className="font-medium text-primary underline underline-offset-2">
                前往设置
              </Link>
            </>
          ) : (
            <>AI 助手尚未启用，请联系管理员在「平台管理」中完成配置。</>
          )}
        </div>
      )}

      <div className="flex h-[calc(100vh-14rem)] min-h-[480px] gap-4">
        <SessionList
          sessions={sessions}
          activeId={activeId}
          loading={sessionsQ.isLoading}
          creating={createMutation.isPending}
          onSelect={setActiveId}
          onCreate={() => createMutation.mutate()}
          onDelete={handleDelete}
          className="hidden md:flex"
        />

        <section
          className={cn(
            'flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl border',
            SURFACE.elevated,
            BORDER.default,
          )}
          aria-label="对话区"
        >
          {activeId == null ? (
            sessions.length === 0 ? (
              <div className="flex flex-1 items-center justify-center p-6">
                <EmptyState
                  title="还没有会话"
                  description="新建一个会话，向助手提问平台状态或让它替你跑测试门禁。"
                  action={
                    <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
                      新建会话
                    </Button>
                  }
                />
              </div>
            ) : (
              <div className="flex flex-1 items-center justify-center">
                <p className={cn('text-sm', TEXT.subtitle)}>从左侧选择一个会话开始对话</p>
              </div>
            )
          ) : (
            <>
              <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
                {messages.map((message) => (
                  <div key={message.id} className="space-y-2">
                    <MessageBubble message={message} />
                    {message.meta.proposed_action_id != null && (
                      <div className="flex">
                        <ActionCard actionId={message.meta.proposed_action_id} />
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className={cn('border-t p-3', BORDER.default)}>
                <div className="flex items-end gap-2">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault();
                        submit();
                      }
                    }}
                    rows={2}
                    placeholder="向助手提问，例如：平台现在健康吗？/ 帮我跑一遍 check:quick 门禁"
                    className={cn(FORM.input, 'resize-none')}
                    aria-label="消息输入框"
                  />
                  <Button
                    onClick={submit}
                    disabled={!draft.trim() || sendMutation.isPending}
                    aria-label="发送"
                  >
                    <Send className="h-4 w-4" />
                  </Button>
                </div>
                <p className={cn('mt-1.5 text-xs', TEXT.caption)}>
                  Enter 发送 · Shift+Enter 换行 · 有副作用的操作会先生成操作卡待审批
                </p>
              </div>
            </>
          )}
        </section>
      </div>
    </PageContainer>
  );
}

function hasPending(messages: Array<{ status: string }> | undefined): boolean {
  return !!messages?.some((m) => m.status === 'pending' || m.status === 'running');
}
