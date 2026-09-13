import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PlanSelect } from './PlanSelect';
import type { Plan } from '@/utils/api/types';

function makePlan(id: number, name: string): Plan {
  return {
    id,
    name,
    description: null,
    failure_threshold: 1,
    patrol_interval_seconds: null,
    timeout_seconds: null,
  } as Plan;
}

const PLANS: Plan[] = Array.from({ length: 100 }, (_, i) =>
  makePlan(i + 1, `夜跑计划 ${i + 1}`),
);

describe('PlanSelect（#627）', () => {
  it('100 个 Plan 时按名称搜到目标', () => {
    render(<PlanSelect plans={PLANS} selectedId="" onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /点击选择 Plan/ }));
    fireEvent.change(screen.getByLabelText('搜索 Plan'), { target: { value: '计划 87' } });

    expect(screen.getByRole('button', { name: '选择 Plan 夜跑计划 87' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '选择 Plan 夜跑计划 88' })).not.toBeInTheDocument();
  });

  it('按 ID 搜到目标（含部分数字）', () => {
    render(<PlanSelect plans={PLANS} selectedId="" onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /点击选择 Plan/ }));
    fireEvent.change(screen.getByLabelText('搜索 Plan'), { target: { value: '42' } });

    expect(screen.getByRole('button', { name: '选择 Plan 夜跑计划 42' })).toBeInTheDocument();
    // 名称里不含 42 的都被过滤掉
    expect(screen.queryByRole('button', { name: '选择 Plan 夜跑计划 7' })).not.toBeInTheDocument();
  });

  it('选中后回调字符串 id 并收起列表', () => {
    const onChange = vi.fn();
    render(<PlanSelect plans={PLANS} selectedId="" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /点击选择 Plan/ }));
    fireEvent.change(screen.getByLabelText('搜索 Plan'), { target: { value: '计划 3' } });
    fireEvent.click(screen.getByRole('button', { name: '选择 Plan 夜跑计划 3' }));

    expect(onChange).toHaveBeenCalledWith('3');
    expect(screen.queryByLabelText('搜索 Plan')).not.toBeInTheDocument();
  });

  it('已选 chip 清除后回调空串', () => {
    const onChange = vi.fn();
    render(<PlanSelect plans={PLANS} selectedId="9" onChange={onChange} />);

    expect(screen.getByRole('button', { name: '清除已选 Plan 夜跑计划 9' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清除已选 Plan 夜跑计划 9' }));

    expect(onChange).toHaveBeenCalledWith('');
  });

  it('无匹配与空列表给出不同提示', () => {
    const { unmount } = render(<PlanSelect plans={PLANS} selectedId="" onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /点击选择 Plan/ }));
    fireEvent.change(screen.getByLabelText('搜索 Plan'), { target: { value: '不存在的名字' } });
    expect(screen.getByText('无匹配 Plan')).toBeInTheDocument();
    unmount();

    render(<PlanSelect plans={[]} selectedId="" onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /点击选择 Plan/ }));
    expect(screen.getByText('暂无 Plan')).toBeInTheDocument();
  });

  it('loading 时显示加载态', () => {
    render(<PlanSelect plans={[]} selectedId="" onChange={() => {}} loading />);
    fireEvent.click(screen.getByRole('button', { name: /点击选择 Plan/ }));
    expect(screen.getByText('Plan 列表加载中…')).toBeInTheDocument();
  });
});
