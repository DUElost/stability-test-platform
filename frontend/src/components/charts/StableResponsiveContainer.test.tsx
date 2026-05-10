import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StableResponsiveContainer } from './StableResponsiveContainer';

describe('StableResponsiveContainer', () => {
  it('does not render children before positive size is observed', () => {
    render(
      <StableResponsiveContainer>
        <div data-testid="chart-body" />
      </StableResponsiveContainer>
    );

    // In jsdom, offsetWidth/offsetHeight are 0 by default,
    // so children should NOT be rendered.
    expect(screen.queryByTestId('chart-body')).not.toBeInTheDocument();
  });
});
