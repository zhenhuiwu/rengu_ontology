<script setup lang="ts">
/**
 * 模型与系统配置（PDE工程师）。
 * 左侧分组：租户信息、大模型连接、通知渠道、安全策略；右侧是该分组的表单。
 * 大模型连接：连接列表（新建 / 编辑抽屉 ?llm=new|LLM-01，密钥只在填写当次可见、之后只显示掩码；测试连接要确认，模拟返回延迟和模型版本）、
 * 每个用途的默认连接、数据出域策略（默认只发送字段名和口径；发送明细只允许私有化部署）。只在前端配置，不发网络请求。
 * 所有分组共用一份草稿：有修改时底部出现保存条（放弃修改 / 保存，保存前确认），离开页面受 useUnsavedGuard 保护；切换分组不丢修改。
 * 通知渠道的配置在抽屉里改（确定后写进草稿，仍需保存）；发送测试要确认。历史版本只读。
 * URL：?group=tenant|llm|notify|security&channel=email&llm=LLM-01
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import InfoTip from '@/components/InfoTip.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { ADMIN_DEPTS } from '@/mock';
import {
  SYSTEM_SETTINGS, NOTIFY_EVENTS, CHANNEL_ORDER, SETTINGS_UPDATED, LLM_USAGES, LLM_KINDS, EGRESS_OPTIONS, MASK_FIELDS,
  type SystemSettings, type NotifyChannel, type ChannelId, type LlmConnection, type LlmUsage, type LlmTest, type LlmKind, type LlmDeploy, type EgressPolicy,
} from '@/mock/settings';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();
const confirm = useConfirmStore();
const nowStr = '2026-10-25 17:20';
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

type GroupKey = 'tenant' | 'llm' | 'notify' | 'security';
const GROUPS: { key: GroupKey; title: string; desc: string }[] = [
  { key: 'tenant', title: '租户信息', desc: '名称、组织、时区与口径' },
  { key: 'llm', title: '大模型连接', desc: '模型接口、用途分配与数据出域' },
  { key: 'notify', title: '通知渠道', desc: '渠道配置与事件通知规则' },
  { key: 'security', title: '安全策略', desc: '密码、登录、会话与数据' },
];
const group = computed<GroupKey>(() => (GROUPS.some((g) => g.key === q('group')) ? (q('group') as GroupKey) : 'tenant'));
const groupTitle = computed(() => GROUPS.find((g) => g.key === group.value)!.title);

// 草稿：所有分组共用
const saved = ref<SystemSettings>(clone(SYSTEM_SETTINGS));
const draft = reactive<SystemSettings>(clone(SYSTEM_SETTINGS));
const updated = reactive({ ...SETTINGS_UPDATED });

const TENANT_LABEL: Record<string, string> = { name: '租户全称', shortName: '简称', defaultOrg: '默认组织', timezone: '时区', fiscalStart: '财年起始月', weekStart: '每周开始于', dataCutoff: '数据截至时间', contact: '运维联系邮箱' };
const SEC_LABEL: Record<string, string> = { pwdMin: '密码最小长度', pwdRules: '密码必须包含', pwdExpireDays: '密码有效期', lockAfter: '连续失败锁定', lockMinutes: '锁定时长', sessionMinutes: '会话超时', mfa: '双因素认证', ipAllow: '登录 IP 白名单', watermark: '页面水印', exportApproval: '导出需审批', auditKeepDays: '审计日志保留' };
const chName = (id: ChannelId) => draft.channels.find((c) => c.id === id)!.name;
const show = (k: string, v: unknown): string => {
  if (typeof v === 'boolean') return v ? '开启' : '关闭';
  if (Array.isArray(v)) return v.join('、') || '无';
  if (k === 'pwdExpireDays') return v ? `${v} 天` : '永不过期';
  if (k === 'lockAfter') return `${v} 次`;
  if (k === 'lockMinutes' || k === 'sessionMinutes') return `${v} 分钟`;
  if (k === 'auditKeepDays') return `${v} 天`;
  if (k === 'fiscalStart') return `${v} 月`;
  if (k === 'ipAllow') return String(v).trim() ? `${String(v).trim().split(/\s+/).length} 条` : '不限制';
  return String(v);
};

interface Change { group: GroupKey; label: string; from: string; to: string }
const changes = computed<Change[]>(() => {
  const out: Change[] = [];
  const s = saved.value;
  for (const k of Object.keys(TENANT_LABEL)) {
    const a = (s.tenant as unknown as Record<string, unknown>)[k]; const b = (draft.tenant as unknown as Record<string, unknown>)[k];
    if (JSON.stringify(a) !== JSON.stringify(b)) out.push({ group: 'tenant', label: TENANT_LABEL[k], from: show(k, a), to: show(k, b) });
  }
  for (const c of draft.llm.connections) {
    const o = s.llm.connections.find((x) => x.id === c.id);
    if (!o) { out.push({ group: 'llm', label: `新建连接 · ${c.name}`, from: '—', to: `${c.kind} · ${c.model}` }); continue; }
    if (o.enabled !== c.enabled) out.push({ group: 'llm', label: `${c.name} 状态`, from: o.enabled ? '启用' : '停用', to: c.enabled ? '启用' : '停用' });
    const keys = ['name', 'kind', 'deploy', 'baseUrl', 'model', 'keyMask', 'timeoutSec', 'maxConcurrency', 'usages'] as const;
    const diff = keys.filter((k) => JSON.stringify(o[k]) !== JSON.stringify(c[k]));
    if (diff.length) out.push({ group: 'llm', label: `${o.name} 配置`, from: '原配置', to: diff.map((k) => LLM_FIELD[k]).join('、') + ' 已修改' });
  }
  for (const o of s.llm.connections) if (!draft.llm.connections.some((c) => c.id === o.id)) out.push({ group: 'llm', label: `删除连接 · ${o.name}`, from: `${o.kind} · ${o.model}`, to: '—' });
  for (const u of LLM_USAGES) {
    const a = s.llm.defaults[u.key]; const b = draft.llm.defaults[u.key];
    if (a !== b) out.push({ group: 'llm', label: `默认连接 · ${u.key}`, from: connName(a, s.llm.connections), to: connName(b) });
  }
  if (s.llm.egress.policy !== draft.llm.egress.policy) out.push({ group: 'llm', label: '数据出域策略', from: egressLabel(s.llm.egress.policy), to: egressLabel(draft.llm.egress.policy) });
  if (draft.llm.egress.policy === 'masked' && (s.llm.egress.sampleRows !== draft.llm.egress.sampleRows || s.llm.egress.maskFields.slice().sort().join() !== draft.llm.egress.maskFields.slice().sort().join()))
    out.push({ group: 'llm', label: '脱敏样例设置', from: `${s.llm.egress.sampleRows} 行 · 脱敏 ${s.llm.egress.maskFields.join('、') || '无'}`, to: `${draft.llm.egress.sampleRows} 行 · 脱敏 ${draft.llm.egress.maskFields.join('、') || '无'}` });
  for (const c of draft.channels) {
    const o = s.channels.find((x) => x.id === c.id)!;
    if (o.enabled !== c.enabled) out.push({ group: 'notify', label: `${c.name}渠道`, from: show('', o.enabled), to: show('', c.enabled) });
    if (o.target !== c.target || o.receivers !== c.receivers || o.secret !== c.secret) out.push({ group: 'notify', label: `${c.name}配置`, from: '原配置', to: '已修改' });
  }
  for (const e of NOTIFY_EVENTS) {
    const a = s.routes[e.key].slice().sort().join(); const b = draft.routes[e.key].slice().sort().join();
    if (a !== b) out.push({ group: 'notify', label: `通知规则 · ${e.name}`, from: routeText(s.routes[e.key]), to: routeText(draft.routes[e.key]) });
  }
  for (const k of Object.keys(SEC_LABEL)) {
    const a = (s.security as unknown as Record<string, unknown>)[k]; const b = (draft.security as unknown as Record<string, unknown>)[k];
    if (JSON.stringify(a) !== JSON.stringify(b)) out.push({ group: 'security', label: SEC_LABEL[k], from: show(k, a), to: show(k, b) });
  }
  return out;
});
const LLM_FIELD: Record<string, string> = { name: '名称', kind: '接口类型', deploy: '部署方式', baseUrl: '地址', model: '模型名', keyMask: '密钥', timeoutSec: '超时', maxConcurrency: '最大并发', usages: '用途' };
function connName(id: string, list: LlmConnection[] = draft.llm.connections) { return list.find((c) => c.id === id)?.name ?? '未设置'; }
const egressLabel = (v: string) => EGRESS_OPTIONS.find((o) => o.value === v)?.label ?? v;
const routeText = (ids: ChannelId[]) => CHANNEL_ORDER.filter((id) => ids.includes(id)).map(chName).join('、') || '不通知';
const dirty = computed(() => changes.value.length > 0);
const dirtyOf = (g: GroupKey) => changes.value.filter((c) => c.group === g).length;
const guard = useUnsavedGuard(dirty);

// 校验
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CIDR_RE = /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$/;
const badIps = computed(() => draft.security.ipAllow.split(/\s+/).filter((x) => x && !CIDR_RE.test(x)));
const llmErrors = computed(() => {
  const e: string[] = [];
  const L = draft.llm;
  for (const u of LLM_USAGES) {
    const c = L.connections.find((x) => x.id === L.defaults[u.key]);
    if (!c) e.push(`用途「${u.key}」没有默认连接`);
    else if (!c.enabled) e.push(`用途「${u.key}」的默认连接「${c.name}」已停用`);
    else if (!c.usages.includes(u.key)) e.push(`连接「${c.name}」不支持用途「${u.key}」`);
  }
  if (L.egress.policy === 'detail') {
    // 已启用、且可能拿到查询结果（问数解析 / 结果解释）的连接都必须是私有化部署
    const pub = L.connections.filter((c) => c.enabled && c.deploy === '公有云' && c.usages.some((u) => u !== '向量化')).map((c) => c.name);
    if (pub.length) e.push(`发送明细只允许私有化部署的连接：「${pub.join('、')}」是公有云，先停用或改用脱敏样例`);
  }
  if (L.egress.policy === 'masked') {
    if (!(L.egress.sampleRows >= 1 && L.egress.sampleRows <= 20)) e.push('脱敏样例行数需在 1～20 之间');
    if (!L.egress.maskFields.includes('手机号') || !L.egress.maskFields.includes('身份证号')) e.push('脱敏样例必须脱敏手机号和身份证号');
  }
  return e;
});
const errors = computed(() => {
  const e: string[] = [];
  if (!draft.tenant.name.trim()) e.push('租户全称不能为空');
  if (!draft.tenant.shortName.trim()) e.push('简称不能为空');
  if (!EMAIL_RE.test(draft.tenant.contact)) e.push('运维联系邮箱格式不对');
  if (badIps.value.length) e.push(`IP 白名单有 ${badIps.value.length} 条格式不对`);
  if (!draft.security.pwdRules.length) e.push('密码至少要求包含一类字符');
  e.push(...llmErrors.value);
  for (const ev of NOTIFY_EVENTS) if (ev.level === '紧急' && !draft.routes[ev.key].some((id) => id !== 'inbox' && draft.channels.find((c) => c.id === id)!.enabled)) e.push(`紧急事件「${ev.name}」至少要有一个站内消息以外的已启用渠道`);
  return e;
});

async function save() {
  const list = changes.value;
  const groups = [...new Set(list.map((c) => c.group))];
  const impact: string[] = [];
  if (groups.includes('security')) impact.push('安全策略：会话超时、IP 白名单立即生效（不在白名单内的已登录会话会被退出）；密码规则在用户下次改密码时生效。');
  if (groups.includes('notify')) impact.push('通知：新规则对之后产生的事件生效，已发出的通知不受影响。');
  if (groups.includes('llm')) impact.push('大模型连接：新开始的问数会话使用新的连接、默认用途和出域策略，进行中的会话不受影响。');
  if (groups.includes('tenant')) impact.push('租户信息：时区、财年、数据截至时间会影响报表口径，下次计算时生效。');
  impact.push('修改会记入审计日志。');
  const shown = list.slice(0, 6).map((c) => ({ label: c.label, value: `${c.from} → ${c.to}` }));
  if (list.length > 6) shown.push({ label: '其他', value: `另有 ${list.length - 6} 处修改` });
  const ok = await confirm.ask({
    title: '保存系统配置', target: `租户「${draft.tenant.shortName || saved.value.tenant.shortName}」· ${list.length} 处修改`, okText: '保存',
    fields: shown, impact: impact.join(''),
    blocked: errors.value.length ? `请先改正：${errors.value.join('；')}` : undefined,
  });
  if (!ok) return;
  for (const g of groups) updated[g] = `${nowStr} · 王敏`;
  saved.value = clone(draft as SystemSettings);
  message.success(`已保存 ${list.length} 处修改`);
}
async function discard() {
  if (!(await confirmDiscard())) return;
  Object.assign(draft, clone(saved.value));
}
const selectGroup = (g: GroupKey) => setQuery({ group: g === 'tenant' ? undefined : g, channel: undefined, llm: undefined });

// 通知渠道
const channel = computed(() => draft.channels.find((c) => c.id === q('channel')) ?? null);
const chForm = reactive({ target: '', receivers: '', secret: '' });
const chSnap = ref('');
watch(channel, (c) => {
  if (!c) return;
  Object.assign(chForm, { target: c.target, receivers: c.receivers, secret: c.secret ?? '' });
  chSnap.value = JSON.stringify(chForm);
}, { immediate: true });
const chDirty = computed(() => !!channel.value && JSON.stringify(chForm) !== chSnap.value);
async function closeChannel() {
  if (chDirty.value && !(await confirmDiscard())) return;
  setQuery({ channel: undefined });
}
function applyChannel() {
  const c = channel.value; if (!c) return;
  c.target = chForm.target.trim(); c.receivers = chForm.receivers.trim();
  if (c.secret !== undefined) c.secret = chForm.secret;
  chSnap.value = JSON.stringify(chForm);
  setQuery({ channel: undefined });
  message.info(`${c.name}配置已写入草稿，保存后生效`);
}
async function testSend(c: NotifyChannel) {
  const isSms = c.id === 'sms';
  const ok = await confirm.ask({
    title: '发送测试通知', target: `${c.name}（${c.via}）`, okText: '发送',
    fields: [{ label: '发送到', value: c.target }, { label: '接收人', value: c.id === 'inbox' ? '王敏（当前用户）' : c.receivers }],
    impact: isSms
      ? '会真实发送一条短信并计费。短信平台不支持幂等：外部明确失败转人工，超时标记“结果未知 · 待对账”，不会重发。'
      : c.extId ? '会真实发送一条测试消息。该渠道不支持幂等：超时标记“结果未知 · 待对账”，不会重发。' : '会真实发送一条测试消息，内容标明“测试”。',
    blocked: !c.enabled ? '渠道未启用，启用并保存后才能发送测试' : chDirty.value ? '配置有未确定的修改，先点“确定”并保存后再测试' : undefined,
  });
  if (!ok) return;
  c.lastTest = nowStr; c.lastResult = '成功';
  saved.value.channels.find((x) => x.id === c.id)!.lastTest = nowStr;
  saved.value.channels.find((x) => x.id === c.id)!.lastResult = '成功';
  message.success(`测试通知已发送：${c.name}`);
}
const enabledOf = (id: ChannelId) => draft.channels.find((c) => c.id === id)!.enabled;
function toggleRoute(ev: string, id: ChannelId, on: boolean) {
  const r = draft.routes[ev];
  draft.routes[ev] = on ? CHANNEL_ORDER.filter((x) => x === id || r.includes(x)) : r.filter((x) => x !== id);
}
const chCols = [
  { title: '渠道', key: 'name', width: 120 },
  { title: '接入方式', key: 'via', width: 150, ellipsis: true },
  { title: '启用', key: 'enabled', width: 70 },
  { title: '发送目标', key: 'target', ellipsis: true },
  { title: '接收人', key: 'receivers', width: 220, ellipsis: true },
  { title: '最近测试', key: 'test', width: 190 },
  { title: '操作', key: 'op', width: 130 },
];
const evCols = computed(() => [
  { title: '事件', key: 'event', width: 360 },
  { title: '级别', key: 'level', width: 80 },
  ...CHANNEL_ORDER.map((id) => ({ title: chName(id), key: id, width: 100, align: 'center' as const })),
]);

// 大模型连接
const llmCols = [
  { title: '连接', key: 'name', width: 150, ellipsis: true },
  { title: '类型 / 部署', key: 'kind', width: 150, ellipsis: true },
  { title: '地址', key: 'url', ellipsis: true },
  { title: '模型', key: 'model', width: 150, ellipsis: true },
  { title: '用途', key: 'usages', width: 200 },
  { title: '超时 / 并发', key: 'limits', width: 110 },
  { title: '状态', key: 'status', width: 80 },
  { title: '最近测试', key: 'test', width: 190, ellipsis: true },
  { title: '操作', key: 'op', width: 200 },
];
const defaultsOf = (id: string) => LLM_USAGES.filter((u) => draft.llm.defaults[u.key] === id).map((u) => u.key);
const candidates = (u: LlmUsage) => draft.llm.connections.filter((c) => c.usages.includes(u));
const tlTest = (t?: LlmTest) => (!t ? 'c-3' : t.ok ? 'c-3' : 'tl-failed');
const testing = ref('');
const KNOWN_VERSION: Record<string, string> = {
  'qwen2.5-72b-instruct': 'Qwen2.5-72B-Instruct-GPTQ-Int4 · vLLM 0.6.3', 'bge-m3': 'bge-m3 · 1024 维 · TEI 1.5.0', 'qwen-max': 'qwen-max-2025-01-25',
  'qwen2.5-32b-instruct': 'Qwen2.5-32B-Instruct-AWQ · vLLM 0.6.3', 'deepseek-v3': 'DeepSeek-V3-0324 · SGLang 0.4',
};
const hash = (str: string) => [...str].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7);
/** 模拟测试：不发请求，按部署方式、地址算一个稳定的延迟；地址里带 .invalid / 127.0.0.1 视为连不上 */
function simulateTest(c: Pick<LlmConnection, 'deploy' | 'baseUrl' | 'model' | 'timeoutSec'>): LlmTest {
  const at = nowStr;
  if (/\.invalid\b|127\.0\.0\.1|localhost/.test(c.baseUrl)) return { at, ok: false, latencyMs: c.timeoutSec * 1000, version: '—', msg: `${c.timeoutSec} 秒内没有响应（连接超时）` };
  const base = c.deploy === '私有化部署' ? (c.model.includes('bge') ? 30 : 320) : 860;
  return { at, ok: true, latencyMs: base + (hash(c.baseUrl + c.model) % 180), version: KNOWN_VERSION[c.model] ?? `${c.model} · 由 /v1/models 返回` };
}
async function runTest(c: Pick<LlmConnection, 'name' | 'kind' | 'deploy' | 'baseUrl' | 'model' | 'timeoutSec' | 'enabled'>, opts: { blocked?: string } = {}): Promise<LlmTest | null> {
  const ok = await confirm.ask({
    title: '测试连接', target: `${c.name || '未命名连接'}（${c.kind} · ${c.deploy}）`, okText: '开始测试',
    fields: [{ label: '地址', value: c.baseUrl }, { label: '模型名', value: c.model || '—' }, { label: '超时', value: `${c.timeoutSec} 秒` }],
    impact: '发送一条固定的测试提示词（“请回复 OK”），不带任何业务字段或数据；记录延迟和服务端返回的模型版本。公有云连接会产生一次很少的调用费用。',
    blocked: opts.blocked ?? (!c.enabled ? '连接已停用，先启用再测试' : undefined),
  });
  if (!ok) return null;
  testing.value = c.name;
  await new Promise((r) => setTimeout(r, 300));
  testing.value = '';
  const t = simulateTest(c);
  if (t.ok) message.success(`连接正常 · 延迟 ${t.latencyMs} ms · 模型版本 ${t.version}`);
  else message.error(`连接失败：${t.msg}`);
  return t;
}
async function testConn(c: LlmConnection) {
  const t = await runTest(c);
  if (!t) return;
  c.lastTest = t;
  const sv = saved.value.llm.connections.find((x) => x.id === c.id);
  if (sv) sv.lastTest = clone(t);
}
function toggleConn(c: LlmConnection) {
  c.enabled = !c.enabled;
  const used = defaultsOf(c.id);
  if (!c.enabled && used.length) message.warning(`「${c.name}」是 ${used.join('、')} 的默认连接，请先换一个默认连接，否则不能保存`);
}
async function removeConn(c: LlmConnection) {
  const used = defaultsOf(c.id);
  const ok = await confirm.ask({
    title: '删除大模型连接', target: c.name, okText: '删除', danger: true,
    fields: [{ label: '地址', value: c.baseUrl }, { label: '模型名', value: c.model }, { label: '用途', value: c.usages.join('、') }],
    impact: '删除写入草稿，保存后生效；密钥一并删除，不能恢复。',
    blocked: used.length ? `它是「${used.join('、')}」的默认连接，先在“用途默认连接”里换成其他连接再删除` : undefined,
  });
  if (!ok) return;
  draft.llm.connections = draft.llm.connections.filter((x) => x.id !== c.id);
  message.info(`已删除「${c.name}」（草稿），保存后生效`);
}

