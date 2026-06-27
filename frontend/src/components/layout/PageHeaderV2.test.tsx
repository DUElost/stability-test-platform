import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { PageHeaderV2 } from './PageHeaderV2';

describe('PageHeaderV2', () => {
  it('renders title and actions', () => {
    render(
      <MemoryRouter>
        <PageHeaderV2 title="Plans" actions={<button>Create</button>} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Plans' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument();
  });

  it('renders breadcrumbs', () => {
    render(
      <MemoryRouter>
        <PageHeaderV2
          title="Edit Plan"
          breadcrumbs={[{ label: 'Plans', path: '/plans' }, { label: 'Edit' }]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Plans' })).toHaveAttribute('href', '/plans');
    expect(screen.getByText('Edit')).toBeInTheDocument();
  });
});
