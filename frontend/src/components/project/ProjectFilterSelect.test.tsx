import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ProjectFilterSelect, UNASSIGNED_FILTER_VALUE } from './ProjectFilterSelect';

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      projects: {
        ...actual.api.projects,
        listActive: vi.fn().mockResolvedValue([
          { id: 1, project_key: 'alpha-proj', display_name: 'Alpha 项目' },
          { id: 2, project_key: 'beta-proj', display_name: 'Beta 项目' },
        ]),
      },
    },
  };
});

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('ProjectFilterSelect', () => {
  it('renders SearchableSelect combobox, supports keyword filtering, and emits project_key or undefined', async () => {
    const onChange = vi.fn();
    renderWithQuery(
      <ProjectFilterSelect
        value={undefined}
        onChange={onChange}
        showUnassigned
        testId="test-project-filter"
      />,
    );

    const trigger = screen.getByTestId('test-project-filter');
    expect(trigger).toHaveAttribute('role', 'combobox');
    expect(trigger).toHaveTextContent('全部项目');

    fireEvent.click(trigger);
    expect(await screen.findByRole('option', { name: 'Alpha 项目（alpha-proj）' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '未归属' })).toBeInTheDocument();

    const searchInput = screen.getByPlaceholderText('搜索项目名 / key...');
    fireEvent.change(searchInput, { target: { value: 'beta' } });
    expect(screen.getByRole('option', { name: 'Beta 项目（beta-proj）' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Alpha 项目（alpha-proj）' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: 'Beta 项目（beta-proj）' }));
    expect(onChange).toHaveBeenCalledWith('beta-proj');

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('option', { name: '未归属' }));
    expect(onChange).toHaveBeenCalledWith(UNASSIGNED_FILTER_VALUE);

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('option', { name: '全部项目' }));
    expect(onChange).toHaveBeenCalledWith(undefined);
  });
});
