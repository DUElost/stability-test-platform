import React from 'react';
import { SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

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
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className={cn('flex flex-col items-center justify-center min-h-screen p-8', SURFACE.page)}>
          <div className="bg-card rounded-xl border border-border shadow-sm p-8 max-w-md w-full text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-destructive/10 flex items-center justify-center">
              <svg className="w-8 h-8 text-destructive" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h2 className={cn('text-xl font-semibold mb-2', TEXT.heading)}>页面出错了</h2>
            <p className={cn('text-sm mb-6', TEXT.subtitle)}>
              抱歉，页面遇到了意外错误。请尝试刷新页面。
            </p>
            {this.state.error && (
              <pre className={cn('text-xs text-left rounded-lg p-3 mb-4 overflow-auto max-h-32 text-destructive border border-border', SURFACE.subtle)}>
                {this.state.error.message}
              </pre>
            )}
            <Button onClick={this.handleReload}>刷新页面</Button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
