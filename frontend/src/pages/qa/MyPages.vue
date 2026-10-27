<script setup lang="ts">
/**
 * 我的页面与模板库（/use/qa/pages）
 *  页签 我的页面 / 模板库（?tab=templates）；表格 full 分页（我的页面 ?page=，模板库 ?tpage=）
 *  行点击 → 页面抽屉（?page_id=P001&ptab=preview|schedule|share）：卡片预览、数据截至、口径版本、刷新与订阅、共享
 *  行内动作：重命名（气泡）、共享（抽屉共享页签）、刷新、删除（确认卡）
 *  模板：预览抽屉（?tpl=T001）+ “用此模板新建”气泡
 * 问数工作台“固定到页面”的页面（华东大区销售周报、门店履约看板……）就是这里的前 13 个页面（同一份 QA_PAGE_LIST）。
 * 真接口（开关 qa 且后台可用，qaLive）：列表 P1、详情 P3、新建 P2、重命名 P4、删除 P5、刷新 P6、模板库 P11（src/api/qaPages.ts）；
 * 后台判定未启动后读接口整页回退演示数据；写操作不回退：开关为真时一律调真接口，失败只报错、不改演示数据（qaWriteReal / qaWriteFail，见 qaFallback.ts）。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
import { useConfirmStore } from '@/stores/confirm';
import { paginate } from '@/mock';
import { MY_PAGES, OTHER_PAGES, OWNER_DEPT, PAGE_TEMPLATES, PAGES_CUTOFF, PAGE_SCOPES, freqLabel, type QaPageFull, type QaTemplate, type TplCreate, type RefreshPlan } from '@/mock/qa-pages';
import PageDrawer from './pages/PageDrawer.vue';
import TemplateDrawer from './pages/TemplateDrawer.vue';
import UseTemplatePop from './pages/UseTemplatePop.vue';
import * as pagesApi from '@/api/qaPages';
import { currentApiContext, type PageResult } from '@/api/http';
import { getSessionOptions } from '@/api/qa';
import { qaFallBackIfDown, qaWriteReal, qaWriteFail } from './qaFallback';
import { errText } from './useQaSession';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();
/* 预览：页面卡片按被预览用户的范围取数 */
const pv = usePreview();
const pvText = computed(() => pv.limited.value.map((o) => `${o.name} ${o.scope}`).join('；') || '全部');
const confirm = useConfirmStore();

const pageContextKey = () => JSON.stringify(currentApiContext());
const pages = reactive<QaPageFull[]>(qaWriteReal.value ? [] : [...MY_PAGES, ...OTHER_PAGES].map((p) => ({ ...p })));
/** 列表可见：平时是我（王敏）的页面；预览时是被预览用户自己的页面 + 共享给他的 */
const ME = '王敏';
const visible = (p: QaPageFull) => {
  const who = pv.active.value ? pv.userName.value : ME;
  const dept = pv.active.value ? (pv.session.value?.user.dept ?? '') : OWNER_DEPT[ME];
  if (p.owner === who) return true;
  if (!pv.active.value) return false;
  return (p.share.mode === '指定成员' && p.share.members.some((m) => m.name === who)) || (p.share.mode === '所在部门' && !!dept && dept.includes(OWNER_DEPT[p.owner] ?? '-'));
};
const tab = computed({ get: () => (q('tab') === 'templates' ? 'templates' : 'pages'), set: (v: string) => setQuery({ tab: v === 'templates' ? 'templates' : undefined }) });

/* ── 我的页面 ── */
const kw = computed({ get: () => q('kw'), set: (v: string) => setQuery({ kw: v || undefined }) });
const srcF = computed({ get: () => q('src') || undefined, set: (v?: string) => setQuery({ src: v || undefined }) });
const shareF = computed({ get: () => q('share') || undefined, set: (v?: string) => setQuery({ share: v || undefined }) });
const statusF = computed({ get: () => q('status') || undefined, set: (v?: string) => setQuery({ status: v || undefined }) });
const rows = computed(() => pages.filter((p) => visible(p) && (!kw.value || p.name.includes(kw.value))
  && (!srcF.value || p.sourceKind === srcF.value)
  && (!shareF.value || (shareF.value === '已共享' ? p.share.mode !== '仅自己' : p.share.mode === '仅自己'))
  && (!statusF.value || p.status === statusF.value)));
