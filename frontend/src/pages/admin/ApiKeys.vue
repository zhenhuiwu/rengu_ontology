<script setup lang="ts">
/**
 * API 密钥（PDE工程师）。
 * 列表（full 分页，一屏 18 行）：名称、掩码密钥（前缀 + 后 4 位）、权限范围、创建人、最近使用、到期、状态；筛选写在 URL。
 * 新建在抽屉里（?form=create，可 &from=AK-xxxx 复制已有密钥的配置）；生成后密钥明文只显示这一次，带复制按钮，确认已保存才能关闭。
 * 详情抽屉（?key=AK-xxxx）：配置和最近调用（simple 分页）。吊销走统一确认卡，不可恢复。
 * 每个密钥必须绑定身份 + 数据范围（角色，或服务账号 + 数据范围），新建时缺少则不能生成；详情显示“以 xx 角色查询 · 大区 = 华东”。
 * 颜色：状态码非 200 都是失败（红）；即将过期 灰点 + 文字（橙色只用于 待确认 / 结果未知）。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { CopyOutlined, ExclamationCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { confirmDiscard, useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate, fmtInt, EMPTY } from '@/mock';
import { API_KEYS, KEY_SCOPES, SERVICE_ACCOUNTS, SA_SCOPES, bindingText, keyActor, keyCalls, newSecret, type ApiKey, type KeyBinding, type KeyScope, type KeyStatus } from '@/mock/apikeys';
import { ROLES } from '@/mock/roles';
import { adminApiOn } from '@/api/admin';
import { apiKeyCalls, apiKeyDetail, apiKeysMeta, createApiKey, createServiceAccount, listServiceAccounts, queryApiKeys, revokeApiKey, revokeServiceAccount, updateApiKey, type KeyPatch, type KeysMeta, type KeysPage, type RemoteKey, type ServiceAccount } from '@/api/apikeys';
import { loadRolesState } from '@/api/roles';
import { currentApiContext } from '@/api/http';
import type { PageResult } from '@/api/http';
import type { KeyCall } from '@/mock/apikeys';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const real = adminApiOn();
const { readonly: viewReadonly, reason: viewReason } = useReadonly();
const meta = ref<KeysMeta | null>(null);
const loading = ref(false);
const busy = ref(false);
const loadError = ref('');
const metaError = ref('');
const remote = ref<KeysPage | null>(null);
const readonly = computed(() => viewReadonly.value || (real && !meta.value?.canManage));
const reason = computed(() => viewReason.value || (real && !meta.value?.canManage ? metaError.value || '当前身份没有密钥管理权限' : ''));
/** 可勾选的权限范围：真模式取后台编码 + 中文名（只列当前身份可授予的），mock 用中文名 */
const scopeOptions = computed<{ value: string; label: string }[]>(() => real
  ? (meta.value?.scopeOptions ?? []).filter((o) => meta.value?.scopes.includes(o.value)).map((o) => ({ value: o.value, label: o.label }))
  : KEY_SCOPES.map((v) => ({ value: v, label: v })));
const scopeLabel = (s: string) => meta.value?.scopeOptions.find((o) => o.value === s)?.label ?? s;
const scopesText = (list: readonly string[]) => list.map(scopeLabel).join('、');
const rk = (k: ApiKey) => k as unknown as RemoteKey;
/** 服务账号密钥（后台）显示服务账号名 + 服务角色；其余沿用 mock 的文案 */
const bindText = (b: KeyBinding) => {
  const x = b as RemoteKey['binding'];
  return x.kind === '服务账号' && x.serviceAccountName ? `以 服务账号 ${x.serviceAccountName}（${x.roleName}）查询 · ${x.scope}` : bindingText(b);
};
const listText = (v?: string[] | null) => (v == null ? '不限制' : v.length ? v.join('、') : '全部拒绝');
const limitText = (v?: number | null, unit = '') => (v == null ? '不限制' : `${fmtInt(v)} ${unit}`);
const confirm = useConfirmStore();
const TODAY = real ? new Date().toISOString().slice(0, 10) : '2026-10-25';
const nowStr = '2026-10-25 17:20';

/** 角色绑定的数据范围取角色的行级范围（mock/roles.ts），只列有行级范围的角色；不限范围的角色不能绑定密钥 */
const roleScope = (name?: string) => {
  const role = ROLES.find((r) => r.name === name);
  if (!role || !role.scopes.length) return '';
  const seen = new Set<string>();
  return role.scopes.filter((x) => { const v = x.values.join('、'); if (seen.has(v)) return false; seen.add(v); return true; }).map((x) => `${x.prop} ${x.op} ${x.values.join('、')}`).join('；');
};
const BIND_ROLES = ROLES.filter((r) => r.scopes.length).map((r) => r.name);
const withScope = (b: KeyBinding): KeyBinding => (b.kind === '角色' ? { ...b, scope: roleScope(b.role) || b.scope } : b);
const keys = ref<ApiKey[]>(real ? [] : API_KEYS.map((k) => ({ ...k, binding: withScope(k.binding) })));
const mask = (k: ApiKey) => `${k.prefix}****${k.last4}`;
const daysLeft = (d: string) => Math.round((new Date(d).getTime() - new Date(TODAY).getTime()) / 86400000);

// 筛选（写在 URL，便于分享）
const qref = (k: string, d = '全部') => computed({ get: () => q(k) || d, set: (v: string) => setQuery({ [k]: v === d ? undefined : v || undefined, page: undefined }) });
const statusF = qref('status');
const scopeF = qref('scope');
const kw = qref('kw', '');
const pq = usePagedQuery({ defaultSize: 18, filters: [statusF, scopeF, kw] });
const rows = computed(() => keys.value.filter((k) =>
  (statusF.value === '全部' || k.status === statusF.value)
  && (scopeF.value === '全部' || k.scopes.includes(scopeF.value as KeyScope))
  && (!kw.value || `${k.name} ${k.prefix} ${k.creator} ${k.id}`.toLowerCase().includes(kw.value.trim().toLowerCase()))));
