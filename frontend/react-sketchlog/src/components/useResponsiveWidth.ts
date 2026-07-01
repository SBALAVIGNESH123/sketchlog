import { useEffect, useState } from 'react';
import type { RefObject } from 'react';

export function useResponsiveWidth(
  elementRef: RefObject<SVGSVGElement | null>,
  fallbackWidth: number,
): number {
  const [width, setWidth] = useState(fallbackWidth);

  useEffect(() => {
    const element = elementRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return;

    const updateWidth = (measuredWidth: number) => {
      const roundedWidth = Math.round(measuredWidth);
      if (roundedWidth > 0) setWidth(roundedWidth);
    };
    updateWidth(element.getBoundingClientRect().width);

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) updateWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [elementRef]);

  return width;
}