const pq = usePagedQuery({ defaultSize: 10, filters: [kw, srcF, shareF, statusF] });
const paged = computed(() => (qaWriteReal.value ? { items: live.list?.items ?? [], total: live.list?.total ?? 0 } : paginate(rows.value, { page: pq.page.value, size: pq.size.value })));

/* ── 真接口 ── */
const live = reactive<{ list: pagesApi.PageList | null; count: number; tpls: PageResult<QaTemplate> | null; cur: QaPageFull | null }>({ list: null, count: 0, tpls: null, cur: null });
let lseq = 0;
async function loadList() {
  const my = ++lseq;
  live.list = null; live.count = 0;
  if (!qaWriteReal.value) return;
  const query = { kw: kw.value || undefined, src: srcF.value, share: shareF.value, status: statusF.value, page: pq.page.value, size: pq.size.value };
  try {
    const [r, all] = await Promise.all([
      pagesApi.listPages(query),
      pagesApi.listPages({ page: 1, size: 1 }),
    ]);
    if (my !== lseq) return;
    live.count = all.total;
    const lastPage = Math.max(1, Math.ceil(r.total / query.size));
    if (query.page > lastPage) { pq.page.value = lastPage; return; }
    live.list = r;
  } catch (e) { if (my === lseq && !qaFallBackIfDown(e)) message.error(`页面列表加载失败：${errText(e)}`); }
}
watch([qaWriteReal, kw, srcF, shareF, statusF, pq.page, pq.size, pageContextKey], loadList, { immediate: true });
let cseq = 0;
async function loadCur() {
  const my = ++cseq;
  live.cur = null;
  const id = q('page_id');
  if (!qaWriteReal.value || !id) { live.cur = null; return; }
  try { const r = await pagesApi.getPage(id); if (my === cseq) live.cur = r; } catch (e) { if (my === cseq) { live.cur = null; if (!qaFallBackIfDown(e)) message.error(`页面加载失败：${errText(e)}`); } }
}
watch([qaWriteReal, () => q('page_id'), pageContextKey], loadCur, { immediate: true });
/** 写操作（开关 qa 为真时，不看后台是否判定未启动）：成功返回结果；失败（含后台未启动）只提示并返回 undefined。
 *  写操作不回退：不改演示数据、不假装成功、不走 qaFallBackIfDown（不把整页切到演示数据） */
async function call<T>(fn: () => Promise<T>, what: string): Promise<T | undefined> {
  const key = pageContextKey();
  try { const result = await fn(); return key === pageContextKey() ? result : undefined; }
  catch (e) { if (key === pageContextKey()) qaWriteFail(what, e); return undefined; }
}
/** 列表行和抽屉里的同一页面一起更新 */
function patchLive(id: string, patch: Partial<QaPageFull>) {
  live.list?.items.filter((x) => x.id === id).forEach((x) => Object.assign(x, patch));
  if (live.cur?.id === id) Object.assign(live.cur, patch);
}
const mockOf = (p: QaPageFull) => pages.find((x) => x.id === p.id);
const cols = [
  { title: '页面名称', key: 'name', ellipsis: true },
  { title: '卡片', key: 'cards', width: 64, align: 'right' },
  { title: '范围', dataIndex: 'scope', key: 'scope', width: 96 },
  { title: '口径版本', key: 'caliber', width: 260, ellipsis: true },
  { title: '来源', key: 'source', width: 220, ellipsis: true },
  { title: '刷新计划', key: 'refresh', width: 130 },
  { title: '订阅', key: 'sub', width: 110 },
  { title: '共享', key: 'share', width: 100 },
  { title: '数据截至', key: 'asof', width: 130 },
  { title: '状态', key: 'status', width: 100 },
  { title: '操作', key: 'op', width: 210 },
];
const caliberText = (p: QaPageFull) => { const s = [...new Set(p.cards.map((c) => `${c.metric} ${c.version}`))]; return { text: s.join('、'), n: s.length }; };
const shareText = (p: QaPageFull) => (p.share.mode === '仅自己' ? '仅自己' : p.share.mode === '所在部门' ? '所在部门' : `${p.share.members.length} 位成员`);
const stCls = (s: string) => (s === '刷新失败' ? 'tl-failed' : 'c-2');

