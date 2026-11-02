/** 列上的像素宽度只表示相对权重。换成占表宽的百分比后，各列比例不随窗口变化。没写宽度的列按 160 计入。 */
export function fitColumns<T extends { width?: number | string }>(cols: readonly T[]): T[] {
  const weight = (width: number | string | undefined) => {
    if (typeof width === 'number' && width > 0) return width;
    if (typeof width === 'string' && width.endsWith('%')) return Number.parseFloat(width) || 160;
    return 160;
  };
  const weights = cols.map((col) => weight(col.width));
  const sum = weights.reduce((total, item) => total + item, 0) || 1;
  let used = 0;
  return cols.map((col, index) => {
    const pct = index === cols.length - 1
      ? Math.round((100 - used) * 10) / 10
      : Math.round((weights[index] / sum) * 1000) / 10;
    used += pct;
    return { ...col, width: `${pct}%` };
  });
}
