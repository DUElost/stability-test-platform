import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { SURFACE } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { ErrorState } from '@/components/ui/error-state';
import { clearChunkRecoveryAttempt, isChunkLoadError } from '@/utils/chunkLoadRecovery';

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  handleReload = () => {
    const isChunkFailure = isChunkLoadError(this.state.error);
    if (isChunkFailure) {
      clearChunkRecoveryAttempt();
    }
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    const isChunkFailure = isChunkLoadError(this.state.error);
    if (this.state.hasError) {
      return (
        <div className={cn('flex items-center justify-center min-h-screen p-8', SURFACE.page)}>
          <div className="w-full max-w-md">
            <ErrorState
              icon={<AlertTriangle className="w-8 h-8 text-destructive" />}
              title={isChunkFailure ? '页面需要刷新以加载最新版本' : '页面出错了'}
              description={
                isChunkFailure
                  ? '检测到当前页面所需资源已被新版本替换。请点击下方按钮刷新，加载完成后即可正常访问。'
                  : '抱歉，页面遇到了意外错误。请尝试刷新页面。'
              }
              action={
                <div className="space-y-3">
                  {this.state.error && (
                    <pre className={cn('text-xs text-left rounded-lg p-3 overflow-auto max-h-32 text-destructive border border-border', SURFACE.subtle)}>
                      {this.state.error.message}
                    </pre>
                  )}
                  <Button onClick={this.handleReload}>刷新页面</Button>
                </div>
              }
            />
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