/* 抽屉 */
const current = computed(() => (qaWriteReal.value ? live.cur ?? undefined : pages.find((p) => p.id === q('page_id'))));
const openPage = (p: QaPageFull, ptab?: string) => setQuery({ page_id: p.id, ptab: ptab && ptab !== 'preview' ? ptab : undefined, tpl: undefined });
const closePage = () => setQuery({ page_id: undefined, ptab: undefined });
const ptab = computed({ get: () => q('ptab') || 'preview', set: (v: string) => setQuery({ ptab: v === 'preview' ? undefined : v }) });

/* 动作 */
const NOW = '2026-10-25 17:10';
async function refresh(p0: QaPageFull) {
  if (qaWriteReal.value) {
    if (readonly.value || !pagesApi.access(p0).canRefresh) return;
    const key = pageContextKey();
    const result = await call(() => pagesApi.refreshPage(p0.id), '刷新页面');
    if (!result || key !== pageContextKey()) return;
    patchLive(p0.id, result);
    if (result.status === '刷新失败') message.error(result.statusNote || '刷新失败，原结果保留');
    else message.success('页面已按当前权限刷新');
    await loadList(); return;
  }
  const p = mockOf(p0); if (!p) return;
  p.lastRefresh = NOW; p.dataAsOf = PAGES_CUTOFF;
  if (p.status === '刷新失败') { p.status = p.cards.some((c) => c.pending) ? '口径待更新' : '正常'; p.statusNote = p.cards.find((c) => c.pending)?.pending; }
  message.success(`已刷新「${p.name}」，数据截至 ${PAGES_CUTOFF}`);
}
const renameId = ref<string>();
const renameVal = ref('');
function openRename(p: QaPageFull) { renameId.value = p.id; renameVal.value = p.name; }
async function rename(p0: QaPageFull, name: string) {
  if (qaWriteReal.value) {
    const r = await call(() => pagesApi.renamePage(p0.id, name), '重命名');
    if (r === undefined) return;
    const old = p0.name; patchLive(p0.id, { name: r.name, updatedAt: r.updatedAt }); renameId.value = undefined; message.success(`已将「${old}」重命名为「${r.name}」`); return;
  }
  const p = mockOf(p0); if (!p) return;
  if (pages.some((x) => x.id !== p.id && x.name === name)) { message.warning(`已有同名页面「${name}」`); return; }
  const old = p.name; p.name = name; renameId.value = undefined; message.success(`已将「${old}」重命名为「${name}」`);
}
async function remove(p: QaPageFull) {
  const shared = p.share.mode !== '仅自己';
  const ok = await confirm.ask({
    title: '删除页面', target: `${p.name}（${p.id}）`, danger: true, okText: '删除', tag: '不可恢复',
    fields: [{ label: '卡片', value: `${p.cards.length} 张` }, { label: '共享', value: shareText(p) }, { label: '订阅', value: p.sub.on ? `${p.sub.channel} · ${p.sub.receivers.length} 人` : '未开启' }],
    impact: `${shared ? '被共享人将无法再打开此页面，' : ''}${p.sub.on ? '订阅推送随之停止，' : ''}定时刷新停止。问数会话里的原始回答不受影响。`,
    irreversible: true,
  });
  if (!ok) return;
  if (qaWriteReal.value) {
    const r = await call(() => pagesApi.deletePage(p.id).then(() => true), '删除');
    if (r === undefined) return;
    if (q('page_id') === p.id) closePage(); message.success(`已删除「${p.name}」`); await loadList(); return;
  }
  const i = pages.findIndex((x) => x.id === p.id); if (i >= 0) pages.splice(i, 1);
  if (q('page_id') === p.id) closePage();
  message.success(`已删除「${p.name}」`);
}

