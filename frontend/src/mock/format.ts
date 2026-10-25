/** 数字格式：与系统导出一致（千分位、两位小数、空值显示 --） */
export const EMPTY = '--';
export const fmtMoney = (n?: number | null, digits = 2) =>
  n == null ? EMPTY : n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const fmtInt = (n?: number | null) => (n == null ? EMPTY : Math.round(n).toLocaleString('zh-CN'));
export const fmtPct = (n?: number | null, digits = 2) => (n == null ? EMPTY : `${(n * 100).toFixed(digits)}%`);
export const fmtDelta = (n?: number | null, digits = 1) =>
  n == null ? EMPTY : `${n > 0 ? '+' : ''}${(n * 100).toFixed(digits)}%`;

/** 确定性伪随机，保证每次截图数据一致 */
export function seeded(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
