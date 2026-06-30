import type { SketchLogCounter } from './types';

export function counterToNumber(value: SketchLogCounter): number {
  const numeric = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : 0;
}