// 出域策略：选“发送明细”先确认会发给哪些连接；选“脱敏样例”时补齐必须脱敏的字段
async function pickEgress(v: EgressPolicy) {
  const L = draft.llm;
  if (v === 'detail' && L.egress.policy !== 'detail') {
    const targets = L.connections.filter((c) => c.enabled && c.usages.some((u) => u !== '向量化'));
    const pubTargets = targets.filter((c) => c.deploy === '公有云');
    const ok = await confirm.ask({
      title: '改为发送明细数据', target: !targets.length ? '没有可用的连接' : targets.every((c) => c.deploy === '私有化部署') ? `将发送给 ${targets.map((c) => c.id).join('、')}（私有化）` : `将发送给 ${targets.map((c) => `${c.id}（${c.deploy === '私有化部署' ? '私有化' : '公有云'}）`).join('、')}`,
      okText: '改为发送明细', danger: true,
      blocked: pubTargets.length ? `明细数据只允许发送给私有化部署的连接：${pubTargets.map((c) => c.id).join('、')} 为公有云，先停用或改用脱敏样例` : undefined,
      fields: targets.map((c) => ({ label: c.id, value: `${c.name} · ${c.baseUrl}` })),
      impact: '风险：查询结果明细（含门店、会员、订单等业务数据）会离开平台发给上述模型；只允许私有化部署的连接，保存后才生效并记入审计日志。',
    });
    if (!ok) return;
  }
  if (v === 'masked') for (const f of ['手机号', '身份证号']) if (!L.egress.maskFields.includes(f)) L.egress.maskFields.push(f);
  L.egress.policy = v;
}

