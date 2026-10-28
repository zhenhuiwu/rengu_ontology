<script setup lang="ts">
/**
 * 我的页面抽屉（?page_id=P001&ptab=preview|schedule|share）
 *  预览：数据截至、范围、来源、口径版本、卡片预览（simple 分页，每页 6 张）；“调整卡片”模式可拖拽排序、移除卡片
 *  刷新与订阅：刷新计划 + 订阅推送
 *  共享：共享方式、成员及权限、链接、导出
 * 历史版本只读：所有修改禁用并提示原因。
 * 未保存保护（公共 useUnsavedGuard）：卡片调整、刷新与订阅、共享有未保存改动时，关闭抽屉 / 切页签 / 离开页面先提醒。
 * 保存顺序时移除了卡片、保存共享时新增了可见人，走公共确认卡写明“将移除 N 张卡片”/“新增可见人：xx”。
 * 真接口（开关 qa，写操作不看后台是否判定未启动，失败只报错不回退）：保存卡片 P8、刷新与订阅 P9、共享 P10，成功后用后台返回值更新并 emit changed（列表重载）；
 * qa 开关关闭时改本地演示数据；开关打开时后台不可达也只报错。
 */
import { computed, ref, watch } from 'vue';
import { onBeforeRouteUpdate } from 'vue-router';
import { currentApiContext } from '@/api/http';
import { message } from 'ant-design-vue';
import { HolderOutlined, DeleteOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { useConfirmStore } from '@/stores/confirm';
import CardPreview from './CardPreview.vue';
import InfoTip from '../InfoTip.vue';
import * as pagesApi from '@/api/qaPages';
import { qaWriteReal, qaWriteFail } from '../qaFallback';
import { PEOPLE, freqLabel, type QaPageFull, type PageCard, type RefreshPlan, type Subscription, type ShareSetting, type SharePerm } from '@/mock/qa-pages';

const props = defineProps<{ page?: QaPageFull; tab: string; readonly: boolean; reason: string }>();
const emit = defineEmits<{ close: []; 'update:tab': [string]; refresh: [QaPageFull]; rename: [QaPageFull, string]; remove: [QaPageFull]; changed: [QaPageFull] }>();
const saving = ref(false);
const editDisabled = computed(() => props.readonly || saving.value || (qaWriteReal.value && (!props.page || !pagesApi.access(props.page).canEdit)));
const shareDisabled = computed(() => props.readonly || saving.value || (qaWriteReal.value && (!props.page || !pagesApi.access(props.page).canShare)));
const scheduleDisabled = computed(() => props.readonly || saving.value || (qaWriteReal.value && (!props.page || !pagesApi.access(props.page).canSchedule)));
/** 真接口写：开关为假（纯演示）返回 null（走演示逻辑）；开关为真时调真接口，成功返回结果，
 *  失败（含后台未启动）只提示并返回 undefined——写操作不回退、不改演示数据、不触发整页回退 */
async function liveCall<T>(fn: () => Promise<T>, what: string): Promise<T | null | undefined> {
  if (!qaWriteReal.value) return null;
  const context = JSON.stringify(currentApiContext()), id = props.page?.id;
  saving.value = true;
  try { const result = await fn(); return context === JSON.stringify(currentApiContext()) && id === props.page?.id ? result : undefined; }
  catch (e) { if (context === JSON.stringify(currentApiContext()) && id === props.page?.id) qaWriteFail(what, e); return undefined; }
  finally { saving.value = false; }
}

/* 预览 */
const CSIZE = 6;
const cpage = ref(1);
const editing = ref(false);
const draftCards = ref<PageCard[]>([]);
watch(() => props.page?.id, () => { cpage.value = 1; editing.value = false; resetDrafts(); });
const shownCards = computed(() => (editing.value ? draftCards.value : props.page?.cards ?? []));
const pagedCards = computed(() => shownCards.value.slice((cpage.value - 1) * CSIZE, cpage.value * CSIZE));
const calibers = computed(() => {
  const m = new Map<string, PageCard>();
  (props.page?.cards ?? []).forEach((c) => { if (c.metric && c.version && c.version !== '--') m.set(`${c.metric} ${c.version}`, c); });
  return [...m.entries()].map(([label, c]) => ({ label, pending: c.pending }));
});
function startEdit() { draftCards.value = [...(props.page?.cards ?? [])]; editing.value = true; cpage.value = 1; }
const dragFrom = ref<number | null>(null);
function onDrop(to: number) {
  if (dragFrom.value == null || dragFrom.value === to) return;
  const list = [...draftCards.value]; const [c] = list.splice(dragFrom.value, 1); list.splice(to, 0, c); draftCards.value = list; dragFrom.value = null;
}
const removeCard = (i: number) => { draftCards.value = draftCards.value.filter((_, j) => j !== i); };
const confirm = useConfirmStore();
async function saveCards() {
  const pg = props.page; if (!pg) return;
  const removed = pg.cards.filter((c) => !draftCards.value.some((d) => d.key === c.key));
  if (removed.length) {
    const ok = await confirm.ask({
      title: '保存卡片调整', target: pg.name,
      fields: [{ label: '移除', value: removed.map((c) => c.title).join('、') }, { label: '保存后', value: `${draftCards.value.length} 张卡片` }],
      impact: `将移除 ${removed.length} 张卡片，共享和订阅这个页面的人也看不到了；需要时可在问数工作台重新固定。`,
      okText: `移除 ${removed.length} 张并保存`, danger: true,
    });
    if (!ok) return;
  }
  const r = await liveCall(() => pagesApi.saveCardOrder(pg.id, draftCards.value.map((c) => c.key)), '保存卡片');
  if (r === undefined) return;
  pg.cards = r ? r.cards : draftCards.value; editing.value = false;
  if (r) emit('changed', pg);
  message.success(removed.length ? `已保存，移除 ${removed.length} 张，剩 ${pg.cards.length} 张` : `已保存卡片顺序，共 ${pg.cards.length} 张`);
}

/* 刷新与订阅 */
const plan = ref<RefreshPlan>({ freq: '每天', time: '08:00', onFail: '保留上次结果' });
const EMPTY_SUB: Subscription = { on: false, channel: '站内信', receivers: [], when: '每次刷新后', onlyChanged: false };
const sub = ref<Subscription>({ ...EMPTY_SUB, receivers: [] });
const share = ref<ShareSetting>({ mode: '仅自己', members: [], link: false, allowExport: true });
function resetDrafts() {
  if (!props.page) return;
  plan.value = { ...props.page.refresh };
  const storedSub = props.page.sub || EMPTY_SUB;
  sub.value = { ...storedSub, receivers: [...storedSub.receivers] };
  share.value = { ...props.page.share, members: props.page.share.members.map((m) => ({ ...m })) };
}
watch(() => props.page, resetDrafts, { immediate: true });
const TIMES = ['06:00', '07:00', '07:30', '08:00', '09:00', '12:00', '18:00', '22:00'];
const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
watch(() => plan.value.freq, (freq) => {
  if (freq === '每周' && !plan.value.weekday) plan.value.weekday = '周一';
  if (freq === '每月' && !plan.value.monthday) plan.value.monthday = 1;
  if (!['手动', '每小时'].includes(freq) && !/^\d{2}:\d{2}$/.test(plan.value.time)) plan.value.time = '08:00';
});
const subscriptionPeople = computed(() => {
  if (!qaWriteReal.value) return PEOPLE.map((person) => ({ value: person.name, label: `${person.name} · ${person.dept}` }));
  const page = props.page;
  if (!page) return [];
  const names = [page.owner, ...page.share.members.filter((member) => (member as typeof member & { userId?: string }).userId !== pagesApi.access(page).ownerId).map((member) => member.name)];
  return [...new Set(names)].map((name) => ({ value: name, label: name, disabled: names.filter((item) => item === name).length !== 1 }));
});
const memberPage = ref(1), memberQuery = ref(''), memberTotal = ref(0), memberLoading = ref(false), memberError = ref('');
const memberRows = ref<pagesApi.ShareCandidate[]>([]);
const memberCache = new Map<string, pagesApi.ShareCandidate>();
let memberSeq = 0;
watch([() => props.page?.id, memberPage, memberQuery, () => JSON.stringify(currentApiContext())], async () => {
  const seq = ++memberSeq, page = props.page;
  memberRows.value = []; memberError.value = '';
  if (!qaWriteReal.value || !page || !pagesApi.access(page).canShare) return;
  memberLoading.value = true;
  try {
    const result = await pagesApi.shareOptions(page.id, memberPage.value, memberQuery.value);
    if (seq !== memberSeq) return;
    memberRows.value = result.items; memberTotal.value = result.total;
    result.items.forEach((row) => memberCache.set(row.userId, row));
  } catch { if (seq === memberSeq) memberError.value = '共享成员加载失败，请重试'; }
  finally { if (seq === memberSeq) memberLoading.value = false; }
}, { immediate: true });
const people = computed(() => qaWriteReal.value ? memberRows.value.map((row) => ({ value: row.userId, label: `${row.name} · ${row.dept || '未登记部门'}` })) : PEOPLE.map((p) => ({ value: p.name, label: `${p.name} · ${p.dept}` })));
const memberKey = (member: ShareSetting['members'][number]) => (member as typeof member & { userId?: string }).userId || member.name;
const nextRun = computed(() => {
  const p = plan.value;
  if (p.freq === '手动') return '不自动刷新';
  if (qaWriteReal.value) return JSON.stringify(p) === JSON.stringify(props.page?.refresh) ? pagesApi.access(props.page!).nextRun || '未排定' : '保存后按计划排定';
  if (p.freq === '每小时') return '2026-10-25 18:00';
  if (p.freq === '每天') return `2026-10-26 ${p.time}`;
  if (p.freq === '每周') return `${p.weekday ?? '周一'} ${p.time}（2026-10-26 起）`;
  return `2026-11-${String(p.monthday ?? 1).padStart(2, '0')} ${p.time}`;
});
async function saveSchedule() {
  const pg = props.page; if (!pg) return;
  if (scheduleDisabled.value) return;
  if (!qaWriteReal.value && sub.value.on && !sub.value.receivers.length) { message.warning('请至少选择一位接收人'); return; }
  const r = await liveCall(() => pagesApi.saveSchedule(pg.id, plan.value, sub.value), '保存刷新与订阅');
  if (r === undefined) return;
  pg.refresh = r ? r.refresh : { ...plan.value }; pg.sub = r ? r.sub : { ...sub.value, receivers: [...sub.value.receivers] };
  if (r) { Object.assign(pg, { nextRun: r.nextRun === '不自动刷新' ? null : r.nextRun, scheduleTimezone: r.scheduleTimezone, scheduleActive: r.refresh.freq !== '手动', subscriptionActive: r.sub.on }); resetDrafts(); emit('changed', pg); }
  message.success(`已保存：${freqLabel(plan.value)}${sub.value.on ? '，订阅已开启' : '，订阅已关闭'}`);
}

/* 共享 */
const addWho = ref<string[]>([]);
const addPerm = ref<SharePerm>('可查看');
watch([() => props.page?.id, () => JSON.stringify(currentApiContext())], () => { addWho.value = []; memberCache.clear(); memberPage.value = 1; memberQuery.value = ''; });
watch(memberQuery, () => { memberPage.value = 1; });
const candidates = computed(() => people.value.filter((p) => !share.value.members.some((m) => memberKey(m) === p.value)));
function addMembers() {
  if (shareDisabled.value) return;
  if (qaWriteReal.value && addWho.value.some((id) => !memberCache.has(id) || (addPerm.value === '可编辑' && !memberCache.get(id)!.canEdit))) { message.warning('所选成员没有编辑权限，请改为可查看或重新选择'); return; }
  const add = addWho.value.map((n) => qaWriteReal.value ? { userId: n, name: memberCache.get(n)!.name, dept: memberCache.get(n)!.dept ?? '', perm: addPerm.value } : { name: n, dept: PEOPLE.find((p) => p.name === n)?.dept ?? '', perm: addPerm.value });
  share.value.members = [...share.value.members, ...add]; addWho.value = [];
  if (share.value.mode === '仅自己') share.value.mode = '指定成员';
}
const MSIZE = 10;
const mpage = ref(1);
const pagedMembers = computed(() => share.value.members.slice((mpage.value - 1) * MSIZE, mpage.value * MSIZE));
/** 保存后比保存前多出来的可见人：新加的成员，或改成“所在部门” */
function newViewers(): string[] {
  const pg = props.page; if (!pg) return [];
  const s = share.value;
  const out: string[] = [];
  if (s.mode === '所在部门' && pg.share.mode !== '所在部门') out.push(`${qaWriteReal.value ? pagesApi.access(pg).ownerDept || '页面拥有者所在部门' : '华东事业部 / 运营中心'} 全体`);
  if (s.mode === '指定成员') out.push(...s.members.filter((m) => pg.share.mode !== '指定成员' || !pg.share.members.some((x) => x.name === m.name)).map((m) => `${m.name}（${m.perm}）`));
  return out;
}
/** 保存后新扩大的权限：开启链接访问、允许导出 */
function newGrants(): string[] {
  const pg = props.page; if (!pg) return [];
  const s = share.value; const out: string[] = [];
  if (s.mode !== '仅自己' && s.link && !(pg.share.mode !== '仅自己' && pg.share.link)) out.push('开启页面链接：仍须是已授权共享成员或同部门用户');
  if (s.mode !== '仅自己' && s.allowExport && !(pg.share.mode !== '仅自己' && pg.share.allowExport)) out.push('允许导出：可见人能下载卡片明细');
  return out;
}
async function saveShare() {
  if (!props.page || shareDisabled.value) return;
  if (share.value.mode === '指定成员' && !share.value.members.length) { message.warning('指定成员共享时至少添加一位成员'); return; }
  const add = newViewers();
  const grants = newGrants();
  if (add.length || grants.length) {
    const ok = await confirm.ask({
      title: '保存共享设置', target: props.page.name,
      fields: [{ label: '新增可见人', value: add.length ? add.join('、') : '无' }, { label: '链接访问', value: share.value.link ? '开启' : '关闭' }, { label: '允许导出', value: share.value.allowExport ? '允许' : '不允许' }],
      impact: `${[add.length ? `新增可见人：${add.join('、')}` : '', ...grants].filter(Boolean).join('；')}。看到的数据按各自的数据权限裁剪，范围外的卡片显示“无权限”。`,
      okText: '共享',
    });
    if (!ok) return;
  }
  const pg = props.page;
  const r = await liveCall(() => pagesApi.saveShare(pg.id, share.value, pagesApi.access(pg).lockVersion), '保存共享');
  if (r === undefined) return;
  pg.share = r ? { mode: r.mode, members: r.members, link: r.link, allowExport: r.allowExport } : { ...share.value, members: share.value.mode === '仅自己' ? [] : share.value.members.map((m) => ({ ...m })) };
  if (r) { pagesApi.access(pg).lockVersion = r.lockVersion; resetDrafts(); emit('changed', pg); }
  message.success(share.value.mode === '仅自己' ? '已取消共享' : `已共享：${share.value.mode === '所在部门' ? pagesApi.access(pg).ownerDept || '所在部门' : `${pg.share.members.length} 位成员`}`);
}
/* 未保存保护 */
const json = (x: unknown) => JSON.stringify(x);
const cardsDirty = computed(() => editing.value && json(draftCards.value.map((c) => c.key)) !== json((props.page?.cards ?? []).map((c) => c.key)));
const schedDirty = computed(() => !!props.page && (json(plan.value) !== json(props.page.refresh) || json(sub.value) !== json(props.page.sub || EMPTY_SUB)));
const normShare = (x: ShareSetting) => json({ ...x, members: x.mode === '仅自己' ? [] : x.members });
const shareDirty = computed(() => !!props.page && normShare(share.value) !== normShare(props.page.share));
const dirty = computed(() => !props.readonly && (cardsDirty.value || schedDirty.value || shareDirty.value));
const guard = useUnsavedGuard(dirty);
onBeforeRouteUpdate(async (to, from) => to.query.ontology === from.query.ontology && to.query.page_id !== from.query.page_id && dirty.value ? await confirmDiscard() : true);
const cancelEdit = async () => { if (cardsDirty.value && !(await confirmDiscard())) return; editing.value = false; };
const discard = () => { editing.value = false; resetDrafts(); };
const tabV = computed({ get: () => props.tab || 'preview', set: (v: string) => { guard(() => { discard(); emit('update:tab', v); }); } });
const close = () => guard(() => { discard(); emit('close'); });
const copyLink = async () => {
  if (!props.page) return;
  const query = new URLSearchParams({ ontology: currentApiContext().ontologyId || '', page_id: props.page.id });
  try { await navigator.clipboard.writeText(`${location.origin}/use/qa/pages?${query}`); message.success('页面链接已复制'); }
  catch { message.error('浏览器未允许复制，请重试'); }
};
const templateName = ref(''), templateOpen = ref(false);
async function saveTemplate() {
  const page = props.page;
  if (!page || shareDisabled.value || !templateName.value.trim()) return;
  const result = await liveCall(() => pagesApi.saveAsTemplate(page.id, templateName.value.trim()), '保存模板');
  if (!result) return;
  templateOpen.value = false; templateName.value = ''; emit('changed', page); message.success('已保存不含数据的模板');
}
async function download() { if (!props.page || props.readonly) return; try { await pagesApi.exportPage(props.page.id); } catch (error) { message.error(error instanceof Error ? error.message : '导出失败'); } }

/* 重命名 */
const renameOpen = ref(false);
const newName = ref('');
watch(renameOpen, (o) => { if (o) newName.value = props.page?.name ?? ''; });
function doRename() { if (props.page && newName.value.trim()) emit('rename', props.page, newName.value.trim()); }
watch(() => props.page?.name, (name) => { if (name === newName.value.trim()) renameOpen.value = false; });
</script>

<template>
  <a-drawer :open="!!page" :width="760" :title="page?.name" @close="close">
    <template #extra>
      <a-space v-if="page">
        <a-tooltip :title="reason || pagesApi.access(page).refreshUnavailableReason"><a-button size="small" :disabled="editDisabled || (qaWriteReal && !pagesApi.access(page).canRefresh)" @click="emit('refresh', page)">立即刷新</a-button></a-tooltip>
        <a-popover v-model:open="renameOpen" trigger="click" placement="bottomRight">
          <template #content>
            <div class="pd-pop"><a-input v-model:value="newName" :maxlength="30" show-count @press-enter="doRename" /><a-button type="primary" size="small" :disabled="!newName.trim()" @click="doRename">确定</a-button></div>
          </template>
          <a-tooltip :title="reason"><a-button size="small" :disabled="editDisabled">重命名</a-button></a-tooltip>
        </a-popover>
        <a-tooltip :title="reason"><a-button size="small" danger :disabled="shareDisabled" @click="emit('remove', page)">删除</a-button></a-tooltip>
      </a-space>
    </template>
    <div v-if="page" class="pd">
      <div class="pd-meta">
        <div><span class="c-3">数据截至</span> <span class="num c-1">{{ page.dataAsOf || '—' }}</span><span v-if="page.status === '刷新失败'" class="tl-failed"> · 刷新失败</span> <InfoTip v-if="page.statusNote" :title="page.statusNote" /></div>
        <div><span class="c-3">范围</span> <span class="c-1">{{ page.scope }}</span></div>
        <div><span class="c-3">刷新</span> <span class="c-1">{{ freqLabel(page.refresh) }}{{ qaWriteReal && page.refresh.freq !== '手动' && !pagesApi.access(page).scheduleActive ? '（未排定）' : '' }}</span><span class="c-3"> · 最近 {{ page.lastRefresh?.slice(5) ?? '尚未刷新' }}</span></div>
        <div class="pd-ell" :title="page.source"><span class="c-3">来源</span> <span class="c-1">{{ page.source }}</span></div>
        <div><span class="c-3">共享</span> <span class="c-1">{{ page.share.mode === '仅自己' ? '仅自己' : page.share.mode === '所在部门' ? '所在部门' : `${page.share.members.length} 位成员` }}</span></div>
        <div><span class="c-3">订阅</span> <span class="c-1">{{ page.sub?.on ? `${page.sub.channel} · ${page.sub.receivers.length ? `${page.sub.receivers.length} 人` : '页面拥有者'}` : '未开启' }}</span></div>
      </div>

      <a-tabs v-model:active-key="tabV" size="small">
        <a-tab-pane key="preview" tab="预览" />
        <a-tab-pane key="schedule" tab="刷新与订阅" />
        <a-tab-pane key="share" tab="共享" />
      </a-tabs>

      <template v-if="tabV === 'preview'">
        <div class="pd-row">
          <span class="c-3 t-12">口径版本</span>
          <a-tag v-for="c in calibers" :key="c.label" :class="{ 'c-2': c.pending }" :title="c.pending">{{ c.label }}{{ c.pending ? ' · 待更新' : '' }}</a-tag>
          <span v-if="!calibers.length" class="t-12 c-3">未记录指标口径版本</span>
          <span class="pd-sp" />
          <template v-if="editing">
            <a-button size="small" @click="cancelEdit">取消</a-button>
            <a-button size="small" type="primary" @click="saveCards">保存顺序</a-button>
          </template>
          <a-tooltip v-else :title="reason"><a-button size="small" :disabled="editDisabled" @click="startEdit">调整卡片</a-button></a-tooltip>
        </div>
        <a-space v-if="qaWriteReal && !editing">
          <a-button size="small" :disabled="shareDisabled || !pagesApi.access(page).canRefresh" @click="templateOpen = true; templateName = page.name">保存为模板</a-button>
          <a-button size="small" :disabled="readonly || (qaWriteReal && !pagesApi.access(page).canRefresh && !page.share.allowExport)" @click="download">导出当前结果 CSV</a-button>
        </a-space>
        <div v-if="!shownCards.length" class="c-3 pd-empty">尚无卡片，可从可执行的问数回答固定到此页面</div>
        <div v-if="!editing" class="pd-grid">
          <CardPreview v-for="(c, i) in pagedCards" :key="c.key" :card="c" :index="(cpage - 1) * CSIZE + i + 1" />
        </div>
        <div v-else class="pd-edit">
          <div class="t-12 c-3 pd-ell" title="拖动左侧手柄调整顺序；移除的卡片保存后从页面删除，可在问数工作台重新固定。">拖动左侧手柄调整顺序；移除的卡片保存后从页面删除，可在问数工作台重新固定。</div>
          <div v-for="(c, i) in pagedCards" :key="c.key" class="pd-erow" draggable="true"
            @dragstart="dragFrom = (cpage - 1) * CSIZE + i" @dragover.prevent @drop="onDrop((cpage - 1) * CSIZE + i)">
            <HolderOutlined class="c-3 pd-handle" />
            <span class="num c-3 t-12">{{ (cpage - 1) * CSIZE + i + 1 }}</span>
            <span class="pd-ell c-1" :title="c.title">{{ c.title }}</span>
            <span class="t-12 c-3">{{ c.viz }} · {{ c.metric }} {{ c.version }}</span>
            <a-button size="small" type="text" @click="removeCard((cpage - 1) * CSIZE + i)"><DeleteOutlined />移除</a-button>
          </div>
        </div>
        <AppPagination v-model:page="cpage" variant="simple" :total="shownCards.length" :size="CSIZE" unit="张卡片" />
      </template>

      <template v-else-if="tabV === 'schedule'">
        <div class="pd-form">
          <div class="t-16 fw-600">定时刷新</div>
          <a-alert v-if="qaWriteReal && !pagesApi.access(page).canRefresh" type="info" show-icon :message="pagesApi.access(page).refreshUnavailableReason || '尚无可执行卡片，暂不能开启自动刷新'" />
          <div class="pd-f"><span class="pd-l c-3">频率</span>
            <a-radio-group v-model:value="plan.freq" :disabled="scheduleDisabled">
              <a-radio-button v-for="f in ['每小时', '每天', '每周', '每月', '手动']" :key="f" :value="f" :disabled="qaWriteReal && f !== '手动' && !pagesApi.access(page).canRefresh">{{ f }}</a-radio-button>
            </a-radio-group>
          </div>
          <div v-if="plan.freq !== '手动' && plan.freq !== '每小时'" class="pd-f"><span class="pd-l c-3">时间</span>
            <a-select v-if="plan.freq === '每周'" v-model:value="plan.weekday" :disabled="scheduleDisabled" class="pd-s" :options="WEEKDAYS.map((w) => ({ value: w, label: w }))" />
            <a-select v-if="plan.freq === '每月'" v-model:value="plan.monthday" :disabled="scheduleDisabled" class="pd-s" :options="[1, 5, 10, 15, 20, 25].map((d) => ({ value: d, label: `${d} 日` }))" />
            <a-select v-model:value="plan.time" :disabled="scheduleDisabled" class="pd-s" :options="[...new Set([...TIMES, plan.time])].map((t) => ({ value: t, label: t }))" />
          </div>
          <div class="pd-f"><span class="pd-l c-3">刷新失败时</span>
            <a-radio-group v-model:value="plan.onFail" :disabled="scheduleDisabled"><a-radio value="保留上次结果">保留上次结果</a-radio><a-radio value="标记失败并通知" :disabled="qaWriteReal">标记失败并通知我</a-radio></a-radio-group>
          </div>
          <div class="pd-f"><span class="pd-l c-3">下次刷新</span><span class="num c-1">{{ nextRun }}</span><span class="t-12 c-3">时区 {{ qaWriteReal ? pagesApi.access(page).scheduleTimezone || '—' : 'Asia/Shanghai' }} · 按已保存条件与当前权限刷新</span></div>

          <div class="t-16 fw-600 pd-gap">订阅推送</div>
          <div class="pd-f"><span class="pd-l c-3">开启订阅</span><a-switch v-model:checked="sub.on" :disabled="scheduleDisabled" /></div>
          <template v-if="sub.on">
            <div class="pd-f"><span class="pd-l c-3">渠道</span>
              <a-radio-group v-model:value="sub.channel" :disabled="scheduleDisabled"><a-radio v-for="c in ['邮件', '企业微信', '站内信']" :key="c" :value="c" :disabled="qaWriteReal && c !== '站内信'">{{ c }}{{ qaWriteReal && c !== '站内信' ? '（未配置）' : '' }}</a-radio></a-radio-group>
            </div>
            <div class="pd-f"><span class="pd-l c-3">接收人</span>
              <a-select v-model:value="sub.receivers" mode="multiple" :disabled="scheduleDisabled" class="pd-wide" placeholder="默认发送给页面拥有者" :options="subscriptionPeople" :max-tag-count="4" />
            </div>
            <div class="pd-f"><span class="pd-l c-3">推送时机</span>
              <a-select v-model:value="sub.when" :disabled="scheduleDisabled" class="pd-m" :options="['每次刷新后', '每天 09:00', '每周一 09:00'].map((w) => ({ value: w, label: w }))" />
              <a-checkbox v-model:checked="sub.onlyChanged" :disabled="scheduleDisabled">仅数据有变化时推送</a-checkbox>
            </div>
            <div class="pd-f"><span class="pd-l" /><span class="t-12 c-3">接收人限页面拥有者和共享成员，内容按接收人的当前权限核验。</span></div>
          </template>
          <div class="pd-f"><span class="pd-l" />
            <a-button :disabled="saving" @click="resetDrafts">还原</a-button>
            <a-tooltip :title="reason || (qaWriteReal && !pagesApi.access(page).canSchedule ? '只有页面拥有者能配置刷新与订阅' : '')"><a-button type="primary" :disabled="scheduleDisabled" :loading="saving" @click="saveSchedule">保存</a-button></a-tooltip>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="pd-form">
          <div class="pd-f"><span class="pd-l c-3">共享方式</span>
            <a-radio-group v-model:value="share.mode" :disabled="shareDisabled">
              <a-radio value="仅自己">仅自己</a-radio><a-radio value="指定成员">指定成员</a-radio><a-radio value="所在部门">所在部门（{{ qaWriteReal ? pagesApi.access(page).ownerDept || '未登记' : '华东事业部 / 运营中心' }}）</a-radio>
            </a-radio-group>
          </div>
          <template v-if="share.mode === '指定成员'">
            <div class="pd-f"><span class="pd-l c-3">添加成员</span>
              <a-select v-model:value="addWho" mode="multiple" :disabled="shareDisabled" class="pd-wide" placeholder="搜索姓名或部门" :options="candidates" :filter-option="!qaWriteReal" :loading="memberLoading" :max-tag-count="3" @search="(value: string) => { memberQuery = value; }" />
              <a-select v-model:value="addPerm" :disabled="shareDisabled" class="pd-s" :options="[{ value: '可查看', label: '可查看' }, { value: '可编辑', label: '可编辑' }]" />
              <a-button :disabled="shareDisabled || !addWho.length" @click="addMembers">添加</a-button>
            </div>
            <div v-if="qaWriteReal" class="pd-f"><span class="pd-l" /><span v-if="memberError" class="c-3">{{ memberError }}</span><AppPagination v-model:page="memberPage" variant="simple" :total="memberTotal" :size="10" unit="位候选成员" :empty="false" /></div>
            <div class="pd-members">
              <div v-for="m in pagedMembers" :key="memberKey(m)" class="pd-mrow">
                <span class="c-1 pd-mname">{{ m.name }}</span><span class="c-1 pd-ell" :title="m.dept">{{ m.dept }}</span>
                <a-select v-model:value="m.perm" size="small" :disabled="shareDisabled" class="pd-s" :options="[{ value: '可查看', label: '可查看' }, { value: '可编辑', label: '可编辑' }]" />
                <a-button size="small" type="text" :disabled="shareDisabled" @click="share.members = share.members.filter((x) => memberKey(x) !== memberKey(m))">移除</a-button>
              </div>
              <div v-if="!share.members.length" class="t-12 c-3 pd-empty">还没有共享成员</div>
            </div>
            <AppPagination v-model:page="mpage" variant="simple" :total="share.members.length" :size="MSIZE" unit="位成员" :empty="false" />
          </template>
          <div class="pd-f"><span class="pd-l c-3">链接访问</span><a-switch v-model:checked="share.link" :disabled="shareDisabled || share.mode === '仅自己'" />
            <span class="t-12 c-3">链接不扩大共享范围</span>
            <a-button v-if="share.link && share.mode !== '仅自己'" size="small" @click="copyLink">复制链接</a-button>
          </div>
          <div class="pd-f"><span class="pd-l c-3">允许导出</span><a-switch v-model:checked="share.allowExport" :disabled="shareDisabled" /><span class="t-12 c-3">关闭后被共享人不能导出卡片数据</span></div>
          <div class="pd-f"><span class="pd-l" /><span class="t-12 c-3">被共享人看到的数据按各自的数据权限裁剪，范围外的卡片显示“无权限”。</span></div>
          <div class="pd-f"><span class="pd-l" />
            <a-button @click="resetDrafts">还原</a-button>
            <a-tooltip :title="reason"><a-button type="primary" :disabled="shareDisabled" @click="saveShare">保存共享设置</a-button></a-tooltip>
          </div>
        </div>
      </template>
    </div>
  </a-drawer>
  <a-modal v-model:open="templateOpen" title="保存为模板" :confirm-loading="saving" :ok-button-props="{ disabled: !templateName.trim() || shareDisabled }" @ok="saveTemplate">
    <a-input v-model:value="templateName" :maxlength="60" placeholder="模板名称" />
    <p class="t-12 c-3">模板保留原卡片条件和口径，不保存结果数据。</p>
  </a-modal>
</template>

<style scoped>
.pd { display: flex; flex-direction: column; gap: 8px; }
.pd-meta { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 4px 16px; }
.pd-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.pd-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pd-sp { flex: 1; }
.pd-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.pd-edit { display: flex; flex-direction: column; gap: 6px; }
.pd-erow { display: flex; align-items: center; gap: 10px; height: 40px; padding: 0 8px; border: var(--border); border-radius: var(--radius); cursor: grab; }
.pd-erow .pd-ell { flex: 1; }
.pd-handle { cursor: grab; }
.pd-form { display: flex; flex-direction: column; gap: 12px; }
.pd-gap { margin-top: 8px; }
.pd-f { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pd-l { width: 80px; flex: none; }
.pd-s { width: 110px; }
.pd-m { width: 160px; }
.pd-wide { width: 360px; }
.pd-members { display: flex; flex-direction: column; margin-left: 88px; border-top: var(--border); }
.pd-mrow { display: flex; align-items: center; gap: 12px; height: 40px; border-bottom: var(--border); }
.pd-mname { width: 64px; flex: none; }
.pd-mrow .pd-ell { flex: 1; }
.pd-empty { padding: 12px 0; }
.pd-pop { display: flex; gap: 8px; width: 300px; }
</style>
