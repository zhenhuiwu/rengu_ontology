<script setup lang="ts">
/**
 * 模型与系统配置（PDE工程师）。
 * 左侧分组：租户信息、大模型连接（占位，第二期接入）、通知渠道、安全策略；右侧是该分组的表单。
 * 所有分组共用一份草稿：有修改时底部出现保存条（放弃修改 / 保存，保存前确认），离开页面受 useUnsavedGuard 保护；切换分组不丢修改。
 * 通知渠道的配置在抽屉里改（确定后写进草稿，仍需保存）；发送测试要确认。历史版本只读。
 * URL：?group=tenant|llm|notify|security&channel=email
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { ADMIN_DEPTS } from '@/mock';
import {
  SYSTEM_SETTINGS, NOTIFY_EVENTS, CHANNEL_ORDER, LLM_PLACEHOLDER, SETTINGS_UPDATED,
  type SystemSettings, type NotifyChannel, type ChannelId,
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
  { key: 'llm', title: '大模型连接', desc: '第二期接入，当前为占位' },
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
const routeText = (ids: ChannelId[]) => CHANNEL_ORDER.filter((id) => ids.includes(id)).map(chName).join('、') || '不通知';
const dirty = computed(() => changes.value.length > 0);
const dirtyOf = (g: GroupKey) => changes.value.filter((c) => c.group === g).length;
const guard = useUnsavedGuard(dirty);

// 校验
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CIDR_RE = /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$/;
const badIps = computed(() => draft.security.ipAllow.split(/\s+/).filter((x) => x && !CIDR_RE.test(x)));
const errors = computed(() => {
  const e: string[] = [];
  if (!draft.tenant.name.trim()) e.push('租户全称不能为空');
  if (!draft.tenant.shortName.trim()) e.push('简称不能为空');
  if (!EMAIL_RE.test(draft.tenant.contact)) e.push('运维联系邮箱格式不对');
  if (badIps.value.length) e.push(`IP 白名单有 ${badIps.value.length} 条格式不对`);
  if (!draft.security.pwdRules.length) e.push('密码至少要求包含一类字符');
  for (const ev of NOTIFY_EVENTS) if (ev.level === '紧急' && !draft.routes[ev.key].some((id) => id !== 'inbox' && draft.channels.find((c) => c.id === id)!.enabled)) e.push(`紧急事件「${ev.name}」至少要有一个站内消息以外的已启用渠道`);
  return e;
});

async function save() {
  const list = changes.value;
  const groups = [...new Set(list.map((c) => c.group))];
  const impact: string[] = [];
  if (groups.includes('security')) impact.push('安全策略：会话超时、IP 白名单立即生效（不在白名单内的已登录会话会被退出）；密码规则在用户下次改密码时生效。');
  if (groups.includes('notify')) impact.push('通知：新规则对之后产生的事件生效，已发出的通知不受影响。');
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
  for (const g of groups) if (g !== 'llm') updated[g] = `${nowStr} · 王敏`;
  saved.value = clone(draft as SystemSettings);
  message.success(`已保存 ${list.length} 处修改`);
}
async function discard() {
  if (!(await confirmDiscard())) return;
  Object.assign(draft, clone(saved.value));
}
const selectGroup = (g: GroupKey) => setQuery({ group: g === 'tenant' ? undefined : g, channel: undefined });

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
  { title: '渠道', key: 'name', width: 190 },
  { title: '启用', key: 'enabled', width: 70 },
  { title: '发送目标', key: 'target', ellipsis: true },
  { title: '接收人', key: 'receivers', width: 260, ellipsis: true },
  { title: '最近测试', key: 'test', width: 190 },
  { title: '操作', key: 'op', width: 130 },
];
const evCols = computed(() => [
  { title: '事件', key: 'event', width: 360 },
  { title: '级别', key: 'level', width: 80 },
  ...CHANNEL_ORDER.map((id) => ({ title: chName(id), key: id, width: 100, align: 'center' as const })),
]);

const TZ = ['Asia/Shanghai（UTC+8）', 'Asia/Hong_Kong（UTC+8）', 'Asia/Singapore（UTC+8）', 'Asia/Tokyo（UTC+9）', 'UTC'];
const fieldDis = computed(() => readonly.value);
</script>

<template>
  <div class="st">
    <div class="panel st-nav">
      <div class="panel-head"><span class="t-16 fw-600">配置分组</span></div>
      <div v-for="g in GROUPS" :key="g.key" class="st-item" :class="{ on: g.key === group }" @click="selectGroup(g.key)">
        <div class="st-l1">
          <span class="t-14 c-1" :class="{ 'fw-600': g.key === group }">{{ g.title }}</span>
          <a-tag v-if="g.key === 'llm'" class="st-tag">占位</a-tag>
          <span v-if="dirtyOf(g.key)" class="t-12 tl-attention st-mod">已修改 {{ dirtyOf(g.key) }}</span>
        </div>
        <div class="t-12 c-3">{{ g.desc }}</div>
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

      <!-- 大模型连接（占位） -->
      <div v-else-if="group === 'llm'" class="st-body">
        <div class="st-note t-14 c-2">
          <QuestionCircleOutlined class="c-3" />
          自定义大模型连接在第二期开放。当前问数、解读和检索都使用平台内置模型，数据不出平台。下面是规划中的配置项，暂不能填写。
        </div>
        <a-form layout="horizontal" :label-col="{ style: { width: '128px' } }" disabled class="st-grid">
          <a-form-item v-for="f in LLM_PLACEHOLDER.fields" :key="f" :label="f"><a-input placeholder="第二期开放" /></a-form-item>
        </a-form>
        <div class="t-14 fw-600 c-1 st-h">用途分配</div>
        <a-table size="small" row-key="use" :pagination="false" :data-source="LLM_PLACEHOLDER.usages" class="st-usage"
          :columns="[{ title: '用途', dataIndex: 'use', key: 'use' }, { title: '当前模型', dataIndex: 'model', key: 'model', width: 240 }, { title: '可切换', key: 'sw', width: 160 }]">
          <template #bodyCell="{ column }"><span v-if="column.key === 'sw'" class="c-3">第二期开放</span></template>
        </a-table>
        <div><a-tooltip title="第二期开放"><a-button disabled>测试连接</a-button></a-tooltip></div>
      </div>

      <!-- 通知渠道 -->
      <div v-else-if="group === 'notify'" class="st-body">
        <a-table size="small" row-key="id" :columns="chCols" :data-source="draft.channels" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <div class="c-1">{{ record.name }}</div>
              <div class="t-12 c-3"><router-link v-if="record.extId" :to="{ path: '/build/data/external', query: { sys: record.extId } }">{{ record.via }}</router-link><template v-else>{{ record.via }}</template></div>
            </template>
            <a-tooltip v-else-if="column.key === 'enabled'" :title="record.locked ? record.note : reason"><a-switch v-model:checked="record.enabled" size="small" :disabled="record.locked || readonly" /></a-tooltip>
            <template v-else-if="column.key === 'target'">
              <div class="num st-ellip" :class="record.enabled ? 'c-1' : 'c-3'">{{ record.target }}</div>
              <div v-if="record.note && !record.locked" class="t-12 c-3 st-ellip">{{ record.note }}</div>
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
            <span v-else-if="column.key === 'level'" :class="record.level === '紧急' ? 'tl-attention' : 'c-2'">{{ record.level }}</span>
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
              <div v-if="draft.security.mfa === '关闭'" class="t-12 tl-attention">不建议：管理员账号将没有第二重验证</div>
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
.st-tag { margin: 0; }
.st-mod { margin-left: auto; }
.st-navfoot { margin-top: auto; padding: 12px 16px; }
.st-main { flex: 1; min-width: 0; }
.st-body { flex: 1; min-height: 0; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.st-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 520px)); column-gap: 48px; }
.st-sec { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; }
.st-sect { min-width: 0; }
.st-h { margin-bottom: 12px; }
.st-note { display: flex; gap: 8px; align-items: baseline; background: var(--g2); border: var(--border); border-radius: var(--radius); padding: 10px 14px; max-width: 1088px; }
.st-usage { max-width: 1088px; }
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
</style>