// 连接抽屉 ?llm=new|LLM-01
const llmKey = computed(() => (group.value === 'llm' ? q('llm') : ''));
const llmConn = computed(() => (llmKey.value && llmKey.value !== 'new' ? draft.llm.connections.find((c) => c.id === llmKey.value) ?? null : null));
interface LlmForm { name: string; kind: LlmKind; deploy: LlmDeploy; baseUrl: string; model: string; key: string; changeKey: boolean; timeoutSec: number; maxConcurrency: number; usages: LlmUsage[]; enabled: boolean }
const blankLlm = (): LlmForm => ({ name: '', kind: 'OpenAI 兼容', deploy: '私有化部署', baseUrl: 'http://', model: '', key: '', changeKey: true, timeoutSec: 60, maxConcurrency: 8, usages: ['问数解析'], enabled: true });
const lf = reactive<LlmForm>(blankLlm());
const lfSnap = ref('');
const lfTest = ref<LlmTest | null>(null);
// 测试结果只对测试时的 地址 / 模型 / 密钥 / 接口类型 有效；这几项改了就作废，不能写进连接
const testSig = () => JSON.stringify([lf.kind, lf.baseUrl.trim(), lf.model.trim(), lf.changeKey ? lf.key : '原密钥']);
const lfTestSig = ref('');
const lfStale = computed(() => !!lfTest.value && testSig() !== lfTestSig.value);
// 新建时字段没碰过、也没点过确定，不显示红字
const lfTried = ref(false);
const touched = reactive<Record<string, boolean>>({});
watch(llmKey, (k) => {
  lfTried.value = false;
  for (const f of Object.keys(touched)) delete touched[f];
  if (!k) { lf.key = ''; return; }  // 抽屉关掉立即清掉明文密钥
  const c = llmConn.value;
  Object.assign(lf, c ? { name: c.name, kind: c.kind, deploy: c.deploy, baseUrl: c.baseUrl, model: c.model, key: '', changeKey: false, timeoutSec: c.timeoutSec, maxConcurrency: c.maxConcurrency, usages: [...c.usages], enabled: c.enabled } : blankLlm());
  lfSnap.value = JSON.stringify(lf);
  lfTest.value = c?.lastTest ? { ...c.lastTest } : null;
  lfTestSig.value = testSig();
}, { immediate: true });
// 切到 Ollama：不需要密钥，立即清掉已填的明文；原密钥在点“确定”后清除
watch(() => lf.kind, (k) => { if (k === 'Ollama') { lf.key = ''; } });
const lfDirty = computed(() => !!llmKey.value && JSON.stringify(lf) !== lfSnap.value);
const needKey = computed(() => lf.kind !== 'Ollama');
const lfErrors = computed(() => {
  const e: string[] = [];
  if (!lf.name.trim()) e.push('请填写名称');
  else if (draft.llm.connections.some((c) => c.name === lf.name.trim() && c.id !== llmConn.value?.id)) e.push('名称已存在');
  if (!/^https?:\/\/[^\s/]+(\/\S*)?$/.test(lf.baseUrl.trim()) || /^https?:\/\/$/.test(lf.baseUrl.trim())) e.push('地址需以 http:// 或 https:// 开头');
  else if (lf.deploy === '公有云' && !lf.baseUrl.startsWith('https://')) e.push('公有云地址必须使用 https');
  if (!lf.model.trim()) e.push('请填写模型名');
  if (needKey.value && lf.changeKey && lf.key.trim().length < 8) e.push(llmConn.value?.keyMask ? '新密钥至少 8 位；不更换请点“保留原密钥”' : '请填写密钥（至少 8 位）');
  if (!(lf.timeoutSec >= 5 && lf.timeoutSec <= 300)) e.push('超时需在 5～300 秒之间');
  if (!(lf.maxConcurrency >= 1 && lf.maxConcurrency <= 64)) e.push('最大并发需在 1～64 之间');
  if (!lf.usages.length) e.push('至少选一个用途');
  const c = llmConn.value;
  if (c) {
    const lost = defaultsOf(c.id).filter((u) => !lf.usages.includes(u as LlmUsage));
    if (lost.length) e.push(`它是「${lost.join('、')}」的默认连接，不能去掉该用途`);
    if (!lf.enabled && defaultsOf(c.id).length) e.push('默认连接不能停用');
  }
  return e;
});
const FIELD_OF: [string, string][] = [['名称', 'name'], ['地址', 'baseUrl'], ['公有云地址', 'baseUrl'], ['模型名', 'model'], ['密钥', 'key']];
const lfShownErrors = computed(() => (lfTried.value || llmConn.value ? lfErrors.value : lfErrors.value.filter((e) => {
  const f = FIELD_OF.find(([w]) => e.includes(w))?.[1];
  return !f || touched[f];
})));
const maskOf = (k: string) => `${k.startsWith('sk-') ? 'sk-' : ''}••••••••${k.trim().slice(-4)}`;
async function closeLlm() {
  if (lfDirty.value && !(await confirmDiscard())) return;
  lfSnap.value = JSON.stringify(lf);
  lf.key = '';
  setQuery({ llm: undefined });
}
async function testForm() {
  const t = await runTest({ ...lf, name: lf.name.trim() }, { blocked: lfErrors.value.filter((x) => !x.includes('默认连接')).length ? `请先改正：${lfErrors.value[0]}` : !lf.enabled ? '连接已停用，先启用再测试' : undefined });
  if (t) { lfTest.value = t; lfTestSig.value = testSig(); }
}
function applyLlm() {
  lfTried.value = true;
  if (lfErrors.value.length) return;
  // 配置改过但没重新测试：不写入旧的测试结果
  const test = lfTest.value && !lfStale.value ? lfTest.value : null;
  const base = { name: lf.name.trim(), kind: lf.kind, deploy: lf.deploy, baseUrl: lf.baseUrl.trim(), model: lf.model.trim(), timeoutSec: lf.timeoutSec, maxConcurrency: lf.maxConcurrency, usages: LLM_USAGES.map((u) => u.key).filter((u) => lf.usages.includes(u)), enabled: lf.enabled };
  const keyMask = !needKey.value ? '' : lf.changeKey ? maskOf(lf.key) : llmConn.value?.keyMask ?? '';
  const c = llmConn.value;
  if (c) { Object.assign(c, base, { keyMask }); if (test) c.lastTest = test; else delete c.lastTest; }
  else {
    const n = Math.max(0, ...draft.llm.connections.map((x) => Number(x.id.slice(4)) || 0), ...saved.value.llm.connections.map((x) => Number(x.id.slice(4)) || 0)) + 1;
    draft.llm.connections.push({ id: `LLM-${String(n).padStart(2, '0')}`, ...base, keyMask, createdAt: nowStr, createdBy: '王敏', ...(test ? { lastTest: test } : {}) });
  }
  lf.key = '';
  lfSnap.value = JSON.stringify(lf);
  setQuery({ llm: undefined });
  message.info(`${c ? '修改' : '新建'}「${base.name}」已写入草稿，保存后生效；密钥只显示掩码，明文不再可见`);
}