/* 新建空白页面 */
/* 从问数工作台“把会话整理成页面”进入时带 ?newName=，直接打开新建并填好名称 */
const newOpen = ref(!!q('newName'));
const newName = ref(q('newName'));
const newScope = ref(qaWriteReal.value ? '' : '华东大区');
const scopeChoices = ref<string[]>(qaWriteReal.value ? [] : PAGE_SCOPES);
let scopeSeq = 0;
async function loadPageOptions() {
  const my = ++scopeSeq;
  if (!qaWriteReal.value) return;
  scopeChoices.value = []; newScope.value = '';
  try {
    const result = await getSessionOptions();
    if (my !== scopeSeq) return;
    scopeChoices.value = result.scopes.map((s) => s.value);
    newScope.value = scopeChoices.value[0] ?? '';
  } catch (e) { if (my === scopeSeq) message.error(`页面范围加载失败：${errText(e)}`); }
}
watch([qaWriteReal, pageContextKey], loadPageOptions, { immediate: true });
async function createBlank() {
  if (qaWriteReal.value) {
    const sourceSessionId = q('source_session') || undefined;
    const r = await call(() => pagesApi.createPage({ name: newName.value.trim(), scope: '当前数据权限', sourceSessionId }), '新建页面');
    if (r === undefined) return;
    newOpen.value = false; newName.value = ''; message.success(sourceSessionId ? '会话已整理为真实页面' : '空白页面已创建');
    setQuery({ newName: undefined, source_session: undefined, page_intent: undefined, page_id: r.id, ptab: sourceSessionId && q('page_intent') === 'share' ? 'share' : 'preview' }); await loadList(); return;
  }
  const id = nextId();
  pages.unshift({ id, name: newName.value.trim(), owner: '王敏', scope: newScope.value, sourceKind: '手动新建', source: '手动新建', cards: [], dataAsOf: PAGES_CUTOFF, lastRefresh: NOW, updatedAt: NOW,
    refresh: { freq: '每天', time: '08:00', onFail: '保留上次结果' }, sub: { on: false, channel: '邮件', receivers: [], when: '每次刷新后', onlyChanged: false },
    share: { mode: '仅自己', members: [], link: false, allowExport: true }, status: '正常', views: 0 });
  newOpen.value = false; newName.value = '';
  message.success('已新建空白页面，可在问数工作台把回答卡片固定到这里');
}
const nextId = () => `P${String(Math.max(...pages.map((p) => Number(p.id.slice(1))), 0) + 1).padStart(3, '0')}`;

