import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PageContainer } from './PageContainer';

describe('PageContainer', () => {
  it('renders children with default padding', () => {
    render(<PageContainer>content</PageContainer>);
    expect(screen.getByText('content')).toBeInTheDocument();
  });

  it('fullBleed removes horizontal padding', () => {
    const { container } = render(<PageContainer fullBleed>content</PageContainer>);
    const root = container.firstChild as HTMLElement;
    expect(root.className).not.toContain('px-');
    expect(root.className).toContain('h-full');
  });

  it('scrollable=false removes overflow-auto', () => {
    const { container } = render(<PageContainer scrollable={false}>content</PageContainer>);
    const root = container.firstChild as HTMLElement;
    expect(root.className).not.toContain('overflow-auto');
  });
});
