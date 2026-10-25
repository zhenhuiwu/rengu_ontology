/**
 * 对象类型在变更单流程上线（v2026.09.3）之前的修订记录（示例数据，固定种子）。
 * 对象类型页 → 使用与治理 → 版本历史 在变更单之后列出这些记录；它们没有变更单，不能跳转。
 */
import { seeded } from './format';

export interface LegacyRev { version: string; title: string; author: string; time: string }

const AUTHORS = ['王敏', '陈立军', '刘婷', '周晓峰', '孙浩然'];
const VERS = ['v2026.09.2', 'v2026.09.1', 'v2026.08.4', 'v2026.08.3', 'v2026.08.2', 'v2026.08.1', 'v2026.07.3', 'v2026.07.2', 'v2026.07.1', 'v2026.06.2', 'v2026.06.1'];
const DATES = ['2026-09-12', '2026-09-05', '2026-08-29', '2026-08-22', '2026-08-15', '2026-08-08', '2026-07-25', '2026-07-18', '2026-07-11', '2026-06-27', '2026-06-20'];
const TEMPLATES = [
  (n: string) => `${n}补充属性说明`, (n: string) => `${n}调整属性分组`, (n: string) => `${n}源字段改名同步`,
  (n: string) => `${n}新增维度标记`, (n: string) => `${n}枚举值整理`, (n: string) => `${n}标题属性调整`,
  (n: string) => `${n}主键映射修正`, (n: string) => `${n}同步频率调整`, (n: string) => `${n}敏感字段标记`,
  (n: string) => `${n}描述改写`,
];

/** 某个对象类型的迁移前修订，按时间倒序；最后一条是首次建模 */
export function legacyRevs(objectId: string, objName: string): LegacyRev[] {
  const r = seeded([...objectId].reduce((s, c) => s * 17 + c.charCodeAt(0), 3));
  const n = 4 + Math.floor(r() * 7);
  const out: LegacyRev[] = [];
  for (let i = 0; i < n; i += 1) {
    const last = i === n - 1;
    out.push({
      version: VERS[i], author: AUTHORS[Math.floor(r() * AUTHORS.length)],
      title: last ? `首次建模：从数据源导入${objName}` : TEMPLATES[Math.floor(r() * TEMPLATES.length)](objName),
      time: `${DATES[i]} ${String(19 + Math.floor(r() * 4)).padStart(2, '0')}:${String(Math.floor(r() * 60)).padStart(2, '0')}`,
    });
  }
  return out;
}
