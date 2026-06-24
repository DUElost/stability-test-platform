import type { ReactNode } from 'react';
import { SECTION_ACCENT, SECTION_ACCENT_LEGACY, TEXT } from '@/design-system/tokens';

interface Props {
  title: string;
  meta?: string;
  extra?: ReactNode;
  /** Optional inline content rendered after meta, before the right-aligned extra. */
  children?: ReactNode;
  /** 色条语义色 */
  color?: 'blue' | 'red' | 'green' | 'amber' | 'gray';
}

export default function SectionHeader({ title, meta, extra, children, color = 'blue' }: Props) {
  const accentKey = SECTION_ACCENT_LEGACY[color] ?? 'primary';
  const gradient = SECTION_ACCENT[accentKey];

  return (
    <div className="mx-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <span className={`h-4 w-1 rounded-full bg-gradient-to-b ${gradient}`} />
      <span className={`text-sm font-bold ${TEXT.heading}`}>{title}</span>
      {meta && <span className={`text-xs ${TEXT.caption}`}>{meta}</span>}
      {children}
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  );
}
