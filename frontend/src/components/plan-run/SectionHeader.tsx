import type { ReactNode } from 'react';

interface Props {
  title: string;
  meta?: string;
  extra?: ReactNode;
  /** Optional inline content rendered after meta, before the right-aligned extra. */
  children?: ReactNode;
  /** 色条颜色，默认蓝色 */
  color?: 'blue' | 'red' | 'green' | 'amber' | 'gray';
}

const COLOR_CLS: Record<NonNullable<Props['color']>, string> = {
  blue:  'from-blue-600 to-blue-400',
  red:   'from-red-500 to-red-400',
  green: 'from-green-600 to-green-400',
  amber: 'from-amber-500 to-amber-400',
  gray:  'from-gray-400 to-gray-300',
};

export default function SectionHeader({ title, meta, extra, children, color = 'blue' }: Props) {
  return (
    <div className="mx-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <span className={`h-4 w-1 rounded-full bg-gradient-to-b ${COLOR_CLS[color]}`} />
      <span className="text-sm font-bold text-gray-800">{title}</span>
      {meta && <span className="text-xs text-gray-400">{meta}</span>}
      {children}
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  );
}