/* ── 模板库 ── */
const tkw = computed({ get: () => q('tkw'), set: (v: string) => setQuery({ tkw: v || undefined }) });
const sceneF = computed({ get: () => q('scene') || undefined, set: (v?: string) => setQuery({ scene: v || undefined }) });
const SCENES = computed(() => [...new Set((qaWriteReal.value ? live.tpls?.items ?? [] : PAGE_TEMPLATES).map((t) => t.scene))]);
const trows = computed(() => PAGE_TEMPLATES.filter((t) => (!tkw.value || t.name.includes(tkw.value)) && (!sceneF.value || t.scene === sceneF.value)));
const tq = usePagedQuery({ defaultSize: 10, filters: [tkw, sceneF], prefix: 't' });
const tpaged = computed(() => (qaWriteReal.value ? { items: live.tpls?.items ?? [], total: live.tpls?.total ?? 0 } : paginate(trows.value, { page: tq.page.value, size: tq.size.value })));
let tseq = 0;
async function loadTpls() {
  const my = ++tseq;
  live.tpls = null;
  if (!qaWriteReal.value || tab.value !== 'templates') return;
  try { const r = await pagesApi.listTemplates({ kw: tkw.value || undefined, scene: sceneF.value, page: tq.page.value, size: tq.size.value }); if (my === tseq) live.tpls = r; } catch (e) { if (my === tseq && !qaFallBackIfDown(e)) message.error(`模板库加载失败：${errText(e)}`); }
}
watch([qaWriteReal, tab, tkw, sceneF, tq.page, tq.size, pageContextKey], loadTpls, { immediate: true });
const tcols = [
  { title: '模板名称', key: 'name', width: 200 },
  { title: '场景', dataIndex: 'scene', key: 'scene', width: 96 },
  { title: '卡片', key: 'cards', width: 64, align: 'right' },
  { title: '包含口径', key: 'caliber', ellipsis: true },
  { title: '提供方', key: 'provider', width: 150 },
  { title: '使用次数', key: 'uses', width: 96, align: 'right' },
  { title: '更新时间', key: 'updatedAt', width: 110 },
  { title: '操作', key: 'op', width: 170 },
];
const tplCur = computed(() => qaWriteReal.value ? live.tpls?.items.find((t) => t.id === q('tpl')) : PAGE_TEMPLATES.find((t) => t.id === q('tpl')));
const openTpl = (t: QaTemplate) => setQuery({ tpl: t.id, page_id: undefined, ptab: undefined });
async function templateCreated(id: string) { setQuery({ tab: undefined, tpl: undefined, page_id: id, ptab: 'preview' }); await loadList(); await loadTpls(); }
async function fromTemplate(e: TplCreate) {
  if (qaWriteReal.value) {
    const r = await call(() => pagesApi.createPage({ name: e.name, scope: e.scope, template: { id: e.tpl.id, caliber: e.caliber, refresh: pagesApi.planOf(e.refresh) } }), '用模板新建');
    if (r === undefined) return;
    setQuery({ tab: undefined, tpl: undefined, page_id: r.id }); message.success(`已用模板「${e.tpl.name}」新建「${r.name}」`); await loadList(); return;
  }
  const id = nextId();
  const refresh: RefreshPlan = e.refresh.startsWith('每小时') ? { freq: '每小时', time: '整点', onFail: '保留上次结果' } : e.refresh.startsWith('每周') ? { freq: '每周', weekday: '周一', time: '08:00', onFail: '保留上次结果' } : e.refresh.startsWith('手动') ? { freq: '手动', time: '--', onFail: '保留上次结果' } : { freq: '每天', time: '08:00', onFail: '保留上次结果' };
  const cards = e.tpl.cards.map((c, i) => ({ ...c, key: `${id}-C${i + 1}` }));
  pages.unshift({ id, name: e.name, owner: '王敏', scope: e.scope, sourceKind: '模板', source: `模板「${e.tpl.name}」· ${e.caliber}`, cards, dataAsOf: PAGES_CUTOFF, lastRefresh: NOW, updatedAt: NOW,
    refresh, sub: { on: false, channel: '邮件', receivers: [], when: '每次刷新后', onlyChanged: false }, share: { mode: '仅自己', members: [], link: false, allowExport: true },
    status: cards.some((c) => c.pending) ? '口径待更新' : '正常', statusNote: cards.find((c) => c.pending)?.pending, views: 0 });
  router.replace({ query: { tab: undefined, page_id: id } });
  message.success(`已用模板「${e.tpl.name}」新建「${e.name}」`);
}
const rowProps = (p: QaPageFull) => ({ class: p.id === q('page_id') ? 'mp-row-on' : '', onClick: () => openPage(p) });
const trowProps = (t: QaTemplate) => ({ class: t.id === q('tpl') ? 'mp-row-on' : '', onClick: () => openTpl(t) });
const fromWb = computed(() => (qaWriteReal.value ? live.list?.fromWorkbenchCount ?? 0 : pages.filter((p) => p.sourceKind === '问数工作台').length));
const pageCount = computed(() => (qaWriteReal.value ? live.count : pages.filter(visible).length));
const tplCount = computed(() => (qaWriteReal.value ? live.tpls?.total ?? 0 : PAGE_TEMPLATES.length));
const cutoff = computed(() => {
  if (!qaWriteReal.value) return PAGES_CUTOFF;
  const dates = (live.list?.items ?? []).map((p) => p.dataAsOf).filter(Boolean).sort();
  return dates.length && dates.length === live.list?.items.length ? dates[0] : '—';
});
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-space>
      <span class="t-12 c-3">本页卡片数据截至 <span class="num">{{ cutoff }}</span></span>
      <a-button @click="router.push('/use/qa/workbench')">去问数工作台</a-button>
      <a-popover v-model:open="newOpen" trigger="click" placement="bottomRight" :title="q('source_session') ? '把会话整理成页面' : '新建空白页面'">
        <template #content>
          <div class="mp-pop">
            <div class="mp-f"><span class="c-3 mp-l">页面名称</span><a-input v-model:value="newName" :maxlength="30" placeholder="例如：华东复购分析" /></div>
            <div class="mp-f"><span class="c-3 mp-l">范围</span><span v-if="qaWriteReal" class="c-2">保留原卡片条件，按当前数据权限读取</span><a-select v-else v-model:value="newScope" class="mp-c" :options="scopeChoices.map((s) => ({ value: s, label: s }))" /></div>
            <div class="mp-btns"><a-button size="small" @click="newOpen = false">取消</a-button><a-button size="small" type="primary" :disabled="!newName.trim()" @click="createBlank">新建</a-button></div>
          </div>
        </template>
        <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly">新建页面</a-button></a-tooltip>
      </a-popover>
    </a-space>
  </Teleport>

  <div class="panel mp">
    <a-tabs v-model:active-key="tab" class="mp-tabs">
      <a-tab-pane key="pages" :tab="pv.active.value ? `${pv.userName.value} 的页面` : '我的页面'" />
      <a-tab-pane key="templates" tab="模板库" />
    </a-tabs>

    <template v-if="tab === 'pages'">
      <div class="mp-filters">
        <a-input-search v-model:value="kw" allow-clear placeholder="搜索页面名称" class="mp-kw" />
        <a-select v-model:value="srcF" allow-clear placeholder="来源" class="mp-sel" :options="['问数工作台', '模板', '手动新建'].map((s) => ({ value: s, label: s }))" />
        <a-select v-model:value="shareF" allow-clear placeholder="共享" class="mp-sel" :options="['已共享', '仅自己'].map((s) => ({ value: s, label: s }))" />
        <a-select v-model:value="statusF" allow-clear placeholder="状态" class="mp-sel" :options="['正常', '口径待更新', '刷新失败'].map((s) => ({ value: s, label: s }))" />
        <span v-if="pv.active.value" class="t-12 c-2 mp-hint">{{ pv.userName.value }} 的数据范围：{{ pvText }}，卡片只显示范围内数据</span>
        <span v-else class="t-12 c-3 mp-hint">{{ fromWb }} 个页面由问数工作台“固定到页面”生成</span>
      </div>
      <div class="mp-table" :class="{ 'paged-scroll': pq.overflow.value }">
        <a-table size="small" row-key="id" :columns="cols" :data-source="paged.items" :pagination="false" :custom-row="rowProps">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <a-popover :open="renameId === record.id" trigger="click" placement="bottomLeft" @open-change="(o: boolean) => { if (!o) renameId = undefined; }">
                <template #content>
                  <div class="mp-pop" @click.stop>
                    <a-input v-model:value="renameVal" :maxlength="30" show-count @press-enter="rename(record, renameVal.trim())" />
                    <div class="mp-btns"><a-button size="small" @click="renameId = undefined">取消</a-button><a-button size="small" type="primary" :disabled="!renameVal.trim()" @click="rename(record, renameVal.trim())">确定</a-button></div>
                  </div>
                </template>
                <span class="c-1">{{ record.name }}</span>
                <a-tag v-if="pv.active.value && record.owner !== pv.userName.value" class="mp-tag">{{ record.owner }} 共享</a-tag>
              </a-popover>
            </template>
            <span v-else-if="column.key === 'cards'" class="num">{{ record.cards.length }}</span>
            <span v-else-if="column.key === 'caliber'" class="c-2" :title="caliberText(record).text">{{ caliberText(record).text || '--' }}</span>
            <span v-else-if="column.key === 'source'" class="c-2" :title="record.source">{{ record.source }}</span>
            <span v-else-if="column.key === 'refresh'" class="c-2">{{ freqLabel(record.refresh) }}{{ qaWriteReal && record.refresh.freq !== '手动' && !pagesApi.access(record).scheduleActive ? '（未排定）' : '' }}</span>
            <span v-else-if="column.key === 'sub'" class="c-2">{{ !record.sub?.on ? '未开启' : qaWriteReal && (record.sub.channel !== '站内信' || !pagesApi.access(record).subscriptionActive) ? `${record.sub.channel}（未启用）` : `${record.sub.channel} · ${record.sub.receivers.length ? `${record.sub.receivers.length} 人` : '页面拥有者'}` }}</span>
            <span v-else-if="column.key === 'share'" class="c-2">{{ shareText(record) }}</span>
            <span v-else-if="column.key === 'asof'" class="num c-2">{{ record.dataAsOf?.slice(5) ?? '—' }}</span>
            <span v-else-if="column.key === 'status'" :class="stCls(record.status)" :title="record.statusNote">{{ record.status }}</span>
            <span v-else-if="column.key === 'op'" class="mp-ops" @click.stop>
              <a-tooltip :title="reason || (qaWriteReal ? pagesApi.access(record).refreshUnavailableReason : '')"><a-button type="link" size="small" :disabled="readonly || (qaWriteReal && !pagesApi.access(record).canRefresh)" @click="refresh(record)">刷新</a-button></a-tooltip>
              <a-tooltip :title="reason"><a-button type="link" size="small" :disabled="readonly || (qaWriteReal && !pagesApi.access(record).canEdit)" @click="openRename(record)">重命名</a-button></a-tooltip>
              <a-button type="link" size="small" @click="openPage(record, 'share')">共享</a-button>
              <a-tooltip :title="reason"><a-button type="link" size="small" class="mp-del" :disabled="readonly || (qaWriteReal && !pagesApi.access(record).canShare)" @click="remove(record)">删除</a-button></a-tooltip>
            </span>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" variant="full" :total="paged.total" :default-size="pq.defaultSize" unit="个页面" empty-text="没有符合条件的页面" class="mp-pager" />
    </template>

    <template v-else>
      <div class="mp-filters">
        <a-input-search v-model:value="tkw" allow-clear placeholder="搜索模板名称" class="mp-kw" />
        <a-select v-model:value="sceneF" allow-clear placeholder="场景" class="mp-sel" :options="SCENES.map((s) => ({ value: s, label: s }))" />
        <span class="t-12 c-3 mp-hint">模板只含卡片结构和口径；新建后按所选范围取数</span>
      </div>
      <div class="mp-table" :class="{ 'paged-scroll': tq.overflow.value }">
        <a-table size="small" row-key="id" :columns="tcols" :data-source="tpaged.items" :pagination="false" :custom-row="trowProps">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'name'" class="c-1">{{ record.name }}</span>
            <span v-else-if="column.key === 'cards'" class="num">{{ record.cards.length }}</span>
            <span v-else-if="column.key === 'caliber'" class="c-2" :title="[...new Set(record.cards.map((c: any) => `${c.metric} ${c.version}`))].join('、')">{{ [...new Set(record.cards.map((c: any) => `${c.metric} ${c.version}`))].join('、') }}</span>
            <span v-else-if="column.key === 'provider'" class="c-2">{{ record.provider }}</span>
            <span v-else-if="column.key === 'uses'" class="num">{{ record.uses }}</span>
            <span v-else-if="column.key === 'updatedAt'" class="num c-2">{{ record.updatedAt }}</span>
            <span v-else-if="column.key === 'op'" class="mp-ops" @click.stop>
              <a-button type="link" size="small" @click="openTpl(record)">预览</a-button>
              <UseTemplatePop :tpl="record" :disabled="readonly" :reason="reason" @create="fromTemplate" @created="templateCreated" />
            </span>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="tq.page.value" v-model:size="tq.size.value" variant="full" :total="tpaged.total" :default-size="tq.defaultSize" unit="个模板" empty-text="没有符合条件的模板" class="mp-pager" />
    </template>
  </div>

  <PageDrawer :page="current" v-model:tab="ptab" :readonly="readonly" :reason="reason" @close="closePage" @refresh="refresh" @rename="rename" @remove="remove" @changed="() => { loadList(); loadCur(); loadTpls(); }" />
  <TemplateDrawer :tpl="tplCur" :readonly="readonly" :reason="reason" @close="setQuery({ tpl: undefined })" @create="fromTemplate" @created="templateCreated" />
