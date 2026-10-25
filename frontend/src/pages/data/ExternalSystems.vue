<script setup lang="ts">
/**
 * 外部系统：动作写回用到的系统（CRM、WMS、短信、顺丰……）。
 * 左侧系统列表，右侧配置（地址、认证、幂等、超时 / 重试）+ 最近调用（simple 分页，按结果筛选）。
 * 调用结果颜色按时间线规则：成功 灰、结果未知 橙、失败 红。点“详情”打开调用抽屉（?call=），结果未知不在本页重试：写回调用到动作中心对账（?tab=unknown&act=），通知类不支持幂等的人工核对。
 * 关联动作：按动作类型的 sysId（mock/actionEvent.ts）过滤得到，点动作名进动作类型；不做写回的集成（短信、企业微信、顺丰、美团）显示用途。
 * 新建 / 编辑系统：配置抽屉（?edit=EXT-WMS / ?edit=new），保存前确认；测试调用：确认后发一次连通性请求，结果记入最近调用。
 * URL：?sys=EXT-WMS&result=失败&call=WMS-00012&edit=EXT-WMS
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { paginate, EMPTY } from '@/mock';
import { EXT_SYSTEMS, extCalls, type CallResult, type ExtCall, type ExtOp, type ExtSample, type ExtSystem } from '@/mock/extsys';
import { ACTION_TYPES, runsOf, type ActionType, type ActionRun } from '@/mock/actionEvent';
import { ACTIONS, RULE_CONFIRMER, OBJ_KIND_LABEL, type ActionRecord } from '@/mock/actions';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();

// 系统列表：直接包装共享的 EXT_SYSTEMS（本次会话里新建 / 编辑后，动作类型页的写回目标也能看到）
const systems = reactive(EXT_SYSTEMS);
const selected = computed<ExtSystem>(() => systems.find((s) => s.id === q('sys')) ?? systems[0]);
/** 关联动作：动作类型里 sysId 指向该系统的（名称与动作类型页完全一致） */
const linked = computed(() => ACTION_TYPES.filter((a) => a.sysId === selected.value.id));
const opsOf = (sys: ExtSystem): ExtOp[] => {
  const acts = ACTION_TYPES.filter((a) => a.sysId === sys.id);
  // 草稿动作没有执行过，不产生调用记录
  if (acts.length) return acts.filter((a) => a.status !== '草稿').map((a) => { const [method, path] = a.endpoint.split(' '); return { name: a.name, method, path, samples: samplesOf(a) }; });
  return (sys.usage?.ops ?? []).map((name) => ({ name }));
};
/**
 * 调用记录的对象 / 发起人取自动作执行记录：优先动作中心（mock/actions.ts，结果未知可按 ACT 编号跳去对账），
 * 动作中心未收录的动作类型取动作类型页的执行记录（只取已有结果的，不产生结果未知）。
 * 对象类型 = 动作类型的目标对象；发起人是人，或“规则 R-027 · 确认人 吴佳”（规则只暂存动作，由固定确认人提交；R-021 只告警）。
 */
const ruleActor = (rule: string) => `规则 ${rule} · 确认人 ${RULE_CONFIRMER[rule] ?? '—'}`;
const withType = (type: string, label: string) => (label.includes(type) ? label : `${type} ${label}`);
const fromCenter = (x: ActionRecord): ExtSample => ({
  obj: withType(OBJ_KIND_LABEL[x.objKind], x.objLabel), actor: x.source.ruleId ? ruleActor(x.source.ruleId) : x.initiator,
  runId: x.id, idemKey: x.idemKey, unknown: x.status === '结果未知',
  time: x.log.find((l) => l.text.startsWith('调用'))?.time ?? x.submittedAt,
});
const fromRun = (a: ActionType, x: ActionRun): ExtSample => ({
  obj: withType(a.target, x.objLabel.replace(/^分群 · /, '')), actor: x.ruleId ? ruleActor(x.ruleId) : x.initiator,
  runId: x.id, idemKey: x.idemKey, unknown: false,
});
const DONE = ['成功', '失败', '结果未知', '已对账', '执行中'];
function samplesOf(a: ActionType): ExtSample[] {
  const center = ACTIONS.filter((x) => x.action === a.name && DONE.includes(x.status));
  if (center.length) return center.map(fromCenter);
  return runsOf(a.id).filter((x) => DONE.includes(x.status)).map((x) => fromRun(a, x));
}
const actionCount = (sys: ExtSystem) => ACTION_TYPES.filter((a) => a.sysId === sys.id).length;
// 状态点：正常 / 停用 灰，降级 红（有失败调用）；橙色只留给 待确认 / 结果未知，不用绿、蓝
const statusBadge = (s: ExtSystem['status']) => (s === '降级' ? 'error' : 'default');
const resultText = (r: CallResult) => (r === '结果未知' ? '结果未知 · 待对账' : r);

