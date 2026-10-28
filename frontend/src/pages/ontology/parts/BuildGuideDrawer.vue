<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { apiClient, currentApiContext, isApiError, type PageResult } from '@/api/http';
import { isStaticReal } from '@/api/static';
import type { OverviewSummary } from '@/api/overview';
const open = defineModel<boolean>('open', { default: false });
const router = useRouter();
interface Step { title: string; purpose: string; paths: { label: string; path: string }[]; state: string; detail: string }
const definitions = [
  { title: '数据连接', purpose: '登记只读连接并完成连通性检查，查看可内省的库表。', paths: [{ label: '数据连接', path: '/build/data/connections' }] },
  { title: '对象与关系', purpose: '配置对象属性、主键、来源映射与对象关系；导入确认后保存并生效。', paths: [{ label: '对象类型', path: '/build/ontology/object-types' }, { label: '关系类型', path: '/build/ontology/link-types' }] },
  { title: '指标、维度与知识', purpose: '配置口径、显式变体、分析层级和业务说明。公式定义不能替代执行器。', paths: [{ label: '指标', path: '/build/ontology/metrics' }, { label: '维度与字典', path: '/build/ontology/dimensions' }, { label: '业务知识', path: '/build/ontology/glossary' }] },
  { title: '函数', purpose: '按需登记受限纯计算函数，供规则、动作等引用；函数版本独立于本体发布快照，无需函数时可跳过此步。', paths: [{ label: '函数', path: '/build/ontology/functions' }] },
  { title: '动作与事件', purpose: '配置动作提交条件、事件载荷和订阅方；外部投递通道需单独验收。', paths: [{ label: '动作类型', path: '/build/ontology/action-types' }, { label: '事件类型', path: '/build/ontology/event-types' }] },
  { title: '规则与生命周期', purpose: '按需配置规则与状态流转；无需自动化时可跳过此步。', paths: [{ label: '规则', path: '/build/ontology/rules' }, { label: '对象生命周期', path: '/build/ontology/object-types?tab=lifecycle' }] },
  { title: '校验与发布', purpose: '在变更单校验并直接发布；发布前已发布定义继续生效。', paths: [{ label: '变更与发布', path: '/build/ontology/releases' }] },
];
const steps = ref<Step[]>(definitions.map((s) => ({ ...s, state: '未加载', detail: '' })));
const loading = ref(false);
let seq = 0;
const ctxKey = () => JSON.stringify(currentApiContext());
const historical = computed(() => !!currentApiContext().ver);
async function load() {
  const my = ++seq, key = ctxKey();
  steps.value = definitions.map((s) => ({ ...s, state: isStaticReal() ? '加载中' : '演示模式', detail: isStaticReal() ? '' : '按演示页面查看流程，不向后台请求。' }));
  if (!open.value || !isStaticReal()) { loading.value = false; return; }
  loading.value = true;
  const ctx = currentApiContext();
  const options = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs, page: 1, size: 1 }, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
  const ontology = apiClient('static');
  const count = async (module: 'static' | 'action' | 'dynamic' | 'data', path: string) => {
    const api = apiClient(module);
    if (!api.isReal()) throw new Error('相关模块尚未启用真实接口');
    return (await api.get<PageResult<unknown>>(path, options)).total;
  };
  // 可试算计数走只读批量就绪接口；历史版本不借用当前映射，失败时只省略计数，不影响步骤状态。
  const readiness = async () => {
    if (ctx.ver) return '';
    try { const r = await ontology.get<{ total: number; ready: number }>('/metrics/trial-readiness', { ...options, query: { ver: ctx.ver, previewAs: ctx.previewAs } }); return `（${r.ready}/${r.total} 可试算）`; }
    catch { return '（可试算数加载失败）'; }
  };
  const results = await Promise.allSettled([
    count('data', '/datasources').then((n) => ({ count: n, detail: `${n} 个已登记连接；登记不代表连通性检查通过` })),
    ontology.get<OverviewSummary>('/ontology/overview', options).then((summary) => ({ count: summary.cards.filter((c) => ['obj', 'link'].includes(c.key)).reduce((n,c) => n+c.count,0), detail: summary.cards.filter((c) => ['obj','link'].includes(c.key)).map((c) => `${c.count} ${c.unit}${c.title}`).join(' · ') })),
    Promise.all([count('static', '/metrics'), count('static', '/dimensions'), count('static', '/knowledge'), readiness()]).then(([m,d,k,r]) => ({ count: m+d+k, detail: `${m} 个指标${r} · ${d} 个维度/码表 · ${k} 条知识` })),
    // 函数列表不按 ?ver= 区分版本，历史版本下不借用当前数量。
    ctx.ver ? Promise.resolve({ count: -1, detail: '函数列表不区分历史版本，请在函数页查看。' }) : count('static', '/functions').then((n) => ({ count: n, detail: `${n} 个函数（全部状态）` })),
    Promise.all([count('action', '/action-types'), count('action', '/event-types')]).then(([a,e]) => ({ count: a+e, detail: `${a} 个动作类型 · ${e} 个事件类型` })),
    ctx.ver ? Promise.resolve({ count: -1, detail: '请在对象页查看生命周期。' }) : count('dynamic', '/rules').then((n) => ({ count: n, detail: `${n} 条规则；生命周期请在对象页按需查看。` })),
    count('static', '/change-requests').then((n) => ({ count: n, detail: `${n} 张变更单；请进入后查看校验、审核和发布状态。` })),
  ]);
  if (my !== seq || key !== ctxKey() || !open.value) return;
  results.forEach((r, i) => {
    if (r.status === 'fulfilled') Object.assign(steps.value[i], { state: r.value.count < 0 ? '不适用' : r.value.count ? '已配置' : '未配置', detail: r.value.detail });
    else Object.assign(steps.value[i], { state: isApiError(r.reason) && r.reason.status === 403 ? '无权限' : '加载失败', detail: r.reason instanceof Error ? r.reason.message : String(r.reason) });
  });
  loading.value = false;
}
watch(() => [open.value, ctxKey()], () => { void load(); });
function go(path: string) { const ctx = currentApiContext(); open.value = false; void router.push({ path: path.split('?')[0], query: { ...Object.fromEntries(new URLSearchParams(path.split('?')[1] ?? '')), ver: ctx.ver || undefined, previewAs: ctx.previewAs || undefined } }); }
</script>
<template>
  <a-drawer v-model:open="open" title="构建引导" :width="760">
    <a-alert type="info" :message="historical ? '当前查看历史定义，只读；连接和运行通道的当前状态不代表历史版本。' : '按需配置，所有定义改动经变更单整版发布后生效。'" show-icon />
    <a-spin :spinning="loading"><ol class="build-steps"><li v-for="(step, i) in steps" :key="step.title"><div class="build-step-head"><span class="t-16 fw-600">{{ i+1 }}. {{ step.title }}</span><a-tag :color="step.state === '加载失败' ? 'error' : undefined">{{ step.state }}</a-tag></div><p class="t-14 c-2">{{ step.purpose }}</p><p class="t-12 c-3">{{ step.detail }}</p><a-space><a-button v-for="link in step.paths" :key="link.path" size="small" @click="go(link.path)">{{ link.label }}</a-button></a-space></li></ol></a-spin>
    <template #footer><a-button :loading="loading" @click="load">刷新状态</a-button></template>
  </a-drawer>
</template>
<style scoped>
.build-steps { list-style: none; padding: 0; margin: 16px 0 0; }
.build-steps li { padding: 16px 0; border-bottom: 1px solid var(--g5); }
.build-step-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.build-steps p { margin: 8px 0; }
</style>
