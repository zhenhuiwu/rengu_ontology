/**
 * 已发布 / 草稿 两种视图（对象类型、指标口径、动作类型、事件类型、规则、关系类型、维度与码表、业务知识共用）。
 * 用法：useDraftView('rule', () => cur.value.id)；draftOf('rule', 'R-007') 取规则所在草稿单。
 *       useDraftView('link', () => cur.value.id)；draftOf('link', 'member_holds_cards') 取关系所在草稿单；
 *       新建的关系 / 知识条目用 start({ ...opts, target: newId }) 登记到所选变更单（传 target 时不切视图，由页面自己跳转）。
 *       useDraftView('dimension', () => cur.value.id)；维度和码表共用 'dimension'，id 不重复（如 format、order_status）。
 *       useDraftView('knowledge', () => cur.value.id)；draftOf('knowledge', 'K-018') 取业务知识条目所在草稿单。
 * 规则：已发布版只读；改定义只有“发起变更”一个入口，进入变更草稿后编辑按钮才可用；
 * 有草稿的对象默认看已发布版（问数用的就是这一版），?view=draft 切到草稿。
 */
import { computed, reactive } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { DRAFTS, DRAFT_TARGETS, type DraftCR, type TargetKind } from '@/mock/releases';
import { CHANGE_REQUESTS, CURRENT_USER } from '@/mock/changes';
import { useReadonly } from '@/composables/useReadonly';

const pad = (n: number) => String(n).padStart(2, '0');
const nowText = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

export type { TargetKind };

/** 每张草稿改了哪些定义：数据在 mock/releases.ts 的 DRAFT_TARGETS，这里复制一份，本会话新建/加入的登记不改 mock */
const TARGETS = reactive<Record<string, Partial<Record<TargetKind, string[]>>>>(
  Object.fromEntries(Object.entries(DRAFT_TARGETS).map(([cr, t]) => [cr, Object.fromEntries(Object.entries(t).map(([k, ids]) => [k, [...ids]]))])),
);
const created = reactive<DraftCR[]>([]);
export const allDrafts = () => [...created, ...DRAFTS];

export function draftOf(kind: TargetKind, id: string): DraftCR | undefined {
  const cr = Object.entries(TARGETS).find(([, t]) => t[kind]?.includes(id))?.[0];
  return allDrafts().find((d) => d.id === cr);
}

/** 新单号 = 变更与发布、草稿、本会话新建的单子里最大单号 + 1，谁加单都不会撞号。 */
function nextCrId() {
  const nums = [...CHANGE_REQUESTS, ...DRAFTS, ...created].map((c) => Number(String(c.id).replace(/\D/g, '')) || 0);
  return `CR-${String(Math.max(0, ...nums) + 1).padStart(4, '0')}`;
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

  /**
   * 新建变更单或加入已有变更单，然后切到草稿视图；返回变更单号。
   * target：登记的目标（默认当前选中项）；新建的对象传新 id，这时不切视图，由页面自己跳转。
   */
  function start(opts: { join?: string; title?: string; target?: string }) {
    let id = opts.join;
    if (!id) {
      id = nextCrId();
      created.unshift({ id, title: opts.title || '未命名变更', owner: CURRENT_USER, updatedAt: nowText(), changes: 0 });
    }
    const tid = opts.target ?? targetId();
    const t = (TARGETS[id] ??= {});
    (t[kind] ??= []).includes(tid) || t[kind]!.push(tid);
    if (!opts.target) setView('draft');
    return id;
  }

  /**
   * 撤销 start() 的登记（落库失败时用）：把 target 从变更单里拿掉；本会话新建且已无任何登记的单子一并删掉；切回已发布视图。
   */
  function cancel(cr: string, target?: string) {
    const tid = target ?? targetId();
    const t = TARGETS[cr];
    if (t?.[kind]) {
      t[kind] = t[kind]!.filter((x) => x !== tid);
      if (!t[kind]!.length) delete t[kind];
    }
    const i = created.findIndex((d) => d.id === cr);
    if (i >= 0 && !Object.keys(TARGETS[cr] ?? {}).length) {
      created.splice(i, 1);
      delete TARGETS[cr];
    }
    if (!target) setView('published');
  }
  return { draft, isDraft, canEdit, editTip, readonly, reason, setView, start, cancel };
}