// 调用记录：本页内可改（重试 / 核对后结果会变）
const overrides = ref<Record<string, Partial<ExtCall>>>({});
// 本页“测试调用”产生的记录（会话内），排在最前
const tests = ref<ExtCall[]>([]);
const calls = computed(() => [...tests.value.filter((c) => c.sysId === selected.value.id), ...extCalls(selected.value, opsOf(selected.value))].map((c) => ({ ...c, ...overrides.value[c.id] })));
const counts = computed(() => {
  const m: Record<string, number> = { 全部: calls.value.length, 成功: 0, 失败: 0, 结果未知: 0 };
  calls.value.forEach((c) => { m[c.result] += 1; });
  return m;
});
const result = computed({ get: () => q('result') || '全部', set: (v: string) => setQuery({ result: v === '全部' ? undefined : v }) });
const filtered = computed(() => calls.value.filter((c) => result.value === '全部' || c.result === result.value));
const SIZE = 10;
const page = ref(1);
watch([() => selected.value.id, result], () => { page.value = 1; });
const paged = computed(() => paginate(filtered.value, { page: page.value, size: SIZE }));
const tl = (r: CallResult) => (r === '成功' ? 'tl-normal' : r === '结果未知' ? 'tl-attention' : 'tl-failed');
const cols = [
  { title: '时间', key: 'time', width: 160 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 130, ellipsis: true },
  { title: '对象', key: 'target', width: 200, ellipsis: true },
  { title: '发起人', dataIndex: 'actor', key: 'actor', width: 130, ellipsis: true },
  { title: '幂等键', key: 'idemKey', width: 130, ellipsis: true },
  { title: '耗时', key: 'ms', width: 80, align: 'right' as const },
  { title: '尝试', key: 'attempt', width: 50, align: 'right' as const },
  { title: 'HTTP', key: 'http', width: 60, align: 'right' as const },
  { title: '结果', key: 'result', width: 150 },
  { title: '操作', key: 'op', width: 60 },
];

// 调用详情抽屉
const call = computed(() => calls.value.find((c) => c.id === q('call')) ?? null);
const openCall = (id: string) => setQuery({ call: id });
const closeCall = () => setQuery({ call: undefined });
const reqBody = (c: ExtCall) => JSON.stringify({ action: c.action, target: c.target, operator: c.actor, ...(c.idemKey ? { [selected.value.idemKey.split('（')[0]]: c.idemKey } : {}) }, null, 2);
const respBody = (c: ExtCall) => (c.http == null ? `（${selected.value.timeoutMs / 1000} 秒内没有收到响应）` : JSON.stringify(c.http === 200 ? { code: 0, msg: 'success' } : { code: c.http, msg: c.http === 429 ? '请求过于频繁' : c.http === 401 ? '签名校验失败' : '服务暂时不可用' }, null, 2));

const confirm = useConfirmStore();
/** 写回调用结果未知：不在本页重试，到动作中心按执行记录对账 */
const reconcileLink = (c: ExtCall) => ({ path: '/use/ops/actions', query: { tab: 'unknown', act: c.runId } });
async function markChecked(c: ExtCall) {
  const ok = await confirm.ask({
    title: '人工核对结果', target: `${c.action} · ${c.target}`, okText: '确认对方已处理', tag: '结果未知 · 待对账',
    fields: [{ label: '系统', value: `${selected.value.name}（${selected.value.vendor}）` }, { label: '原调用', value: `${c.id} · ${c.time}` }],
    impact: `${selected.value.name} 不支持幂等，从不重试。请先在对方系统确认这条请求已处理，再标记为成功；没处理的话请重新发起动作。`,
  });
  if (!ok) return;
  overrides.value[c.id] = { result: '成功', msg: '人工核对：对方已处理（王敏 16:30 标记）' };
  message.success('已标记为成功');
}

