import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UserTable } from './UserTable';
import type { User } from '@/utils/api';

/**
 * #2358：用户管理页的时间列必须原样展示后端 naive 时间戳（本地墙上时间）。
 *
 * 反例（修复前）：走 `formatDateTimeFull` → 按 UTC 解析再转本地 → 09:30 显示成
 * 17:30（若是东八区），恰好是「未来时间」这种最刺眼的错法。
 */
const user: User = {
  id: 1,
  username: 'alice',
  role: 'admin',
  is_active: 'Y',
  created_at: '2026-08-18T13:48:36',
  last_login: '2026-09-16T12:10:36',
};

describe('UserTable（#2358）', () => {
  it('created_at / last_login 原样展示，不做时区换算', () => {
    render(
      <UserTable
        users={[user]}
        currentUserId={2}
        onEdit={vi.fn()}
        onToggleActive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText('2026-08-18 13:48:36')).toBeInTheDocument();
    expect(screen.getByText('2026-09-16 12:10:36')).toBeInTheDocument();
  });

  it('last_login 为空显示占位符', () => {
    render(
      <UserTable
        users={[{ ...user, last_login: null }]}
        currentUserId={1}
        onEdit={vi.fn()}
        onToggleActive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText('-')).toBeInTheDocument();
  });
});
