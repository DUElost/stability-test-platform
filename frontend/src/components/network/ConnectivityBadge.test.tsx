import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ConnectivityBadge } from './ConnectivityBadge';

describe('ConnectivityBadge', () => {
  it('renders online status correctly', () => {
    render(<ConnectivityBadge status="online" />);
    const badge = screen.getByText(/online/i);
    expect(badge).toBeInTheDocument();
    expect(badge.parentElement).toHaveClass('text-green-700');
  });

  it('renders offline status correctly', () => {
    render(<ConnectivityBadge status="offline" />);
    const badge = screen.getByText(/offline/i);
    expect(badge).toBeInTheDocument();
    expect(badge.parentElement).toHaveClass('text-red-700');
  });

  it('renders warning status correctly', () => {
    render(<ConnectivityBadge status="warning" />);
    const badge = screen.getByText(/warning/i);
    expect(badge).toBeInTheDocument();
    expect(badge.parentElement).toHaveClass('text-yellow-700');
  });

  it('displays latency when provided', () => {
    render(<ConnectivityBadge status="online" latency={45} />);
    expect(screen.getByText(/\(45ms\)/)).toBeInTheDocument();
  });

  it('does not display latency when not provided', () => {
    render(<ConnectivityBadge status="online" />);
    expect(screen.queryByText(/ms\)/)).not.toBeInTheDocument();
  });

  it('applies capitalization to status text', () => {
    render(<ConnectivityBadge status="online" />);
    const statusText = screen.getByText(/online/i);
    expect(statusText).toHaveClass('capitalize');
  });
});