// 测试调用：只发连通性探测（不带业务数据、不写回），结果记入最近调用
const pad = (n: number) => String(n).padStart(2, '0');
const nowText = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const testing = ref(false);
async function testCall() {
  const sys = selected.value;
  const ok = await confirm.ask({
    title: '测试调用', target: `${sys.name}（${sys.vendor}）`, okText: '发送测试',
    fields: [{ label: '接口地址', value: sys.endpoint }, { label: '认证', value: sys.auth }, { label: '超时', value: `${sys.timeoutMs / 1000} 秒` }],
    impact: '只发送一次连通性探测（鉴权 + 心跳接口），不带业务数据、不会写回；结果记入下方“最近调用”，发起人记为你。',
    blocked: sys.status === '停用' ? '该系统已停用，先在“编辑”里改为正常再测试' : undefined,
  });
  if (!ok) return;
  testing.value = true;
  await new Promise((r) => setTimeout(r, 600));
  testing.value = false;
  const degraded = sys.status === '降级';
  const n = tests.value.filter((c) => c.sysId === sys.id).length + 1;
  const rec: ExtCall = {
    id: `${sys.id.slice(4)}-T${String(n).padStart(3, '0')}`, sysId: sys.id, time: nowText(), action: '测试调用（连通性）', target: '—',
    method: 'GET', path: '/health', idemKey: null, ms: degraded ? 3120 : 140 + (sys.timeoutMs % 97), http: degraded ? 502 : 200,
    result: degraded ? '失败' : '成功', attempt: 1, actor: '王敏（U010001）',
    msg: degraded ? '对方返回 502（网关错误），系统处于降级状态，请联系负责人' : 'OK · 鉴权通过，心跳正常',
  };
  tests.value.unshift(rec);
  setQuery({ result: undefined });
  page.value = 1;
  if (degraded) message.error(`测试失败：HTTP 502 · ${rec.ms} ms`);
  else message.success(`测试通过：HTTP 200 · ${rec.ms} ms`);
}

// 新建 / 编辑外部系统（?edit=EXT-WMS / ?edit=new）
const CATEGORIES = ['CRM', 'ERP', 'OMS', 'WMS', 'SMS', '物流', '通知', '渠道', '审批'];
const AUTHS = ['OAuth 2.0（客户端凭证）', 'AppID + AppSecret（OpenAPI）', 'mTLS + 服务令牌', 'API Key + HMAC 签名', 'AccessKey（RAM 子账号）', 'Token（服务账号）', 'CorpID + Secret', 'AppID + 签名'];
const OWNERS = ['王敏', '陈立军', '何静', '刘婷', '孙浩然', '吴佳'];
const blank = (): ExtSystem => ({
  id: '', name: '', vendor: '', category: 'ERP', endpoint: 'https://', auth: AUTHS[0], authDetail: '', idempotent: true, idemKey: 'X-Idempotency-Key',
  timeoutMs: 5000, maxRetry: 3, retry: '', rateLimit: '10 次/秒', owner: '王敏', status: '正常', env: '生产',
});
const editKey = computed(() => q('edit'));
const editing = computed(() => (editKey.value === 'new' ? null : systems.find((s) => s.id === editKey.value) ?? null));
const form = reactive<ExtSystem>(blank());
const formSnap = ref('');
watch(editKey, (k) => {
  if (!k) return;
  Object.assign(form, k === 'new' ? blank() : JSON.parse(JSON.stringify(editing.value ?? blank())));
  formSnap.value = JSON.stringify(form);
}, { immediate: true });
const formDirty = computed(() => !!editKey.value && JSON.stringify(form) !== formSnap.value);
useUnsavedGuard(formDirty);
const retryText = (f: ExtSystem) => (f.idempotent && f.maxRetry
  ? `外部明确失败时最多 ${f.maxRetry} 次；超时不重试，标记结果未知`
  : '不重试：外部明确失败转人工；超时标记结果未知，人工对账');
