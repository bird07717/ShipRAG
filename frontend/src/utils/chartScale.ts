// Aim for about five axis intervals so the tallest bar fills most of the plot.
const TARGET_INTERVALS = 5;

/** Smallest 1/2/(2.5)/5 × 10ⁿ step ≥ raw; 2.5 only where it stays an integer. */
export function niceStep(raw: number): number {
  if (raw <= 1) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const residual = raw / magnitude;
  const factors = magnitude >= 10 ? [1, 2, 2.5, 5, 10] : [1, 2, 5, 10];
  return factors.find((factor) => residual <= factor)! * magnitude;
}

/** Axis ceiling: whole steps just above the maximum (at least one step). */
export function niceAxis(max: number): { step: number; top: number } {
  const step = niceStep(max / TARGET_INTERVALS);
  return { step, top: Math.max(Math.ceil(max / step), 1) * step };
}