const TZ = ['Asia/Shanghai（UTC+8）', 'Asia/Hong_Kong（UTC+8）', 'Asia/Singapore（UTC+8）', 'Asia/Tokyo（UTC+9）', 'UTC'];
const fieldDis = computed(() => readonly.value);
</script>

<template>
  <div class="st">
    <div class="panel st-nav">
      <div class="panel-head"><span class="t-16 fw-600">配置分组</span></div>
      <div v-for="g in GROUPS" :key="g.key" class="st-item" :class="{ on: g.key === group }" @click="selectGroup(g.key)">
        <div class="st-l1" :title="g.desc">
          <span class="t-14 c-1" :class="{ 'fw-600': g.key === group }">{{ g.title }}</span>
          <span v-if="dirtyOf(g.key)" class="t-12 c-2 st-mod">已修改 {{ dirtyOf(g.key) }}</span>
        </div>
      </div>
      <div class="st-navfoot t-12 c-3">修改可以跨分组累计，统一在底部保存。<span v-if="reason">（{{ reason }}）</span></div>
    </div>

    <div class="panel st-main">
      <div class="panel-head">
        <span class="t-16 fw-600">{{ groupTitle }}</span>
        <span class="t-12 c-3">{{ updated[group] ? `最近修改：${updated[group]}` : '尚未配置' }}</span>
      </div>

      <!-- 租户信息 -->
      <div v-if="group === 'tenant'" class="st-body">
        <a-form layout="horizontal" :label-col="{ style: { width: '128px' } }" :disabled="fieldDis" class="st-grid">
          <a-form-item label="租户全称" required :validate-status="draft.tenant.name.trim() ? '' : 'error'" :help="draft.tenant.name.trim() ? '' : '不能为空'"><a-input v-model:value="draft.tenant.name" :maxlength="40" /></a-form-item>
          <a-form-item label="简称" required extra="顶栏和导出文件抬头使用" :validate-status="draft.tenant.shortName.trim() ? '' : 'error'"><a-input v-model:value="draft.tenant.shortName" :maxlength="12" show-count /></a-form-item>
          <a-form-item label="租户 ID" extra="创建后不能修改"><span class="num c-2">{{ draft.tenant.tenantId }}</span></a-form-item>
          <a-form-item label="行业" extra="开通时确定，变更请联系平台运营"><span class="c-2">{{ draft.tenant.industry }}</span></a-form-item>
          <a-form-item label="默认组织" extra="新用户默认归属；问数默认按此组织取数"><a-select v-model:value="draft.tenant.defaultOrg" :options="['华东事业部', '华南事业部', '集团总部', ...ADMIN_DEPTS].filter((v, i, a) => a.indexOf(v) === i).map((v) => ({ value: v }))" /></a-form-item>
          <a-form-item label="时区"><a-select v-model:value="draft.tenant.timezone" :options="TZ.map((v) => ({ value: v }))" /></a-form-item>
          <a-form-item label="本位币" extra="创建后不能修改，金额指标都按本位币计算"><span class="c-2">{{ draft.tenant.currency }}</span></a-form-item>
          <a-form-item label="财年起始月"><a-select v-model:value="draft.tenant.fiscalStart" :options="Array.from({ length: 12 }, (_, i) => ({ value: i + 1, label: `${i + 1} 月` }))" /></a-form-item>
          <a-form-item label="每周开始于"><a-radio-group v-model:value="draft.tenant.weekStart" :options="['周一', '周日']" /></a-form-item>
          <a-form-item label="数据截至时间" extra="“今日”“昨日”等口径以此为日切"><a-select v-model:value="draft.tenant.dataCutoff" :options="['每日 00:00', '每日 06:00', '每日 08:00'].map((v) => ({ value: v }))" /></a-form-item>
          <a-form-item label="租户管理员" extra="在角色与权限里调整"><span class="c-1">{{ draft.tenant.admin }}</span></a-form-item>
          <a-form-item label="运维联系邮箱" :validate-status="EMAIL_RE.test(draft.tenant.contact) ? '' : 'error'" :help="EMAIL_RE.test(draft.tenant.contact) ? '' : '邮箱格式不对'"><a-input v-model:value="draft.tenant.contact" /></a-form-item>
        </a-form>
      </div>

      <!-- 大模型连接 -->
      <div v-else-if="group === 'llm'" class="st-body">
        <div class="st-rowhead">
          <span class="t-14 fw-600 c-1">模型连接<InfoTip text="支持 OpenAI 兼容接口；密钥加密保存，只在填写当次可见。停用的连接不会被调用。" /></span>
          <a-tooltip :title="reason"><a-button type="primary" size="small" class="st-add" :disabled="readonly" @click="setQuery({ llm: 'new' })">新建连接</a-button></a-tooltip>
        </div>
        <a-table size="small" row-key="id" :columns="llmCols" :data-source="draft.llm.connections" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <span :class="record.enabled ? 'c-1' : 'c-3'" :title="record.name">{{ record.name }}</span>
            </template>
            <span v-else-if="column.key === 'kind'" class="c-2" :title="`${record.kind} · ${record.deploy}`">{{ record.kind }} · {{ record.deploy }}</span>
            <template v-else-if="column.key === 'url'">
              <span class="num" :class="record.enabled ? 'c-1' : 'c-3'" :title="`${record.baseUrl} · 密钥 ${record.keyMask || '无需密钥'}`">{{ record.baseUrl }}</span>
            </template>
            <span v-else-if="column.key === 'model'" class="num c-2" :title="record.model">{{ record.model }}</span>
            <template v-else-if="column.key === 'usages'">
              <span v-for="u in record.usages" :key="u" class="st-use t-12" :class="{ def: draft.llm.defaults[u as LlmUsage] === record.id }">{{ u }}<template v-if="draft.llm.defaults[u as LlmUsage] === record.id"> · 默认</template></span>
            </template>
            <span v-else-if="column.key === 'limits'" class="num c-2">{{ record.timeoutSec }} 秒 / {{ record.maxConcurrency }}</span>
            <a-badge v-else-if="column.key === 'status'" status="default" :text="record.enabled ? '已启用' : '已停用'" />
            <template v-else-if="column.key === 'test'">
              <template v-if="record.lastTest">
                <span class="num" :class="tlTest(record.lastTest)" :title="record.lastTest.ok ? `模型版本 ${record.lastTest.version}` : record.lastTest.msg"><span class="c-2">{{ record.lastTest.at }}</span> · {{ record.lastTest.ok ? `${record.lastTest.latencyMs} ms` : '失败' }}</span>
              </template>
              <span v-else class="c-3">未测试</span>
            </template>
            <a-space v-else-if="column.key === 'op'" :size="12">
              <a @click="setQuery({ llm: record.id })">{{ readonly ? '查看' : '编辑' }}</a>
              <a-tooltip :title="reason"><a :class="{ 'st-dis': readonly }" @click="!readonly && testConn(record)">{{ testing === record.name ? '测试中…' : '测试连接' }}</a></a-tooltip>
              <a-tooltip :title="reason"><a :class="{ 'st-dis': readonly }" @click="!readonly && toggleConn(record)">{{ record.enabled ? '停用' : '启用' }}</a></a-tooltip>
              <a-tooltip :title="reason"><a :class="{ 'st-dis': readonly }" @click="!readonly && removeConn(record)">删除</a></a-tooltip>
            </a-space>
          </template>
        </a-table>

        <div class="st-llm2">
          <div class="st-sect">
            <div class="st-rowhead st-h"><span class="t-14 fw-600 c-1">用途默认连接<InfoTip text="每个用途调用哪个连接；只能选声明了该用途的连接" /></span></div>
            <a-form layout="horizontal" :label-col="{ style: { width: '96px' } }" :disabled="fieldDis">
              <a-form-item v-for="u in LLM_USAGES" :key="u.key" :label="u.key" :extra="u.desc"
                :validate-status="llmErrors.some((x) => x.includes(`「${u.key}」`)) ? 'error' : ''" :help="llmErrors.find((x) => x.includes(`「${u.key}」`))">
                <a-select v-model:value="draft.llm.defaults[u.key]" class="st-w360"
                  :options="candidates(u.key).map((c) => ({ value: c.id, label: `${c.name}${c.enabled ? '' : '（已停用）'}`, disabled: !c.enabled }))" />
              </a-form-item>
            </a-form>
          </div>
          <div class="st-sect">
            <div class="st-rowhead st-h"><span class="t-14 fw-600 c-1">数据出域策略<InfoTip text="发给大模型的内容范围，对所有连接生效" /></span></div>
            <a-form layout="vertical" :disabled="fieldDis">
              <a-radio-group :value="draft.llm.egress.policy" class="st-egress" @change="(e: { target: { value: EgressPolicy } }) => pickEgress(e.target.value)">
                <a-radio v-for="o in EGRESS_OPTIONS" :key="o.value" :value="o.value">
                  <span class="c-1">{{ o.label }}</span><span v-if="o.value === 'schema'" class="c-2">（默认）</span><InfoTip :text="o.desc" />
                </a-radio>
              </a-radio-group>
              <div v-if="draft.llm.egress.policy === 'masked'" class="st-mask">
                <a-form-item label="每次最多样例行数" class="st-mask1"><a-input-number v-model:value="draft.llm.egress.sampleRows" :min="1" :max="20" addon-after="行" class="st-w160" /></a-form-item>
                <a-form-item label="先脱敏的字段" :help="llmErrors.find((x) => x.includes('脱敏样例必须'))" :validate-status="llmErrors.some((x) => x.includes('脱敏样例必须')) ? 'error' : ''">
                  <a-checkbox-group v-model:value="draft.llm.egress.maskFields" :options="MASK_FIELDS" />
                </a-form-item>
              </div>
              <div v-if="llmErrors.find((x) => x.startsWith('发送明细'))" class="t-12 tl-failed">{{ llmErrors.find((x) => x.startsWith('发送明细')) }}</div>
            </a-form>
          </div>
        </div>
      </div>

      <!-- 通知渠道 -->
      <div v-else-if="group === 'notify'" class="st-body">
        <a-table size="small" row-key="id" :columns="chCols" :data-source="draft.channels" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <span class="c-1">{{ record.name }}</span>
            </template>
            <span v-else-if="column.key === 'via'" class="c-2" :title="record.via"><router-link v-if="record.extId" :to="{ path: '/build/data/external', query: { sys: record.extId } }">{{ record.via }}</router-link><template v-else>{{ record.via }}</template></span>
            <a-tooltip v-else-if="column.key === 'enabled'" :title="record.locked ? record.note : reason"><a-switch v-model:checked="record.enabled" size="small" :disabled="record.locked || readonly" /></a-tooltip>
            <template v-else-if="column.key === 'target'">
              <span class="num" :class="record.enabled ? 'c-1' : 'c-3'" :title="record.note && !record.locked ? `${record.target}：${record.note}` : record.target">{{ record.target }}</span>
            </template>
            <span v-else-if="column.key === 'receivers'" class="c-2">{{ record.receivers }}</span>
            <span v-else-if="column.key === 'test'" class="num">
              <span class="c-2">{{ record.lastTest }}</span>
              <span class="st-res" :class="record.lastResult === '失败' ? 'tl-failed' : 'c-3'">{{ record.lastResult }}</span>
            </span>
            <a-space v-else-if="column.key === 'op'" :size="12">
              <a v-if="!record.locked" @click="setQuery({ channel: record.id })">配置</a>
              <a-tooltip :title="reason"><a :class="{ 'st-dis': readonly }" @click="!readonly && testSend(record)">发送测试</a></a-tooltip>
            </a-space>
          </template>
        </a-table>
        <div class="st-rowhead">
          <span class="t-14 fw-600 c-1">通知规则</span>
          <span class="t-12 c-3">站内消息始终发送；紧急事件至少还要一个已启用渠道。未启用的渠道不能勾选。</span>
        </div>
        <a-table size="small" row-key="key" :columns="evCols" :data-source="NOTIFY_EVENTS" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'event'"><span class="c-1">{{ record.name }}</span><span class="t-12 c-3"> · {{ record.desc }}</span></template>
            <span v-else-if="column.key === 'level'" :class="record.level === '紧急' ? 'c-1 fw-600' : 'c-2'">{{ record.level }}</span>
            <a-checkbox v-else-if="column.key === 'inbox'" :checked="true" disabled />
            <a-tooltip v-else :title="enabledOf(column.key as ChannelId) ? reason : '渠道未启用'">
              <a-checkbox :checked="draft.routes[record.key].includes(column.key as ChannelId)" :disabled="readonly || !enabledOf(column.key as ChannelId)"
                @change="(e: { target: { checked: boolean } }) => toggleRoute(record.key, column.key as ChannelId, e.target.checked)" />
            </a-tooltip>
          </template>
        </a-table>
      </div>

      <!-- 安全策略 -->
      <div v-else class="st-body">
        <a-form layout="horizontal" :label-col="{ style: { width: '128px' } }" :disabled="fieldDis" class="st-sec">
          <div class="st-sect">
            <div class="t-14 fw-600 c-1 st-h">密码</div>
            <a-form-item label="最小长度"><a-input-number v-model:value="draft.security.pwdMin" :min="8" :max="32" addon-after="位" class="st-w160" /></a-form-item>
            <a-form-item label="必须包含" :validate-status="draft.security.pwdRules.length ? '' : 'error'" :help="draft.security.pwdRules.length ? '' : '至少选一类'"><a-checkbox-group v-model:value="draft.security.pwdRules" :options="['大写字母', '小写字母', '数字', '特殊字符']" /></a-form-item>
            <a-form-item label="有效期" extra="到期后登录时要求修改"><a-select v-model:value="draft.security.pwdExpireDays" class="st-w160" :options="[30, 60, 90, 180, 0].map((v) => ({ value: v, label: v ? `${v} 天` : '永不过期' }))" /></a-form-item>
          </div>
          <div class="st-sect">
            <div class="t-14 fw-600 c-1 st-h">登录与会话</div>
            <a-form-item label="连续失败锁定"><a-input-number v-model:value="draft.security.lockAfter" :min="3" :max="10" addon-after="次" class="st-w160" /></a-form-item>
            <a-form-item label="锁定时长"><a-input-number v-model:value="draft.security.lockMinutes" :min="5" :max="1440" addon-after="分钟" class="st-w160" /></a-form-item>
            <a-form-item label="会话超时" extra="无操作超过该时长需重新登录"><a-select v-model:value="draft.security.sessionMinutes" class="st-w160" :options="[30, 60, 120, 240, 480].map((v) => ({ value: v, label: `${v} 分钟` }))" /></a-form-item>
            <a-form-item label="双因素认证" :extra="draft.security.mfa === '关闭' ? '' : '企业微信扫码或动态口令'">
              <a-radio-group v-model:value="draft.security.mfa" :options="['关闭', '仅管理员', '全部用户']" />
              <div v-if="draft.security.mfa === '关闭'" class="t-12 c-2">不建议：管理员账号将没有第二重验证</div>
            </a-form-item>
          </div>
          <div class="st-sect">
            <div class="t-14 fw-600 c-1 st-h">访问与数据</div>
            <a-form-item label="登录 IP 白名单" :validate-status="badIps.length ? 'error' : ''" :help="badIps.length ? `格式不对：${badIps.join('、')}` : '每行一个 IP 或网段；留空表示不限制。API 密钥的白名单单独设置'">
              <a-textarea v-model:value="draft.security.ipAllow" :rows="4" class="num" />
            </a-form-item>
            <a-form-item label="页面水印" extra="显示 姓名 + 工号后四位"><a-switch v-model:checked="draft.security.watermark" /></a-form-item>
            <a-form-item label="导出需审批" extra="超过 1 万行的导出走审批"><a-switch v-model:checked="draft.security.exportApproval" /></a-form-item>
            <a-form-item label="审计日志保留"><a-select v-model:value="draft.security.auditKeepDays" class="st-w160" :options="[180, 365, 730, 1095].map((v) => ({ value: v, label: `${v} 天` }))" /></a-form-item>
          </div>
        </a-form>
      </div>

      <div v-if="dirty" class="st-savebar">
        <span class="t-14 c-1">
          有未保存的修改：<template v-for="(g, i) in GROUPS.filter((x) => dirtyOf(x.key))" :key="g.key">{{ i ? '，' : '' }}{{ g.title }} <span class="num">{{ dirtyOf(g.key) }}</span> 处</template>
          <span v-if="errors.length" class="t-12 tl-failed st-err">· {{ errors[0] }}<template v-if="errors.length > 1"> 等 {{ errors.length }} 项</template></span>
        </span>
        <a-space>
          <a-button @click="discard">放弃修改</a-button>
          <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="save">保存</a-button></a-tooltip>
        </a-space>
      </div>
    </div>
  </div>

  <a-drawer :open="!!llmKey" :width="600" :title="llmConn ? `大模型连接 · ${llmConn.name}` : '新建大模型连接'" @close="closeLlm">
    <a-form layout="vertical" class="st-chform" :disabled="readonly">
      <div class="st-frow">
        <a-form-item label="名称" required class="st-f2"><a-input v-model:value="lf.name" :maxlength="30" placeholder="例如：私有化 Qwen（问数）" @blur="touched.name = true" /></a-form-item>
        <a-form-item label="接口类型" class="st-f1"><a-select v-model:value="lf.kind" :options="LLM_KINDS.map((v) => ({ value: v }))" /></a-form-item>
      </div>
      <a-form-item label="部署方式"><a-radio-group v-model:value="lf.deploy" :options="['私有化部署', '公有云']" /></a-form-item>
      <a-form-item label="地址" required extra="OpenAI 兼容接口的 Base URL，例如 http://10.20.8.15:8000/v1"><a-input v-model:value="lf.baseUrl" class="num" @blur="touched.baseUrl = true" /></a-form-item>
      <a-form-item label="模型名" required><a-input v-model:value="lf.model" class="num" placeholder="例如：qwen2.5-72b-instruct" @blur="touched.model = true" /></a-form-item>
      <div v-if="!needKey && llmConn?.keyMask" class="t-12 c-2 st-ollama">Ollama 不需要密钥：点“确定”后会清除已保存的密钥 <span class="num">{{ llmConn.keyMask }}</span></div>
      <a-form-item v-if="needKey" label="密钥" :required="lf.changeKey">
        <template v-if="lf.changeKey">
          <a-input-password v-model:value="lf.key" class="num" placeholder="粘贴 API Key" autocomplete="new-password" @blur="touched.key = true" />
          <div class="t-12 c-3 st-keyhint">只在本次填写时可见；点“确定”后只保留末四位掩码，之后无法再查看明文。<a v-if="llmConn?.keyMask" @click="lf.changeKey = false; lf.key = ''">保留原密钥</a></div>
        </template>
        <div v-else class="st-keyrow"><span class="num c-2">{{ llmConn?.keyMask || '未设置' }}</span><a @click="lf.changeKey = true">更换密钥</a></div>
      </a-form-item>
      <div class="st-frow">
        <a-form-item label="超时" class="st-f1"><a-input-number v-model:value="lf.timeoutSec" :min="5" :max="300" addon-after="秒" class="st-wfull" /></a-form-item>
        <a-form-item label="最大并发" class="st-f1"><a-input-number v-model:value="lf.maxConcurrency" :min="1" :max="64" addon-after="个请求" class="st-wfull" /></a-form-item>
      </div>
      <a-form-item label="用途" required><a-checkbox-group v-model:value="lf.usages" :options="LLM_USAGES.map((u) => u.key)" /></a-form-item>
      <a-form-item label="状态"><a-switch v-model:checked="lf.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item>
    </a-form>
    <div v-if="lfStale" class="st-stale t-12 c-2">配置已改动，需重新测试（地址、模型、密钥或接口类型变了，之前的测试结果不再有效，也不会写入连接）</div>
    <a-descriptions v-else-if="lfTest" size="small" :column="1" bordered class="st-desc" title="测试结果">
      <a-descriptions-item label="结果"><span :class="lfTest.ok ? 'c-1' : 'tl-failed'">{{ lfTest.ok ? '连接正常' : `失败：${lfTest.msg}` }}</span></a-descriptions-item>
      <a-descriptions-item label="延迟"><span class="num">{{ lfTest.latencyMs }} ms</span></a-descriptions-item>
      <a-descriptions-item label="模型版本"><span class="num">{{ lfTest.version }}</span></a-descriptions-item>
      <a-descriptions-item label="测试时间"><span class="num">{{ lfTest.at }}</span></a-descriptions-item>
    </a-descriptions>
    <template #footer>
      <div class="st-foot">
        <a class="st-fl" :class="{ 'st-dis': readonly }" @click="!readonly && testForm()">{{ testing ? '测试中…' : '用当前填写测试' }}</a>
        <span v-if="lfShownErrors.length && !readonly" class="t-12 tl-failed">{{ lfShownErrors[0] }}</span>
        <a-button @click="closeLlm">取消</a-button>
        <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly || (lfTried && !!lfErrors.length) || (!lfDirty && !!llmConn)" @click="applyLlm">确定</a-button></a-tooltip>
      </div>
    </template>
  </a-drawer>

  <a-drawer :open="!!channel" :width="560" :title="channel ? `通知渠道 · ${channel.name}` : ''" @close="closeChannel">
    <template v-if="channel">
      <a-descriptions size="small" :column="1" bordered class="st-desc">
        <a-descriptions-item label="接入方式">
          <router-link v-if="channel.extId" :to="{ path: '/build/data/external', query: { sys: channel.extId } }">{{ channel.via }}</router-link>
          <template v-else>{{ channel.via }}</template>
        </a-descriptions-item>
        <a-descriptions-item label="状态">{{ channel.enabled ? '已启用' : '未启用' }}</a-descriptions-item>
        <a-descriptions-item label="最近测试"><span class="num">{{ channel.lastTest }}</span><span class="st-res" :class="channel.lastResult === '失败' ? 'tl-failed' : 'c-3'">{{ channel.lastResult }}</span></a-descriptions-item>
        <a-descriptions-item v-if="channel.note" label="说明">{{ channel.note }}</a-descriptions-item>
      </a-descriptions>
      <a-form layout="vertical" class="st-chform" :disabled="readonly">
        <a-form-item label="发送目标" required :validate-status="chForm.target.trim() ? '' : 'error'" :help="chForm.target.trim() ? '' : '不能为空'"><a-textarea v-model:value="chForm.target" :rows="2" class="num" /></a-form-item>
        <a-form-item v-if="channel.secret !== undefined" label="签名密钥 / 密码" extra="已加密保存；留原值表示不修改"><a-input-password v-model:value="chForm.secret" /></a-form-item>
        <a-form-item label="接收人"><a-input v-model:value="chForm.receivers" /></a-form-item>
      </a-form>
      <div v-if="channel.extId" class="t-12 c-3">连接、鉴权和超时在“外部系统”里维护；这里只配置通知用到的目标和接收人。</div>
    </template>
    <template #footer>
      <div class="st-foot">
        <a v-if="channel" class="st-fl" :class="{ 'st-dis': readonly }" @click="!readonly && channel && testSend(channel)">发送测试</a>
        <a-button @click="closeChannel">取消</a-button>
        <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly || !chDirty || !chForm.target.trim()" @click="applyChannel">确定</a-button></a-tooltip>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.st { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.st-nav { width: 260px; flex: none; }
