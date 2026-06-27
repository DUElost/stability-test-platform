import { useEffect } from 'react';

export function useDocumentTitle(title: string) {
  useEffect(() => {
    const original = document.title;
    document.title = title ? `${title} | STP` : 'STP';
    return () => {
      document.title = original;
    };
  }, [title]);
}