</template>

<style scoped>
.mp-del:not(:disabled) { color: var(--g8); }
.mp-del:not(:disabled):hover { color: var(--g10); }
.mp { flex: 1; min-height: 0; }
.mp-tabs { flex: none; padding: 0 16px; }
.mp-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.mp-filters { display: flex; gap: 8px; align-items: center; padding: 12px 16px; flex: none; }
.mp-kw { width: 240px; }
.mp-sel { width: 130px; }
.mp-hint { margin-left: auto; }
.mp-tag { margin-inline-start: 8px; margin-inline-end: 0; }
.mp-table { flex: 1; min-height: 0; padding: 0 16px; }
.mp-table :deep(.ant-table-row) { cursor: pointer; }
.mp-table :deep(.mp-row-on > td) { background: var(--brand-bg) !important; }
.mp-pager { padding: 6px 16px 8px; }
.mp-ops { display: inline-flex; }
.mp-ops :deep(.ant-btn-link) { padding: 0 4px; }
.mp-pop { width: 300px; display: flex; flex-direction: column; gap: 10px; }
.mp-f { display: flex; align-items: center; gap: 8px; }
.mp-l { width: 56px; flex: none; }
.mp-c { flex: 1; }
.mp-btns { display: flex; justify-content: flex-end; gap: 8px; }
</style>
