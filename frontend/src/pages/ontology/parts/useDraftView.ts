/**
 * 已发布 / 草稿 两种视图（对象类型、指标口径、动作类型、事件类型共用）。
 * 规则：已发布版只读；改定义只有“发起变更”一个入口，进入变更草稿后编辑按钮才可用；
 * 有草稿的对象默认看已发布版（问数用的就是这一版），?view=draft 切到草稿。
 */
import { computed, reactive } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { DRAFTS, type DraftCR } from '@/mock/releases';
import { useReadonly } from '@/composables/useReadonly';

export type TargetKind = 'object' | 'metric' | 'action' | 'event';

/** 每张草稿改了哪些对象 / 指标 / 动作类型 / 事件类型（示例数据） */
const TARGETS = reactive<Record<string, Partial<Record<TargetKind, string[]>>>>({
  'CR-0014': { action: ['adjust_region_budget'] },
  'CR-0013': { event: ['store_price_changed'] },
  'CR-0012': { object: ['member'] },
  'CR-0011': { metric: ['gross_margin'] },
  'CR-0009': { object: ['store'] },
});
const created = reactive<DraftCR[]>([]);
export const allDrafts = () => [...created, ...DRAFTS];

export function draftOf(kind: TargetKind, id: string): DraftCR | undefined {
  const cr = Object.entries(TARGETS).find(([, t]) => t[kind]?.includes(id))?.[0];
  return allDrafts().find((d) => d.id === cr);
}

export function useDraftView(kind: TargetKind, targetId: () => string) {
  const route = useRoute();
  const router = useRouter();
  const draft = computed(() => draftOf(kind, targetId()));
  const { readonly, reason } = useReadonly();
  // 查看历史版本时整页只读：不进草稿，编辑和发起变更都置灰
  const isDraft = computed(() => !readonly.value && !!draft.value && route.query.view === 'draft');
  const canEdit = computed(() => isDraft.value);
  const editTip = computed(() => (readonly.value ? reason.value : isDraft.value ? '' : '已发布版本只读，请先发起变更'));
  const setView = (v: 'published' | 'draft') => router.replace({ query: { ...route.query, view: v === 'draft' ? 'draft' : undefined } });

  /** 新建变更单或加入已有变更单，然后切到草稿视图 */
  function start(opts: { join?: string; title?: string }) {
    let id = opts.join;
    if (!id) {
      id = `CR-${String(15 + created.length).padStart(4, '0')}`;
      created.unshift({ id, title: opts.title || '未命名变更', owner: '王敏', updatedAt: '2026-10-25 16:30', changes: 0 });
    }
    const t = (TARGETS[id] ??= {});
    (t[kind] ??= []).includes(targetId()) || t[kind]!.push(targetId());
    setView('draft');
  }
  return { draft, isDraft, canEdit, editTip, readonly, reason, setView, start };
}