watch(() => form.idempotent, (v) => { if (!v) form.maxRetry = 0; });
const formErrors = computed(() => {
  const e: string[] = [];
  if (!form.name.trim()) e.push('请填写系统名称');
  else if (systems.some((s) => s.name === form.name.trim() && s.id !== editing.value?.id)) e.push('系统名称已存在');
  if (!form.vendor.trim()) e.push('请填写供应商');
  if (!/^https:\/\/[^\s/]+\.[^\s]+$/.test(form.endpoint.trim())) e.push('接口地址需以 https:// 开头');
  if (form.idempotent && !form.idemKey.trim()) e.push('支持幂等时需填写幂等键');
  if (!(form.timeoutMs >= 1000 && form.timeoutMs <= 60000)) e.push('超时需在 1～60 秒之间');
  return e;
});
const usedBy = computed(() => (editing.value ? ACTION_TYPES.filter((a) => a.sysId === editing.value!.id) : []));
const openEdit = (id: string) => setQuery({ edit: id, call: undefined });
async function closeEdit() {
  if (formDirty.value && !(await confirmDiscard())) return;
  formSnap.value = JSON.stringify(form);
  setQuery({ edit: undefined });
}
async function saveEdit() {
  const isNew = !editing.value;
  const before = editing.value;
  const diff = before ? (['name', 'endpoint', 'auth', 'timeoutMs', 'maxRetry', 'rateLimit', 'owner', 'status', 'idempotent'] as const)
    .filter((k) => before[k] !== form[k]).map((k) => ({ label: ({ name: '名称', endpoint: '接口地址', auth: '认证方式', timeoutMs: '超时', maxRetry: '最多重试', rateLimit: '限流', owner: '负责人', status: '状态', idempotent: '幂等' } as Record<string, string>)[k], value: `${String(before[k])} → ${String(form[k])}` })) : [];
  const ok = await confirm.ask({
    title: isNew ? '新建外部系统' : '保存外部系统配置', target: `${form.name || '未命名'}（${form.vendor || '—'}）`, okText: '保存',
    fields: isNew ? [{ label: '接口地址', value: form.endpoint }, { label: '环境', value: form.env }, { label: '负责人', value: form.owner }] : diff.length ? diff : [{ label: '修改', value: '无变化' }],
    impact: isNew ? '保存后可在动作类型的“写回目标”里选择该系统；凭证由负责人在密钥库录入，界面不回显。'
      : usedBy.value.length ? `影响 ${usedBy.value.length} 个动作类型（${usedBy.value.map((a) => a.name).join('、')}）的写回，保存后对新发起的动作立即生效。` : '没有动作类型写回该系统，保存后立即生效。',
    blocked: formErrors.value[0],
  });
  if (!ok) return;
  const next: ExtSystem = { ...JSON.parse(JSON.stringify(form)), name: form.name.trim(), vendor: form.vendor.trim(), endpoint: form.endpoint.trim(), retry: retryText(form), idemKey: form.idempotent ? form.idemKey : '-' };
  if (isNew) {
    next.id = `EXT-N${String(systems.filter((s) => s.id.startsWith('EXT-N')).length + 1).padStart(2, '0')}`;
    systems.push(next);
  } else Object.assign(before!, next);
  formSnap.value = JSON.stringify(form);
  setQuery({ edit: undefined, sys: next.id });
  message.success(isNew ? `已新建外部系统：${next.name}` : `已保存：${next.name}`);
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="openEdit('new')">新建外部系统</a-button></a-tooltip>
  </Teleport>
  <div class="ex">
    <div class="panel ex-list">
      <div class="panel-head">
        <span class="t-16 fw-600">外部系统</span>
        <span class="t-12 c-3">共 {{ systems.length }} 个</span>
      </div>
      <div class="ex-items">
        <div v-for="s in systems" :key="s.id" class="ex-item" :class="{ on: s.id === selected.id }" @click="setQuery({ sys: s.id, result: undefined, call: undefined })">
          <div class="ex-l1">
            <span class="ex-cat t-12">{{ s.category }}</span>
            <span class="t-14 fw-600 c-1 ex-ellip">{{ s.name }}</span>
            <a-badge :status="statusBadge(s.status)" :text="s.status" class="ex-badge" />
          </div>
          <div class="t-12 c-3 ex-l2">
            <span class="ex-ellip">{{ s.vendor }} · {{ s.env }}</span>
            <span>{{ actionCount(s) ? `${actionCount(s)} 个动作` : '无动作写回' }} · {{ s.idempotent ? '支持幂等' : '不支持幂等' }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="panel ex-detail">
      <div class="ex-head">
        <div class="ex-title">
          <span class="t-20 fw-600 c-1">{{ selected.name }}</span>
          <a-tag>{{ selected.category }}</a-tag>
          <a-tag>{{ selected.env }}</a-tag>
          <a-badge :status="statusBadge(selected.status)" :text="selected.status" />
          <span v-if="selected.status === '降级'" class="t-12 c-2">最近 6 次调用有失败 / 结果未知；结果未知不自动重试，需对账</span>
        </div>
        <a-space>
          <a-tooltip :title="reason"><a-button :disabled="readonly" :loading="testing" @click="testCall">测试调用</a-button></a-tooltip>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openEdit(selected.id)">编辑</a-button></a-tooltip>
        </a-space>
      </div>
      <div class="ex-cfg">
        <a-descriptions :column="3" bordered size="small" class="ex-desc">
          <a-descriptions-item label="接口地址" :span="2"><span class="num">{{ selected.endpoint }}</span></a-descriptions-item>
          <a-descriptions-item label="供应商">{{ selected.vendor }}</a-descriptions-item>
          <a-descriptions-item label="认证方式">{{ selected.auth }}</a-descriptions-item>
          <a-descriptions-item label="凭证" :span="2"><span class="num">{{ selected.authDetail }}</span></a-descriptions-item>
          <a-descriptions-item label="幂等">
            <span v-if="selected.idempotent">支持 · <span class="num">{{ selected.idemKey }}</span></span>
            <span v-else class="c-2">不支持（从不重试；结果未知需人工对账）</span>
          </a-descriptions-item>
          <a-descriptions-item label="超时"><span class="num">{{ selected.timeoutMs / 1000 }} 秒</span></a-descriptions-item>
          <a-descriptions-item label="限流">{{ selected.rateLimit }}</a-descriptions-item>
          <a-descriptions-item label="重试策略" :span="2">{{ selected.retry }}</a-descriptions-item>
          <a-descriptions-item label="负责人">{{ selected.owner }}</a-descriptions-item>
          <a-descriptions-item label="关联动作" :span="3">
            <template v-if="linked.length">
              <router-link v-for="a in linked" :key="a.id" :to="{ path: '/build/ontology/action-types', query: { action: a.id } }" class="ex-act">{{ a.name }}</router-link>
            </template>
            <span v-else-if="selected.usage" class="c-2">暂无关联动作类型 · {{ selected.usage.text }}</span>
            <span v-else class="c-3">暂无关联动作类型</span>
          </a-descriptions-item>
        </a-descriptions>
      </div>
      <div class="ex-calls-head">
        <span class="t-16 fw-600">最近调用</span>
        <a-radio-group v-model:value="result" size="small" button-style="solid">
          <a-radio-button v-for="k in ['全部', '成功', '失败', '结果未知']" :key="k" :value="k">{{ k }}（{{ counts[k] }}）</a-radio-button>
        </a-radio-group>
      </div>
      <div class="ex-table paged-scroll">
        <a-table size="small" row-key="id" :columns="cols" :data-source="paged.items" :pagination="false">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'time'" class="num">{{ record.time }}</span>
            <span v-else-if="column.key === 'target'" class="num">{{ record.target }}</span>
            <span v-else-if="column.key === 'idemKey'" class="num c-3">{{ record.idemKey ?? EMPTY }}</span>
            <span v-else-if="column.key === 'ms'" class="num">{{ record.ms }} ms</span>
            <span v-else-if="column.key === 'attempt'" class="num c-2">{{ record.attempt }}</span>
            <span v-else-if="column.key === 'http'" class="num" :class="record.http && record.http !== 200 ? 'tl-failed' : ''">{{ record.http ?? EMPTY }}</span>
            <span v-else-if="column.key === 'result'" class="ex-res" :class="tl(record.result)"><QuestionCircleOutlined v-if="record.result === '结果未知'" /><span v-else class="ex-dot" />{{ resultText(record.result) }}</span>
            <a v-else-if="column.key === 'op'" @click="openCall(record.id)">详情</a>
          </template>
        </a-table>
      </div>
      <AppPagination v-model:page="page" variant="simple" :total="filtered.length" :size="SIZE" unit="次调用" class="ex-pager" />
    </div>
  </div>

  <a-drawer :open="!!call" :width="560" :title="call ? `调用详情 · ${call.id}` : ''" @close="closeCall">
    <template v-if="call">
      <div class="ex-dr-status" :class="tl(call.result)"><QuestionCircleOutlined v-if="call.result === '结果未知'" /><span v-else class="ex-dot" /><span class="t-16 fw-600">{{ resultText(call.result) }}</span></div>
      <div class="t-12 c-2 ex-dr-msg">{{ call.msg }}</div>
      <div v-if="call.result === '结果未知'" class="t-12 c-2 ex-dr-note">
        处理方式：调用超时即标记为结果未知，系统不自动重试；{{ selected.idempotent ? '每 15 分钟按幂等键向对方查询一次处理结果，查到即自动更新' : '对方不支持幂等，无法自动查询' }}；仍查不到时，在动作中心“结果未知与对账”中人工对账。
      </div>
      <a-descriptions :column="1" bordered size="small" class="ex-desc">
        <a-descriptions-item label="系统">{{ selected.name }}（{{ selected.vendor }}）</a-descriptions-item>
        <a-descriptions-item label="动作">{{ call.action }}</a-descriptions-item>
        <a-descriptions-item label="对象"><span class="num">{{ call.target }}</span></a-descriptions-item>
        <a-descriptions-item label="发起人">{{ call.actor }}</a-descriptions-item>
        <a-descriptions-item v-if="call.runId" label="执行记录"><span class="num">{{ call.runId }}</span></a-descriptions-item>
        <a-descriptions-item label="时间"><span class="num">{{ call.time }}</span></a-descriptions-item>
        <a-descriptions-item label="请求"><span class="num">{{ call.method }} {{ call.path }}</span></a-descriptions-item>
        <a-descriptions-item label="幂等键"><span class="num">{{ call.idemKey ?? '不支持' }}</span></a-descriptions-item>
        <a-descriptions-item label="耗时 / 尝试"><span class="num">{{ call.ms }} ms · 第 {{ call.attempt }} 次</span></a-descriptions-item>
      </a-descriptions>
      <div class="t-14 fw-600 c-1 ex-dr-sub">请求体</div>
      <pre class="ex-pre num">{{ reqBody(call) }}</pre>
      <div class="t-14 fw-600 c-1 ex-dr-sub">响应</div>
      <pre class="ex-pre num">{{ respBody(call) }}</pre>
    </template>
    <template v-if="call && call.result === '结果未知'" #footer>
      <div class="ex-foot">
        <router-link v-if="call.runId" :to="reconcileLink(call)"><a-button type="link">去动作中心对账</a-button></router-link>
        <a-tooltip v-else :title="reason">
          <a-button type="primary" :disabled="readonly" @click="markChecked(call)">人工核对</a-button>
        </a-tooltip>
      </div>
    </template>
  </a-drawer>

  <a-drawer :open="!!editKey" :width="560" :title="editing ? `编辑外部系统 · ${editing.name}` : '新建外部系统'" @close="closeEdit">
    <a-form layout="vertical" class="ex-form">
      <div class="ex-row">
        <a-form-item label="系统名称" required class="ex-f2"><a-input v-model:value="form.name" placeholder="例如：仓储 WMS" :maxlength="20" /></a-form-item>
        <a-form-item label="类别" class="ex-f1"><a-select v-model:value="form.category" :options="CATEGORIES.map((v) => ({ value: v, label: v }))" /></a-form-item>
      </div>
      <div class="ex-row">
        <a-form-item label="供应商 / 产品" required class="ex-f2"><a-input v-model:value="form.vendor" placeholder="例如：富勒 FLUX" /></a-form-item>
        <a-form-item label="环境" class="ex-f1"><a-select v-model:value="form.env" :options="['生产', '测试'].map((v) => ({ value: v, label: v }))" /></a-form-item>
      </div>
      <a-form-item label="接口地址" required><a-input v-model:value="form.endpoint" class="num" /></a-form-item>
      <div class="ex-row">
        <a-form-item label="认证方式" class="ex-f2"><a-select v-model:value="form.auth" :options="AUTHS.map((v) => ({ value: v, label: v }))" /></a-form-item>
        <a-form-item label="负责人" class="ex-f1"><a-select v-model:value="form.owner" :options="OWNERS.map((v) => ({ value: v, label: v }))" /></a-form-item>
      </div>
      <a-form-item label="凭证说明" extra="只写标识（如 AppKey、证书名），密钥在密钥库录入，界面不回显"><a-input v-model:value="form.authDetail" /></a-form-item>
      <div class="ex-row">
        <a-form-item label="幂等" class="ex-f1"><a-switch v-model:checked="form.idempotent" checked-children="支持" un-checked-children="不支持" /></a-form-item>
        <a-form-item label="幂等键" class="ex-f2"><a-input v-model:value="form.idemKey" :disabled="!form.idempotent" /></a-form-item>
      </div>
      <div class="ex-row">
        <a-form-item label="超时（毫秒）" class="ex-f1"><a-input-number v-model:value="form.timeoutMs" :min="1000" :max="60000" :step="1000" class="ex-w" /></a-form-item>
        <a-form-item label="明确失败时最多重试" class="ex-f1"><a-input-number v-model:value="form.maxRetry" :min="0" :max="5" :disabled="!form.idempotent" class="ex-w" /></a-form-item>
        <a-form-item label="限流" class="ex-f1"><a-input v-model:value="form.rateLimit" /></a-form-item>
      </div>
      <a-form-item label="状态"><a-radio-group v-model:value="form.status" :options="['正常', '降级', '停用'].map((v) => ({ value: v, label: v }))" /></a-form-item>
      <div class="t-12 c-3">重试规则：{{ retryText(form) }}</div>
      <div v-if="usedBy.length" class="t-12 c-2 ex-used">写回到该系统的动作类型：{{ usedBy.map((a) => a.name).join('、') }}</div>
    </a-form>
    <template #footer>
      <div class="ex-foot ex-foot-gap">
        <span v-if="formErrors.length" class="t-12 tl-failed ex-err">{{ formErrors[0] }}</span>
        <a-button @click="closeEdit">取消</a-button>
        <a-button type="primary" @click="saveEdit">保存</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.ex { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.ex-list { width: 320px; flex: none; }
.ex-items { flex: 1; min-height: 0; overflow: hidden; }
.ex-item { padding: 10px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.ex-item:hover { background: var(--g2); }
.ex-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.ex-l1 { display: flex; align-items: center; gap: 8px; min-width: 0; }
.ex-l2 { display: flex; justify-content: space-between; margin-top: 2px; }
.ex-cat { flex: none; min-width: 36px; text-align: center; border: var(--border); border-radius: 4px; color: var(--g8); line-height: 18px; padding: 0 4px; }
.ex-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ex-badge { margin-left: auto; flex: none; }
.ex-detail { flex: 1; min-width: 0; }
.ex-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 10px; }
.ex-title { display: flex; align-items: center; gap: 10px; }
.ex-cfg { padding: 0 20px; flex: none; }
.ex-desc :deep(.ant-descriptions-item-label) { width: 120px; }
.ex-calls-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 8px; flex: none; }
.ex-table { flex: 1; min-height: 0; padding: 0 20px; }
.ex-pager { padding: 6px 20px 8px; }
.ex-res { display: inline-flex; align-items: center; gap: 6px; }
.ex-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.ex-dr-status { display: flex; align-items: center; gap: 8px; }
.ex-dr-msg { margin: 4px 0 12px 16px; }
.ex-dr-note { margin: -4px 0 12px 16px; padding: 8px 12px; background: var(--g2); border-radius: var(--radius); }
.ex-dr-sub { margin: 16px 0 6px; }
.ex-pre { background: var(--g2); border: var(--border); border-radius: var(--radius); padding: 8px 12px; margin: 0; color: var(--g9); white-space: pre-wrap; }
.ex-foot { display: flex; justify-content: flex-end; }
.ex-foot-gap { gap: 8px; align-items: center; }
.ex-err { margin-right: auto; }
.ex-act { margin-right: 12px; color: var(--brand); }
.ex-row { display: flex; gap: 12px; }
.ex-f1 { flex: 1; min-width: 0; }
.ex-f2 { flex: 2; min-width: 0; }
.ex-w { width: 100%; }
.ex-used { margin-top: 6px; }
.ex-form :deep(.ant-form-item) { margin-bottom: 12px; }
</style>