const paged = computed(() => real ? remote.value ?? { items: [], total: 0 } : paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const count = (s: KeyStatus) => real ? remote.value?.counts[s] ?? '—' : keys.value.filter((k) => k.status === s).length;
const errorText = (e: unknown) => e instanceof Error ? e.message : '请求失败';
let listSeq = 0;
async function reload() {
  if (!real) return;
  const seq = ++listSeq;
  const context = JSON.stringify(currentApiContext());
  loading.value = true; loadError.value = ''; remote.value = null; keys.value = [];
  try {
    const result = await queryApiKeys(pq.page.value, pq.size.value, statusF.value, scopeF.value, kw.value);
    if (seq !== listSeq || context !== JSON.stringify(currentApiContext())) return;
    remote.value = result; keys.value = result.items as unknown as ApiKey[];
    if (result.page !== pq.page.value) pq.page.value = result.page;
  } catch (e) {
    if (seq === listSeq && context === JSON.stringify(currentApiContext())) loadError.value = `密钥加载失败：${errorText(e)}`;
  } finally { if (seq === listSeq) loading.value = false; }
}
watch([statusF, scopeF, kw, pq.page, pq.size, () => JSON.stringify(currentApiContext())], reload, { immediate: true });
// 状态：有效 / 即将过期 灰（即将过期另写剩余天数）；已过期 红；已吊销 灰（文字淡）
const badge = (_s: KeyStatus) => 'default';
const columns = [
  { title: '名称', key: 'name', width: 190, ellipsis: true },
  { title: '密钥', key: 'key', width: 170 },
  { title: '权限范围', key: 'scopes' },
  { title: '绑定身份', key: 'binding', width: 150, ellipsis: true },
  { title: '创建人', dataIndex: 'creator', key: 'creator', width: 80 },
  { title: '创建时间', key: 'createdAt', width: 140 },
  { title: '最近使用', key: 'lastUsed', width: 140 },
  { title: '到期', key: 'expires', width: 150 },
  { title: '状态', key: 'status', width: 100 },
  { title: '近 7 天调用', key: 'calls', width: 110, align: 'right' as const },
  { title: '操作', key: 'op', width: 180 },
];

// 吊销
async function revoke(k: ApiKey) {
  if (readonly.value || busy.value) return;
  const context = JSON.stringify(currentApiContext());
  const ok = await confirm.ask({
    title: '吊销 API 密钥', target: `${k.name}（${mask(k)}）`, okText: '吊销', danger: true, irreversible: true,
    fields: [
      { label: '权限范围', value: scopesText(k.scopes) },
      { label: '绑定身份', value: bindText(k.binding) },
      { label: '最近使用', value: k.lastUsed ? `${k.lastUsed}（${k.lastIp}）` : '从未使用' },
      { label: '近 7 天调用', value: `${fmtInt(k.calls7d)} 次` },
    ],
    impact: `吊销立即生效，使用这个密钥的请求会返回 401。${k.calls7d ? '这个密钥近 7 天仍有调用，请先确认调用方已切换到新密钥。' : ''}吊销后不能恢复，只能新建。`,
  });
  if (!ok || readonly.value || context !== JSON.stringify(currentApiContext())) return;
  if (real) {
    busy.value = true;
    try {
      const result = await revokeApiKey(k.id);
      if (context !== JSON.stringify(currentApiContext())) return;
      Object.assign(k, result);
      if (remoteDetail.value?.id === k.id) remoteDetail.value = result as unknown as ApiKey;
      await reload(); message.success(`已吊销：${k.name}`);
    } catch (e) { message.error(`吊销失败：${errorText(e)}`); }
    finally { busy.value = false; }
    return;
  }
  Object.assign(k, { status: '已吊销' as KeyStatus, revokedAt: nowStr, revokedBy: '王敏' });
  message.success(`已吊销：${k.name}`);
}

// 详情抽屉
const remoteDetail = ref<ApiKey | null>(null);
const detailError = ref('');
const detail = computed(() => real ? remoteDetail.value : keys.value.find((k) => k.id === q('key')));
const calls = computed(() => (detail.value ? keyCalls(detail.value) : []));
const CSIZE = real ? 10 : 12;
const cpage = ref(1);
watch(() => q('key'), () => { cpage.value = 1; });
const remoteCalls = ref<PageResult<KeyCall> | null>(null);
const cpaged = computed(() => real ? remoteCalls.value ?? { items: [], total: 0 } : paginate(calls.value, { page: cpage.value, size: CSIZE }));
let detailSeq = 0;
watch([() => q('key'), cpage, () => JSON.stringify(currentApiContext())], async () => {
  if (!real) return;
  const seq = ++detailSeq; const id = q('key');
  const context = JSON.stringify(currentApiContext());
  remoteDetail.value = null; remoteCalls.value = null; detailError.value = '';
  if (!id) return;
  try {
    const [key, result] = await Promise.all([apiKeyDetail(id), apiKeyCalls(id, cpage.value, CSIZE)]);
    if (seq !== detailSeq || context !== JSON.stringify(currentApiContext())) return;
    remoteDetail.value = key as unknown as ApiKey; remoteCalls.value = result;
    if (result.page !== cpage.value) cpage.value = result.page;
  } catch (e) { if (seq === detailSeq) detailError.value = `详情加载失败：${errorText(e)}`; }
}, { immediate: true });

// 新建抽屉
interface Form {
  name: string; env: '生产' | '测试'; scopes: KeyScope[]; bindKind: '角色' | '服务账号'; role?: string; account?: string; saScope?: string; life: string; ip: string; note: string;
  // 真模式：主体（当前用户 / 服务账号）、动作与事件类型白名单（null = 不限制）、限流配额（null = 不限制）
  principal: '用户' | '服务账号'; saId?: string; limitActions: boolean; actionTypes: string[]; limitEvents: boolean; eventTypes: string[];
  rate: number | null; quota: number | null;
}
const blank = (): Form => ({ name: '', env: '生产', scopes: [], bindKind: '角色', role: undefined, account: undefined, saScope: undefined, life: '90', ip: '', note: '',
  principal: '用户', saId: undefined, limitActions: false, actionTypes: [], limitEvents: false, eventTypes: [], rate: null, quota: null });
const form = reactive<Form>(blank());
const formSnap = ref('');
const createOpen = computed(() => q('form') === 'create');
const secret = ref<{ key: ApiKey; text: string } | null>(null);
const saved = ref(false);
const tried = ref(false); // 点过“生成密钥”才显示必填红字
watch(createOpen, (open) => {
  if (!open) { secret.value = null; return; }
  secret.value = null; saved.value = false; tried.value = false;
  Object.assign(form, blank());
  const src = keys.value.find((k) => k.id === q('from')) ?? (remoteDetail.value?.id === q('from') ? remoteDetail.value : null);
  if (src) Object.assign(form, { name: `${src.name}-new`, env: src.env, scopes: [...src.scopes], bindKind: src.binding.kind === '服务账号' ? '服务账号' : '角色', principal: src.binding.kind === '服务账号' ? '服务账号' : '用户', saId: rk(src).binding.serviceAccountId, limitActions: rk(src).actionTypes != null, actionTypes: [...(rk(src).actionTypes ?? [])], limitEvents: rk(src).eventTypes != null, eventTypes: [...(rk(src).eventTypes ?? [])], rate: rk(src).ratePerMinute ?? null, quota: rk(src).quotaPerDay ?? null, role: src.binding.role, account: src.binding.account, saScope: src.binding.kind === '服务账号' ? src.binding.scope : undefined, ip: src.ipAllow.join('\n'), note: `替换 ${src.name}（${src.id}）` });
  formSnap.value = JSON.stringify(blank());
}, { immediate: true });
const formDirty = computed(() => createOpen.value && !secret.value && JSON.stringify(form) !== formSnap.value);
const nameOk = computed(() => /^[a-z0-9][a-z0-9-]{2,39}$/.test(form.name));
const nameTaken = computed(() => !real && keys.value.some((k) => k.name === form.name && k.status !== '已吊销'));
const saOptions = computed(() => (meta.value?.serviceAccounts ?? []).map((a) => ({ value: a.id, label: `${a.name}（${a.id} · ${a.roleName}）` })));
const realBinding = (): KeyBinding | null => {
  if (form.principal === '用户') return meta.value?.binding ?? null;
  const a = meta.value?.serviceAccounts?.find((x) => x.id === form.saId);
  return a ? { kind: '服务账号', account: a.name, scope: `服务角色 ${a.roleName} 的当前权限`, ...{ serviceAccountName: a.name, roleName: a.roleName } } as KeyBinding : null;
};
const formBinding = computed<KeyBinding | null>(() => real ? realBinding() : (form.bindKind === '角色'
  ? (form.role ? { kind: '角色', role: form.role, scope: roleScope(form.role) } : null)
  : (form.account && form.saScope ? { kind: '服务账号', account: form.account, scope: form.saScope } : null)));
const formErrors = computed(() => {
  const e: string[] = [];
  if (!nameOk.value) e.push('请填写名称（小写字母、数字和短横线，3–40 个字符）');
  else if (nameTaken.value) e.push('已有同名的有效密钥');
  if (!form.scopes.length) e.push('请至少勾选一项权限范围');
  if (real && !meta.value) e.push(metaError.value || '身份与授权加载中');
  if (real && form.principal === '服务账号' && !form.saId) e.push('请选择服务账号');
  if (real && form.limitActions && !(form.scopes as string[]).some((x) => x.startsWith('actions:'))) e.push('动作白名单需要勾选动作试运行或动作执行');
  if (real && form.limitEvents && !(form.scopes as string[]).includes('events:ingest')) e.push('事件类型白名单需要勾选事件写入');
  if (!real && form.bindKind === '角色' && !form.role) e.push('请绑定角色：密钥按角色的数据范围查询');
  if (!real && form.bindKind === '服务账号' && !form.account) e.push('请选择服务账号');
  if (!real && form.bindKind === '服务账号' && !form.saScope) e.push('请为服务账号选择数据范围');
  return e;
});
const canCreate = computed(() => !formErrors.value.length && !readonly.value);
const LIFE = [{ value: '30', label: '30 天' }, { value: '90', label: '90 天（推荐）' }, { value: '180', label: '180 天' }, { value: '365', label: '1 年' }, { value: '0', label: '永不过期（不推荐）' }];
const addDays = (n: number) => { const d = new Date(TODAY); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
async function create() {
  if (readonly.value || busy.value) return;
  tried.value = true;
  if (!canCreate.value || !formBinding.value) return;
  if (real) {
    const context = JSON.stringify(currentApiContext());
    const ok = await confirm.ask({ title: '生成 API 密钥', target: form.name, okText: '生成密钥',
      fields: [{ label: '权限范围', value: scopesText(form.scopes) }, { label: '绑定身份', value: bindText(formBinding.value) }, { label: '有效期', value: LIFE.find((x) => x.value === form.life)?.label ?? '' }],
      impact: '密钥将立即生效，明文仅本次显示；只允许当前身份已获授权的元数据 GET 接口。' });
    if (!ok || readonly.value || context !== JSON.stringify(currentApiContext()) || !meta.value?.binding.userId) return;
    const binding = form.principal === '服务账号' && form.saId
      ? { kind: '服务账号' as const, serviceAccountId: form.saId }
      : { kind: '用户' as const, userId: meta.value.binding.userId as string };
    busy.value = true;
    try {
      const result = await createApiKey({
        name: form.name, env: form.env, scopes: [...form.scopes], binding, lifeDays: Number(form.life), note: form.note, ipAllow: form.ip.split(/\s+/).filter(Boolean),
        actionTypes: form.limitActions ? [...form.actionTypes] : null, eventTypes: form.limitEvents ? [...form.eventTypes] : null,
        ratePerMinute: form.rate || null, quotaPerDay: form.quota || null,
      });
      if (context !== JSON.stringify(currentApiContext())) return;
      const { secret: text, ...key } = result;
      secret.value = { key: key as unknown as ApiKey, text }; saved.value = false;
      await reload();
    } catch (e) { message.error(`生成失败：${errorText(e)}`); }
    finally { busy.value = false; }
    return;
  }
  const text = newSecret(form.env, keys.value.length * 97 + form.name.length);
  const k: ApiKey = {
    id: `AK-${1001 + keys.value.length}`, name: form.name, env: form.env, binding: formBinding.value, prefix: text.slice(0, 12), last4: text.slice(-4),
    scopes: [...form.scopes], creator: '王敏', createdAt: nowStr, lastUsed: null, lastIp: null,
    expiresAt: form.life === '0' ? null : addDays(Number(form.life)), status: '有效', calls7d: 0,
    ipAllow: form.ip.split(/\s+/).filter(Boolean),
  };
  keys.value.unshift(k);
  secret.value = { key: k, text };
  saved.value = false;
}
async function copySecret() {
  if (!secret.value) return;
  try { await navigator.clipboard.writeText(secret.value.text); message.success('已复制到剪贴板'); } catch { message.warning('浏览器不允许自动复制，请手动选中复制'); }
}
async function closeCreate() {
  if (busy.value) return;
  if (secret.value && !saved.value) {
    const ok = await confirm.ask({
      title: '关闭后不能再查看密钥', target: `${secret.value.key.name}（${mask(secret.value.key)}）`, okText: '仍然关闭', danger: true,
      impact: '密钥明文只显示这一次，平台不保存明文。没保存的话，只能吊销后重新生成。',
    });
    if (!ok) return;
  } else if (formDirty.value && !(await confirmDiscard())) return;
  setQuery({ form: undefined, from: undefined });
}
// 调整授权与配额（PATCH；权限范围只能收窄，白名单 / 限流配额可改）
const policyKey = ref<RemoteKey | null>(null);
const policy = reactive({ scopes: [] as string[], limitActions: false, actionTypes: [] as string[], limitEvents: false, eventTypes: [] as string[], rate: null as number | null, quota: null as number | null });
function openPolicy(k: RemoteKey) {
  policyKey.value = k;
  Object.assign(policy, { scopes: [...k.scopes], limitActions: k.actionTypes != null, actionTypes: [...(k.actionTypes ?? [])], limitEvents: k.eventTypes != null,
    eventTypes: [...(k.eventTypes ?? [])], rate: k.ratePerMinute ?? null, quota: k.quotaPerDay ?? null });
}
async function savePolicy() {
  const k = policyKey.value;
  if (!k || readonly.value || busy.value) return;
  if (!policy.scopes.length) { message.error('至少保留一项权限范围'); return; }
  const body: KeyPatch = {
    scopes: [...policy.scopes], actionTypes: policy.limitActions ? [...policy.actionTypes] : null, eventTypes: policy.limitEvents ? [...policy.eventTypes] : null,
    ratePerMinute: policy.rate || null, quotaPerDay: policy.quota || null,
  };
  const context = JSON.stringify(currentApiContext());
  busy.value = true;
  try {
    const result = await updateApiKey(k.id, body);
    if (context !== JSON.stringify(currentApiContext())) return;
    if (remoteDetail.value?.id === k.id) remoteDetail.value = result as unknown as ApiKey;
    policyKey.value = null; await reload(); message.success(`已更新：${k.name}`);
  } catch (e) { message.error(`更新失败：${errorText(e)}`); }
  finally { busy.value = false; }
}

// 服务账号（?sa=1）：列表、新建（绑定服务角色）、吊销（同时吊销其全部有效密钥）
const saOpen = computed(() => q('sa') === '1');
const accounts = ref<ServiceAccount[]>([]);
const saError = ref('');
const roleOptions = ref<{ value: string; label: string }[]>([]);
const saForm = reactive({ name: '', description: '', roleId: undefined as string | undefined });
const saNameOk = computed(() => /^[a-z0-9][a-z0-9-]{2,63}$/.test(saForm.name));
const canManageSa = computed(() => real && !!meta.value?.canManageServiceAccounts && !viewReadonly.value);
async function loadAccounts() {
  if (!real || !saOpen.value) return;
  saError.value = '';
  try {
    accounts.value = (await listServiceAccounts()).items;
    if (!roleOptions.value.length) {
      const state = await loadRolesState();
      roleOptions.value = (state?.roles ?? []).map((r) => ({ value: r.id, label: `${r.name}（${r.id}）` }));
    }
  } catch (e) { saError.value = `服务账号加载失败：${errorText(e)}`; }
}
watch([saOpen, () => JSON.stringify(currentApiContext())], loadAccounts, { immediate: true });
async function refreshMeta() { try { meta.value = await apiKeysMeta(); } catch { /* 列表已显示错误 */ } }
async function addAccount() {
  if (!canManageSa.value || busy.value || !saNameOk.value || !saForm.roleId) return;
  busy.value = true;
  try {
    const a = await createServiceAccount({ name: saForm.name, description: saForm.description, roleId: saForm.roleId });
    Object.assign(saForm, { name: '', description: '', roleId: undefined });
    message.success(`已新建服务账号：${a.name}（${a.id}）`);
    await Promise.all([loadAccounts(), refreshMeta()]);
  } catch (e) { message.error(`新建失败：${errorText(e)}`); }
  finally { busy.value = false; }
}
async function revokeAccount(a: ServiceAccount) {
  if (!canManageSa.value || busy.value) return;
  const ok = await confirm.ask({
    title: '吊销服务账号', target: `${a.name}（${a.id}）`, okText: '吊销', danger: true, irreversible: true,
    fields: [{ label: '服务角色', value: a.roleName }, { label: '有效密钥', value: `${a.activeKeys} 个` }],
    impact: '吊销立即生效：这个服务账号的全部有效密钥一起吊销，调用返回 401。不能恢复。',
  });
  if (!ok) return;
  busy.value = true;
  try {
    await revokeServiceAccount(a.id);
    message.success(`已吊销：${a.name}`);
    await Promise.all([loadAccounts(), refreshMeta(), reload()]);
  } catch (e) { message.error(`吊销失败：${errorText(e)}`); }
  finally { busy.value = false; }
}

useUnsavedGuard(() => formDirty.value || !!(secret.value && !saved.value));
let metaSeq = 0;
watch(() => JSON.stringify(currentApiContext()), async () => {
  if (!real) return;
  const seq = ++metaSeq;
  meta.value = null; metaError.value = ''; secret.value = null;
  try { const result = await apiKeysMeta(); if (seq === metaSeq) meta.value = result; }
  catch (e) { if (seq === metaSeq) metaError.value = `身份与授权加载失败：${errorText(e)}`; }
}, { immediate: true });
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-button v-if="real" class="ak-mr8" @click="setQuery({ sa: '1' })">服务账号</a-button>
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly || busy" @click="setQuery({ form: 'create', from: undefined })">新建密钥</a-button></a-tooltip>
  </Teleport>
  <div class="panel ak">
    <a-alert v-if="loadError || metaError" type="error" show-icon :message="loadError || metaError" />
    <div class="panel-head">
      <span class="ak-sum">
        <span class="t-16 fw-600">API 密钥</span>
        <span class="t-12 c-2">有效 <span class="num c-1">{{ count('有效') }}</span> · 即将过期 <span class="num c-1">{{ count('即将过期') }}</span> · 已过期 <span class="num c-1">{{ count('已过期') }}</span> · 已吊销 <span class="num c-1">{{ count('已吊销') }}</span></span>
      </span>
      <a-space>
        <a-select v-model:value="statusF" size="small" class="ak-w120" :options="['全部', '有效', '即将过期', '已过期', '已吊销'].map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v }))" />
        <a-select v-model:value="scopeF" size="small" class="ak-w140" :options="[{ value: '全部', label: '全部权限范围' }, ...scopeOptions]" />
        <a-input-search :value="kw" size="small" placeholder="名称 / 前缀 / 创建人" class="ak-w200" allow-clear @search="(v: string) => (kw = v)" />
      </a-space>
    </div>
    <div class="ak-table" :class="{ 'paged-scroll': pq.overflow.value }">
      <a-table size="small" row-key="id" :columns="columns" :data-source="paged.items" :pagination="false" :loading="loading" :locale="{ emptyText: loadError || '没有符合条件的密钥' }">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'name'" class="ak-name"><a class="num" @click="setQuery({ key: record.id })">{{ record.name }}</a><a-tag v-if="record.env === '测试'" class="ak-env">测试</a-tag></span>
          <span v-else-if="column.key === 'key'" class="num c-2">{{ mask(record) }}</span>
          <a-tooltip v-else-if="column.key === 'scopes'" :title="scopesText(record.scopes)"><span class="ak-scopes"><a-tag v-for="s in record.scopes" :key="s">{{ scopeLabel(s) }}</a-tag></span></a-tooltip>
          <span v-else-if="column.key === 'binding'" class="c-2">{{ record.binding.kind === '用户' ? `${record.binding.userName}（${record.binding.userId}）` : record.binding.kind === '角色' ? record.binding.role : record.binding.serviceAccountName ?? record.binding.account }}</span>
          <span v-else-if="column.key === 'createdAt'" class="num c-2">{{ record.createdAt }}</span>
          <span v-else-if="column.key === 'lastUsed'" class="num" :class="record.lastUsed ? 'c-2' : 'c-3'">{{ record.lastUsed ?? '从未使用' }}</span>
          <span v-else-if="column.key === 'expires'" class="num">
            <template v-if="!record.expiresAt"><span class="c-3">永不过期</span></template>
            <template v-else>{{ record.expiresAt }}<span v-if="record.status === '即将过期'" class="t-12 c-2"> 剩 {{ daysLeft(record.expiresAt) }} 天</span></template>
          </span>
          <a-badge v-else-if="column.key === 'status'" :status="badge(record.status)" :text="record.status" :class="{ 'ak-off': record.status === '已吊销' }" />
          <span v-else-if="column.key === 'calls'" class="num" :class="record.calls7d ? 'c-1' : 'c-3'">{{ record.calls7d ? fmtInt(record.calls7d) : EMPTY }}</span>
          <a-space v-else-if="column.key === 'op'" :size="12">
            <a @click="setQuery({ key: record.id })">详情</a>
            <a-tooltip v-if="record.status !== '已吊销'" :title="reason"><a :class="{ 'ak-dis': readonly }" @click="!readonly && revoke(record)">吊销</a></a-tooltip>
            <a-tooltip v-if="record.status !== '有效'" :title="reason"><a :class="{ 'ak-dis': readonly }" @click="!readonly && setQuery({ form: 'create', from: record.id })">复制配置新建</a></a-tooltip>
          </a-space>
        </template>
      </a-table>
    </div>
    <AppPagination v-if="!real || remote"
      v-model:page="pq.page.value" v-model:size="pq.size.value" class="ak-pager"
      variant="full" :total="paged.total" unit="个密钥" :default-size="pq.defaultSize" :empty="false"
    />
  </div>

  <!-- 详情 -->
  <a-drawer :open="real ? !!q('key') : !!detail" :width="720" :title="detail ? `API 密钥 · ${detail.name}` : 'API 密钥详情'" @close="setQuery({ key: undefined })">
    <a-alert v-if="detailError" type="error" show-icon :message="detailError" />
    <a-spin v-else-if="real && !detail" />
    <template v-if="detail">
      <a-descriptions :column="2" bordered size="small" class="ak-desc">
        <a-descriptions-item label="密钥"><span class="num">{{ mask(detail) }}</span></a-descriptions-item>
        <a-descriptions-item label="状态"><a-badge :status="badge(detail.status)" :text="detail.status" /></a-descriptions-item>
        <a-descriptions-item label="环境">{{ detail.env }}</a-descriptions-item>
        <a-descriptions-item label="到期"><span class="num">{{ detail.expiresAt ?? '永不过期' }}</span></a-descriptions-item>
        <a-descriptions-item label="权限范围" :span="2">{{ scopesText(detail.scopes) }}</a-descriptions-item>
        <template v-if="real">
          <a-descriptions-item label="动作白名单" :span="2"><span class="num">{{ listText(rk(detail).actionTypes) }}</span></a-descriptions-item>
          <a-descriptions-item label="事件类型白名单" :span="2"><span class="num">{{ listText(rk(detail).eventTypes) }}</span></a-descriptions-item>
          <a-descriptions-item label="每分钟限流"><span class="num">{{ limitText(rk(detail).ratePerMinute, '次') }}</span><span class="t-12 c-3"> · 本分钟 {{ rk(detail).usage?.minute ?? 0 }}</span></a-descriptions-item>
          <a-descriptions-item label="每日配额"><span class="num">{{ limitText(rk(detail).quotaPerDay, '次') }}</span><span class="t-12 c-3"> · 今日 {{ rk(detail).usage?.day ?? 0 }}</span></a-descriptions-item>
        </template>
        <a-descriptions-item label="身份与数据范围" :span="2"><a-tooltip :title="bindText(detail.binding)"><span class="ak-ell">{{ bindText(detail.binding) }}</span></a-tooltip></a-descriptions-item>
        <a-descriptions-item v-if="detail.scopes.includes('动作执行')" label="动作发起人" :span="2">{{ keyActor(detail) }}<span class="t-12 c-3"> · 动作中心执行记录的发起人这样显示</span></a-descriptions-item>
        <a-descriptions-item label="IP 白名单" :span="2"><span class="num">{{ detail.ipAllow.join('、') || '不限制' }}</span></a-descriptions-item>
        <a-descriptions-item label="创建"><span class="num">{{ detail.createdAt }}</span> · {{ detail.creator }}</a-descriptions-item>
        <a-descriptions-item label="最近使用"><span class="num">{{ detail.lastUsed ? `${detail.lastUsed} · ${detail.lastIp}` : '从未使用' }}</span></a-descriptions-item>
        <a-descriptions-item v-if="detail.revokedAt" label="吊销" :span="2"><span class="num">{{ detail.revokedAt }}</span> · {{ detail.revokedBy }}</a-descriptions-item>
      </a-descriptions>
      <div class="t-14 fw-600 c-1 ak-h">最近调用</div>
      <a-table size="small" row-key="time" :pagination="false" :data-source="cpaged.items" :locale="{ emptyText: '没有调用记录' }"
        :columns="[{ title: '时间', key: 'time', width: 170 }, { title: '接口', dataIndex: 'api', key: 'api', ellipsis: true }, { title: '状态码', key: 'code', width: 80 }, { title: '耗时', key: 'ms', width: 80, align: 'right' }]">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'time'" class="num c-2">{{ record.time }}</span>
          <span v-else-if="column.key === 'code'" class="num" :class="record.code === 200 ? 'tl-normal' : 'tl-failed'">{{ record.code }}</span>
          <span v-else-if="column.key === 'ms'" class="num">{{ record.ms }} ms</span>
        </template>
      </a-table>
      <AppPagination v-model:page="cpage" variant="simple" :total="cpaged.total" :size="CSIZE" unit="次调用" :empty="false" class="ak-cpager" />
    </template>
    <template #footer>
      <div class="ak-foot">
        <a-button @click="setQuery({ key: undefined })">关闭</a-button>
        <a-tooltip v-if="real && detail && detail.status !== '已吊销'" :title="reason"><a-button :disabled="readonly || busy" @click="openPolicy(rk(detail))">调整授权与配额</a-button></a-tooltip>
        <a-tooltip v-if="detail && detail.status !== '已吊销'" :title="reason"><a-button danger :disabled="readonly" @click="revoke(detail)">吊销</a-button></a-tooltip>
      </div>
    </template>
  </a-drawer>

  <!-- 新建 -->
  <a-drawer :open="createOpen" :width="560" :title="secret ? '密钥已生成' : '新建 API 密钥'" :mask-closable="!secret && !busy" :closable="!busy" @close="closeCreate">
    <template v-if="!secret">
      <a-alert v-if="real && metaError" type="error" show-icon :message="metaError" />
      <a-form layout="vertical" :model="form" :disabled="busy">
        <a-form-item label="名称" required :validate-status="form.name && (!nameOk || nameTaken) ? 'error' : undefined"
          :help="form.name && !nameOk ? '小写字母、数字和短横线，3–40 个字符' : nameTaken ? '已有同名的有效密钥' : '建议写调用方系统名，例如 bi-sync-prod'">
          <a-input v-model:value="form.name" placeholder="例如 bi-sync-prod" />
        </a-form-item>
        <a-form-item label="环境"><a-radio-group v-model:value="form.env" :options="['生产', '测试']" option-type="button" /></a-form-item>
        <a-form-item label="权限范围" required :help="real ? '按最小权限勾选；只能勾选当前身份已获授权的范围' : '按最小权限勾选；“动作执行”还要满足动作类型的提交条件'">
          <a-checkbox-group v-model:value="form.scopes" :options="scopeOptions" />
          <span v-if="real && meta && !scopeOptions.length" class="tl-failed">当前身份没有可授予的权限范围</span>
        </a-form-item>
        <template v-if="real">
          <a-form-item label="绑定身份" required :help="form.principal === '用户' ? '访问权限受当前账号权限限制' : '按服务账号绑定的服务角色在当前本体的权限查询'" class="ak-mb8">
            <a-radio-group v-model:value="form.principal" option-type="button"
              :options="[{ value: '用户', label: '当前账号' }, { value: '服务账号', label: '服务账号', disabled: !meta?.canManageServiceAccounts }]" />
          </a-form-item>
          <a-form-item v-if="form.principal === '用户'"><a-input :value="meta ? `${meta.binding.userName}（${meta.binding.userId}）` : '身份加载中'" disabled /></a-form-item>
          <a-form-item v-else :validate-status="tried && !form.saId ? 'error' : undefined" :help="saOptions.length ? undefined : '还没有启用的服务账号，先在“服务账号”里新建'">
            <a-select v-model:value="form.saId" placeholder="选择服务账号" :options="saOptions" />
          </a-form-item>
          <a-form-item label="动作类型白名单" help="开启后只允许调用列出的动作类型编号；空列表 = 全部拒绝">
            <a-switch v-model:checked="form.limitActions" size="small" />
            <a-select v-if="form.limitActions" v-model:value="form.actionTypes" mode="tags" class="num ak-mt8" placeholder="输入动作类型编号后回车" :token-separators="[',', ' ']" />
          </a-form-item>
          <a-form-item label="事件类型白名单" help="开启后只允许写入列出的事件类型编号">
            <a-switch v-model:checked="form.limitEvents" size="small" />
            <a-select v-if="form.limitEvents" v-model:value="form.eventTypes" mode="tags" class="num ak-mt8" placeholder="输入事件类型编号后回车" :token-separators="[',', ' ']" />
          </a-form-item>
          <a-form-item label="限流与配额" help="留空表示不限制；超出返回 429">
            <a-space><a-input-number v-model:value="form.rate" :min="1" :max="100000" placeholder="每分钟" class="num" /><a-input-number v-model:value="form.quota" :min="1" :max="100000000" placeholder="每日" class="num" /></a-space>
          </a-form-item>
        </template>
        <template v-else>
        <a-form-item label="绑定身份" required class="ak-mb8">
          <a-radio-group v-model:value="form.bindKind" :options="[{ value: '角色', label: '绑定角色' }, { value: '服务账号', label: '服务账号 + 数据范围' }]" option-type="button" />
        </a-form-item>
        <a-form-item v-if="form.bindKind === '角色'" :validate-status="tried && !form.role ? 'error' : undefined"
          :help="form.role ? `数据范围：${roleScope(form.role)}（取角色的行级范围）` : tried ? '请绑定角色' : '密钥按角色的功能权限和行级数据范围查询；不限范围的角色不能绑定'">
          <a-select v-model:value="form.role" placeholder="选择角色" :options="BIND_ROLES.map((v) => ({ value: v, label: v }))" />
        </a-form-item>
        <template v-else>
          <a-form-item :validate-status="tried && !form.account ? 'error' : undefined" :help="tried && !form.account ? '请选择服务账号' : undefined" class="ak-mb8">
            <a-select v-model:value="form.account" placeholder="选择服务账号" class="num" :options="SERVICE_ACCOUNTS.map((v) => ({ value: v, label: v }))" />
          </a-form-item>
          <a-form-item :validate-status="tried && !form.saScope ? 'error' : undefined" :help="tried && !form.saScope ? '请选择数据范围' : '服务账号没有角色范围，必须显式指定可查询的数据'">
            <a-select v-model:value="form.saScope" placeholder="选择数据范围" :options="SA_SCOPES.map((v) => ({ value: v, label: v }))" />
          </a-form-item>
        </template>
        </template>
        <a-form-item label="有效期"><a-select v-model:value="form.life" :options="LIFE" /></a-form-item>
        <a-form-item label="IP 白名单" help="每行一个 IP 或网段；留空表示不限制"><a-textarea v-model:value="form.ip" :rows="3" class="num" placeholder="10.12.0.0/16" /></a-form-item>
        <a-form-item label="备注"><a-input v-model:value="form.note" :maxlength="real ? 512 : undefined" /></a-form-item>
      </a-form>
    </template>
    <template v-else>
      <div class="ak-once">
        <ExclamationCircleOutlined class="c-2" />
        <span class="t-14 c-1">密钥明文只显示这一次，平台不保存明文。请现在复制并保存到调用方的密钥管理里。</span>
      </div>
      <div class="t-12 c-2 ak-lbl">{{ secret.key.name }} · {{ secret.key.env }} · {{ scopesText(secret.key.scopes) }} · {{ bindText(secret.key.binding) }}</div>
      <div class="ak-secret">
        <a-input :value="secret.text" readonly class="num" />
        <a-button type="primary" @click="copySecret"><CopyOutlined />复制</a-button>
      </div>
      <a-descriptions :column="1" bordered size="small" class="ak-desc ak-mt">
        <a-descriptions-item label="列表中显示为"><span class="num">{{ mask(secret.key) }}</span></a-descriptions-item>
        <a-descriptions-item label="到期"><span class="num">{{ secret.key.expiresAt ?? '永不过期' }}</span></a-descriptions-item>
        <a-descriptions-item label="请求头"><span class="num">Authorization: Bearer &lt;密钥&gt;</span></a-descriptions-item>
      </a-descriptions>
      <a-checkbox v-model:checked="saved" class="ak-mt">我已复制并妥善保存</a-checkbox>
    </template>
    <template #footer>
      <div class="ak-foot">
        <template v-if="!secret">
          <span v-if="tried && formErrors.length" class="t-12 tl-failed ak-err">{{ formErrors[0] }}{{ formErrors.length > 1 ? ` 等 ${formErrors.length} 项` : '' }}</span>
          <a-button :disabled="busy" @click="closeCreate">取消</a-button>
          <a-button type="primary" :loading="busy" :disabled="readonly || busy || (tried && !canCreate)" @click="create">生成密钥</a-button>
        </template>
        <a-button v-else type="primary" @click="closeCreate">完成</a-button>
      </div>
    </template>
  </a-drawer>

  <!-- 调整授权与配额 -->
  <a-modal :open="!!policyKey" :title="policyKey ? `调整授权与配额 · ${policyKey.name}` : ''" :confirm-loading="busy" ok-text="保存" @ok="savePolicy" @cancel="policyKey = null">
    <a-form v-if="policyKey" layout="vertical">
      <a-form-item label="权限范围" help="只能收窄；要增加范围请新建密钥">
        <a-checkbox-group v-model:value="policy.scopes" :options="policyKey.scopes.map((v) => ({ value: v, label: scopeLabel(v) }))" />
      </a-form-item>
      <a-form-item label="动作类型白名单">
        <a-switch v-model:checked="policy.limitActions" size="small" />
        <a-select v-if="policy.limitActions" v-model:value="policy.actionTypes" mode="tags" class="num ak-mt8" :token-separators="[',', ' ']" />
      </a-form-item>
      <a-form-item label="事件类型白名单">
        <a-switch v-model:checked="policy.limitEvents" size="small" />
        <a-select v-if="policy.limitEvents" v-model:value="policy.eventTypes" mode="tags" class="num ak-mt8" :token-separators="[',', ' ']" />
      </a-form-item>
      <a-form-item label="限流与配额" help="留空表示不限制">
        <a-space><a-input-number v-model:value="policy.rate" :min="1" :max="100000" placeholder="每分钟" /><a-input-number v-model:value="policy.quota" :min="1" :max="100000000" placeholder="每日" /></a-space>
      </a-form-item>
    </a-form>
  </a-modal>

  <!-- 服务账号 -->
  <a-drawer :open="saOpen" :width="720" title="服务账号" @close="setQuery({ sa: undefined })">
    <a-alert v-if="saError" type="error" show-icon :message="saError" />
    <a-alert v-else-if="!meta?.canManageServiceAccounts" type="info" show-icon message="管理服务账号需要“API 密钥”和“角色与权限”的管理权限" class="ak-mb8" />
    <a-table size="small" row-key="id" :pagination="false" :data-source="accounts" :locale="{ emptyText: '还没有服务账号' }"
      :columns="[{ title: '编号', dataIndex: 'id', key: 'id', width: 90 }, { title: '名称', dataIndex: 'name', key: 'name' }, { title: '服务角色', dataIndex: 'roleName', key: 'roleName', width: 120 }, { title: '有效密钥', dataIndex: 'activeKeys', key: 'activeKeys', width: 80, align: 'right' }, { title: '状态', dataIndex: 'status', key: 'status', width: 80 }, { title: '操作', key: 'op', width: 70 }]">
      <template #bodyCell="{ column, record }">
        <a v-if="column.key === 'op' && record.status !== '已吊销'" :class="{ 'ak-dis': !canManageSa }" @click="canManageSa && revokeAccount(record)">吊销</a>
      </template>
    </a-table>
    <template v-if="canManageSa">
      <div class="t-14 fw-600 c-1 ak-h">新建服务账号</div>
      <a-form layout="vertical" :disabled="busy">
        <a-form-item label="名称" required :validate-status="saForm.name && !saNameOk ? 'error' : undefined" help="小写字母、数字和短横线，3–64 个字符">
          <a-input v-model:value="saForm.name" placeholder="例如 svc-bi-sync" />
        </a-form-item>
        <a-form-item label="服务角色" required help="角色需已绑定到当前本体；密钥按这个角色的功能与行级权限执行">
          <a-select v-model:value="saForm.roleId" :options="roleOptions" placeholder="选择角色" />
        </a-form-item>
        <a-form-item label="说明"><a-input v-model:value="saForm.description" :maxlength="512" /></a-form-item>
        <a-button type="primary" :loading="busy" :disabled="!saNameOk || !saForm.roleId" @click="addAccount">新建</a-button>
      </a-form>
    </template>
  </a-drawer>
</template>

<style scoped>
.ak-mr8 { margin-right: 8px; }
.ak-mt8 { margin-top: 8px; }
.ak { flex: 1; min-height: 0; }
.ak-empty { margin: auto; padding: 32px; }
.ak-sum { display: inline-flex; align-items: baseline; gap: 16px; }
.ak-table { flex: 1; min-height: 0; padding: 0 16px; }
.ak-pager { padding: 4px 16px 8px; }
.ak-w120 { width: 120px; }
.ak-table :deep(td) { white-space: nowrap; }
.ak-scopes { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ak-w140 { width: 140px; }
.ak-w200 { width: 200px; }
.ak-name { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; }
.ak-env { margin: 0; }
.ak-scopes :deep(.ant-tag) { margin-inline-end: 4px; }
.ak-off :deep(.ant-badge-status-text) { color: var(--g8); }
.ak-dis { color: var(--g6); cursor: not-allowed; }
.ak-desc :deep(.ant-descriptions-item-label) { width: 110px; }
.ak-h { margin: 16px 0 8px; }
.ak-cpager { margin-top: 8px; }
.ak-foot { display: flex; justify-content: flex-end; gap: 8px; }
.ak-once { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border: var(--border); background: var(--g2); border-radius: var(--radius); }
.ak-mb8 { margin-bottom: 8px; }
.ak-err { margin-right: auto; align-self: center; }
.ak-lbl { margin: 16px 0 6px; }
.ak-secret { display: flex; gap: 8px; }
.ak-mt { margin-top: 16px; }
.ak-ell { display: block; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
