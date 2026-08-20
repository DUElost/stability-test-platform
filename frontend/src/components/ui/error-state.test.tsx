import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { InlineError } from './error-state';

describe('InlineError', () => {
  it('无 onRetry 时只渲染文案', () => {
    render(<InlineError message="加载失败" />);
    expect(screen.getByText('加载失败')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重试/ })).not.toBeInTheDocument();
  });

  it('onRetry 渲染重试按钮并回调', () => {
    const onRetry = vi.fn();
    render(<InlineError message="加载失败" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