.st-item { padding: 12px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.st-item:hover { background: var(--g2); }
.st-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.st-l1 { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.st-mod { margin-left: auto; }
.st-navfoot { margin-top: auto; padding: 12px 16px; }
.st-main { flex: 1; min-width: 0; }
.st-body { flex: 1; min-height: 0; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.st-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 520px)); column-gap: 48px; }
.st-sec { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; }
.st-sect { min-width: 0; }
.st-h { margin-bottom: 12px; }
.st-rowhead { display: flex; align-items: baseline; gap: 12px; margin-top: 4px; }
.st-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-w160 { width: 160px; }
.st-dis { color: var(--g6); cursor: not-allowed; }
.st-res { margin-left: 8px; }
.st-err { margin-left: 8px; }
.st-savebar { flex: none; display: flex; justify-content: space-between; align-items: center; padding: 10px 20px; border-top: var(--border); background: var(--g1); }
.st-desc { margin-bottom: 16px; }
.st-desc :deep(.ant-descriptions-item-label) { width: 110px; }
.st-foot { display: flex; justify-content: flex-end; align-items: center; gap: 8px; }
.st-fl { margin-right: auto; }
.st-add { margin-left: auto; }
.st-use { display: inline-block; margin: 2px 6px 2px 0; padding: 0 6px; line-height: 20px; border: var(--border); border-radius: 4px; color: var(--g8); }
.st-use.def { border-color: var(--g7); color: var(--g9); font-weight: 600; }
.st-llm2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 32px; margin-top: 4px; }
.st-w360 { width: 360px; }
.st-stale { margin-top: 12px; padding: 8px 12px; background: var(--g2); border-radius: var(--radius); }
.st-ollama { margin: -8px 0 16px; }
.st-egress { display: flex; flex-direction: column; gap: 10px; }
.st-mask { margin: 10px 0 0 24px; }
.st-mask1 { margin-bottom: 8px; }
.st-frow { display: flex; gap: 12px; }
.st-f1 { flex: 1; min-width: 0; }
.st-f2 { flex: 2; min-width: 0; }
.st-wfull { width: 100%; }
.st-keyhint { margin-top: 4px; }
.st-keyrow { display: flex; gap: 16px; align-items: center; }
</style>
