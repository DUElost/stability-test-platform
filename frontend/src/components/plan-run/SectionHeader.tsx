import type { ReactNode } from 'react';

interface Props {
  title: string;
  meta?: string;
  extra?: ReactNode;
  /** Optional inline content rendered after meta, before the right-aligned extra. */
  children?: ReactNode;
}

export default function SectionHeader({ title, meta, extra, children }: Props) {
  return (
    <div className="mx-1 flex items-center gap-2.5">
      <span className="h-3 w-1 rounded-sm bg-gradient-to-b from-blue-600 to-blue-400" />
      <span className="text-xs font-bold uppercase tracking-wider text-gray-700">
        {title}
      </span>
      {meta && <span className="text-xs text-gray-500">{meta}</span>}
      {children}
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  );
}
