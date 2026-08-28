import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, Loader2, Wrench } from 'lucide-react';
import type { AiChatMessage } from '@/utils/api/types';
import { BORDER, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

/**
 * 单条消息气泡。markdown 渲染基座：**禁 rehype-raw**（防 HTML 注入，ADR-0031 D7），
 * 链接一律新窗口打开且不带 referrer。
 */
export function MessageBubble({ message }: { message: AiChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className={cn(
            'max-w-[80%] rounded-2xl rounded-br-sm px-4 py-2.5 text-sm whitespace-pre-wrap break-words',
            'bg-primary text-primary-foreground',
          )}
        >
          {message.content}
        </div>
      </div>
    );
  }

  if (message.role === 'tool') {
    return (
      <div className="flex">
        <div
          className={cn(
            'max-w-[90%] rounded-lg px-3 py-1.5 text-xs text-muted-foreground',
            SURFACE.subtle,
            BORDER.subtle,
            'border',
          )}
        >
          <span className="inline-flex items-center gap-1.5">
            <Wrench className="h-3 w-3 shrink-0" />
            <span className="break-all">{message.content}</span>
          </span>
        </div>
      </div>
    );
  }

  // assistant
  return (
    <div className="flex flex-col items-start gap-1.5">
      {message.tool_calls.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {message.tool_calls.map((call) => (
            <span
              key={call.id}
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs',
                SURFACE.subtle,
                TEXT.caption,
              )}
            >
              <Wrench className="h-3 w-3" />
              {call.name}
            </span>
          ))}
        </div>
      )}
      {message.status === 'pending' || message.status === 'running' ? (
        <div
          className={cn(
            'inline-flex items-center gap-2 rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm',
            SURFACE.elevated,
            BORDER.default,
            'border',
            TEXT.subtitle,
          )}
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          思考中…
        </div>
      ) : message.status === 'failed' ? (
        <div
          className={cn(
            'inline-flex max-w-[90%] items-start gap-2 rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm',
            'bg-destructive/10 text-destructive',
          )}
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="break-words">
            {message.meta.error || '本轮处理失败，请重试或调整问法。'}
          </span>
        </div>
      ) : (
        <div
          className={cn(
            'max-w-[90%] rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm',
            SURFACE.elevated,
            BORDER.default,
            'border',
            // 手动排版（无 typography 插件）；子元素样式收口在这里
            '[&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0',
            '[&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5',
            '[&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-medium',
            '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2',
            '[&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3',
            '[&_table]:my-2 [&_table]:w-full [&_th]:border [&_th]:px-2 [&_th]:py-1 [&_td]:border [&_td]:px-2 [&_td]:py-1',
            '[&_hr]:my-3 [&_hr]:border-border',
            TEXT.body,
          )}
        >
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // 剔除 react-markdown 注入的 node prop，避免透传到 DOM
              a: ({ node: _node, ...props }) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
              pre: ({ node: _node, ...props }) => (
                <pre
                  className="my-2 overflow-x-auto rounded-md bg-muted/60 p-3 font-mono text-xs"
                  {...props}
                />
              ),
              code: ({ node: _node, ...props }) => <code className="font-mono text-xs" {...props} />,
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}

export default MessageBubble;
