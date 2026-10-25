/**
 * PDE工程师负责页面用到的抽屉对象（?drawer=<kind>:<id>），由 objectRegistry 的 RESOLVERS 展开合入。
 *   object-type:<key>   对象类型（数据质量页“失败检查”打开，不跳转页面）
 */
import type { ObjView } from './objectRegistry';
import { fmtInt } from '@/mock/format';
import { QUALITY_CHECKS, qObjType } from '@/mock/quality';

export const PDE_RESOLVERS: Record<string, (id: string) => ObjView | null> = {
  'object-type'(id) {
    const t = qObjType(id);
    if (!t) return null;
    const checks = QUALITY_CHECKS.filter((c) => c.typeKey === t.key);
    const failed = checks.filter((c) => c.result === '失败');
    return {
      kind: 'object-type', kindLabel: '对象类型', id: t.key, title: t.name, code: t.api,
      status: failed.length ? { text: `质量检查失败 ${failed.length} 项`, color: 'error' } : { text: '质量检查通过', color: 'success' },
      brief: [
        { label: '分组', value: t.group }, { label: '对象数', value: fmtInt(t.count) },
        { label: '数据源', value: t.source }, { label: '数据截至', value: t.cutoff },
      ],
      fields: [
        { label: 'API 名称', value: t.api }, { label: '分组', value: t.group }, { label: '主键', value: t.pk },
        { label: '对象数', value: fmtInt(t.count) }, { label: '数据源', value: t.source }, { label: '负责人', value: t.owner },
        { label: '数据截至', value: t.cutoff }, { label: '质量检查', value: `${checks.length} 项，失败 ${failed.length} 项` },
      ],
      timeline: checks.map((c) => ({
        time: c.lastRun, text: `${c.kind}：${c.result}`, desc: `${c.rule} · 实际 ${c.actual}${c.failSince ? ` · 自 ${c.failSince} 起失败` : ''}`,
        status: c.result === '失败' ? 'error' as const : 'done' as const,
      })),
      tabs: [{
        key: 'quality', title: '数据质量',
        fields: checks.map((c) => ({ label: c.kind, value: `${c.result} · ${c.actual}（阈值 ${c.threshold}）` })),
      }],
    };
  },
};
