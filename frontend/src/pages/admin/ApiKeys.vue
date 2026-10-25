<script setup lang="ts">
/**
 * API 密钥（PDE工程师）。
 * 列表（full 分页，一屏 18 行）：名称、掩码密钥（前缀 + 后 4 位）、权限范围、创建人、最近使用、到期、状态；筛选写在 URL。
 * 新建在抽屉里（?form=create，可 &from=AK-xxxx 复制已有密钥的配置）；生成后密钥明文只显示这一次，带复制按钮，确认已保存才能关闭。
 * 详情抽屉（?key=AK-xxxx）：配置和最近调用（simple 分页）。吊销走统一确认卡，不可恢复。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { CopyOutlined, ExclamationCircleOutlined } from '@ant-design/icons-vue';
import AppPagination from '@/components/AppPagination.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { confirmDiscard } from '@/composables/useUnsavedGuard';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { paginate, fmtInt, EMPTY } from '@/mock';
import { API_KEYS, KEY_SCOPES, keyCalls, newSecret, type ApiKey, type KeyScope, type KeyStatus } from '@/mock/apikeys';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();
const confirm = useConfirmStore();
const TODAY = '2026-10-25';
const nowStr = '2026-10-25 17:20';

const keys = ref<ApiKey[]>(API_KEYS.map((k) => ({ ...k })));
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
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const count = (s: KeyStatus) => keys.value.filter((k) => k.status === s).length;
// 状态：有效 灰；即将过期 橙；已过期 红；已吊销 灰（文字淡）
const badge = (s: KeyStatus) => (s === '即将过期' ? 'warning' : s === '已过期' ? 'error' : 'default');
const columns = [
  { title: '名称', key: 'name', width: 190, ellipsis: true },
  { title: '密钥', key: 'key', width: 170 },
  { title: '权限范围', key: 'scopes' },
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
  const ok = await confirm.ask({
    title: '吊销 API 密钥', target: `${k.name}（${mask(k)}）`, okText: '吊销', danger: true, irreversible: true,
    fields: [
      { label: '权限范围', value: k.scopes.join('、') },
      { label: '最近使用', value: k.lastUsed ? `${k.lastUsed}（${k.lastIp}）` : '从未使用' },
      { label: '近 7 天调用', value: `${fmtInt(k.calls7d)} 次` },
    ],
    impact: `吊销立即生效，使用这个密钥的请求会返回 401。${k.calls7d ? '这个密钥近 7 天仍有调用，请先确认调用方已切换到新密钥。' : ''}吊销后不能恢复，只能新建。`,
  });
  if (!ok) return;
  Object.assign(k, { status: '已吊销' as KeyStatus, revokedAt: nowStr, revokedBy: '王敏' });
  message.success(`已吊销：${k.name}`);
}

// 详情抽屉
const detail = computed(() => keys.value.find((k) => k.id === q('key')));
const calls = computed(() => (detail.value ? keyCalls(detail.value) : []));
const CSIZE = 12;
const cpage = ref(1);
watch(() => q('key'), () => { cpage.value = 1; });
const cpaged = computed(() => paginate(calls.value, { page: cpage.value, size: CSIZE }));

// 新建抽屉
interface Form { name: string; env: '生产' | '测试'; scopes: KeyScope[]; life: string; ip: string; note: string }
const blank = (): Form => ({ name: '', env: '生产', scopes: [], life: '90', ip: '', note: '' });
const form = reactive<Form>(blank());
const formSnap = ref('');
const createOpen = computed(() => q('form') === 'create');
const secret = ref<{ key: ApiKey; text: string } | null>(null);
const saved = ref(false);
watch(createOpen, (open) => {
  if (!open) return;
  secret.value = null; saved.value = false;
  Object.assign(form, blank());
  const src = keys.value.find((k) => k.id === q('from'));
  if (src) Object.assign(form, { name: `${src.name}-new`, env: src.env, scopes: [...src.scopes], ip: src.ipAllow.join('\n'), note: `替换 ${src.name}（${src.id}）` });
  formSnap.value = JSON.stringify(blank());
}, { immediate: true });
const formDirty = computed(() => createOpen.value && !secret.value && JSON.stringify(form) !== formSnap.value);
const nameOk = computed(() => /^[a-z0-9][a-z0-9-]{2,39}$/.test(form.name));
const nameTaken = computed(() => keys.value.some((k) => k.name === form.name && k.status !== '已吊销'));
const canCreate = computed(() => nameOk.value && !nameTaken.value && form.scopes.length > 0 && !readonly.value);
const LIFE = [{ value: '30', label: '30 天' }, { value: '90', label: '90 天（推荐）' }, { value: '180', label: '180 天' }, { value: '365', label: '1 年' }, { value: '0', label: '永不过期（不推荐）' }];
const addDays = (n: number) => { const d = new Date(TODAY); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
function create() {
  if (!canCreate.value) return;
  const text = newSecret(form.env, keys.value.length * 97 + form.name.length);
  const k: ApiKey = {
    id: `AK-${1001 + keys.value.length}`, name: form.name, env: form.env, prefix: text.slice(0, 12), last4: text.slice(-4),
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
  if (secret.value && !saved.value) {
    const ok = await confirm.ask({
      title: '关闭后不能再查看密钥', target: `${secret.value.key.name}（${mask(secret.value.key)}）`, okText: '仍然关闭', danger: true,
      impact: '密钥明文只显示这一次，平台不保存明文。没保存的话，只能吊销后重新生成。',
    });
    if (!ok) return;
  } else if (formDirty.value && !(await confirmDiscard())) return;
  setQuery({ form: undefined, from: undefined });
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="setQuery({ form: 'create', from: undefined })">新建密钥</a-button></a-tooltip>
  </Teleport>
  <div class="panel ak">
    <div class="panel-head">
      <span class="ak-sum">
        <span class="t-16 fw-600">API 密钥</span>
        <span class="t-12 c-2">有效 <span class="num c-1">{{ count('有效') }}</span> · 即将过期 <span class="num tl-attention">{{ count('即将过期') }}</span> · 已过期 <span class="num c-1">{{ count('已过期') }}</span> · 已吊销 <span class="num c-1">{{ count('已吊销') }}</span></span>
      </span>
      <a-space>
        <a-select v-model:value="statusF" size="small" class="ak-w120" :options="['全部', '有效', '即将过期', '已过期', '已吊销'].map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v }))" />
        <a-select v-model:value="scopeF" size="small" class="ak-w140" :options="['全部', ...KEY_SCOPES].map((v) => ({ value: v, label: v === '全部' ? '全部权限范围' : v }))" />
        <a-input-search :value="kw" size="small" placeholder="名称 / 前缀 / 创建人" class="ak-w200" allow-clear @search="(v: string) => (kw = v)" />
      </a-space>
    </div>
    <div class="ak-table" :class="{ 'paged-scroll': pq.overflow.value }">
      <a-table size="small" row-key="id" :columns="columns" :data-source="paged.items" :pagination="false">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'name'" class="ak-name"><a class="num" @click="setQuery({ key: record.id })">{{ record.name }}</a><a-tag v-if="record.env === '测试'" class="ak-env">测试</a-tag></span>
          <span v-else-if="column.key === 'key'" class="num c-2">{{ mask(record) }}</span>
          <span v-else-if="column.key === 'scopes'" class="ak-scopes"><a-tag v-for="s in record.scopes" :key="s">{{ s }}</a-tag></span>
          <span v-else-if="column.key === 'createdAt'" class="num c-2">{{ record.createdAt }}</span>
          <span v-else-if="column.key === 'lastUsed'" class="num" :class="record.lastUsed ? 'c-2' : 'c-3'">{{ record.lastUsed ?? '从未使用' }}</span>
          <span v-else-if="column.key === 'expires'" class="num">
            <template v-if="!record.expiresAt"><span class="c-3">永不过期</span></template>
            <template v-else>{{ record.expiresAt }}<span v-if="record.status === '即将过期'" class="t-12 tl-attention"> 剩 {{ daysLeft(record.expiresAt) }} 天</span></template>
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
    <AppPagination
      v-model:page="pq.page.value" v-model:size="pq.size.value" class="ak-pager"
      variant="full" :total="paged.total" unit="个密钥" :default-size="pq.defaultSize" :empty="false"
    />
  </div>

  <!-- 详情 -->
  <a-drawer :open="!!detail" :width="720" :title="detail ? `API 密钥 · ${detail.name}` : ''" @close="setQuery({ key: undefined })">
    <template v-if="detail">
      <a-descriptions :column="2" bordered size="small" class="ak-desc">
        <a-descriptions-item label="密钥"><span class="num">{{ mask(detail) }}</span></a-descriptions-item>
        <a-descriptions-item label="状态"><a-badge :status="badge(detail.status)" :text="detail.status" /></a-descriptions-item>
        <a-descriptions-item label="环境">{{ detail.env }}</a-descriptions-item>
        <a-descriptions-item label="到期"><span class="num">{{ detail.expiresAt ?? '永不过期' }}</span></a-descriptions-item>
        <a-descriptions-item label="权限范围" :span="2">{{ detail.scopes.join('、') }}</a-descriptions-item>
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
          <span v-else-if="column.key === 'code'" class="num" :class="record.code === 200 ? 'tl-normal' : record.code === 429 ? 'tl-attention' : 'tl-failed'">{{ record.code }}</span>
          <span v-else-if="column.key === 'ms'" class="num">{{ record.ms }} ms</span>
        </template>
      </a-table>
      <AppPagination v-model:page="cpage" variant="simple" :total="calls.length" :size="CSIZE" unit="次调用" :empty="false" class="ak-cpager" />
    </template>
    <template #footer>
      <div class="ak-foot">
        <a-button @click="setQuery({ key: undefined })">关闭</a-button>
        <a-tooltip v-if="detail && detail.status !== '已吊销'" :title="reason"><a-button danger :disabled="readonly" @click="revoke(detail)">吊销</a-button></a-tooltip>
      </div>
    </template>
  </a-drawer>

  <!-- 新建 -->
  <a-drawer :open="createOpen" :width="560" :title="secret ? '密钥已生成' : '新建 API 密钥'" :mask-closable="!secret" @close="closeCreate">
    <template v-if="!secret">
      <a-form layout="vertical" :model="form">
        <a-form-item label="名称" required :validate-status="form.name && (!nameOk || nameTaken) ? 'error' : undefined"
          :help="form.name && !nameOk ? '小写字母、数字和短横线，3–40 个字符' : nameTaken ? '已有同名的有效密钥' : '建议写调用方系统名，例如 bi-sync-prod'">
          <a-input v-model:value="form.name" placeholder="例如 bi-sync-prod" />
        </a-form-item>
        <a-form-item label="环境"><a-radio-group v-model:value="form.env" :options="['生产', '测试']" option-type="button" /></a-form-item>
        <a-form-item label="权限范围" required help="按最小权限勾选；“动作执行”还要满足动作类型的提交条件">
          <a-checkbox-group v-model:value="form.scopes" :options="[...KEY_SCOPES]" />
        </a-form-item>
        <a-form-item label="有效期"><a-select v-model:value="form.life" :options="LIFE" /></a-form-item>
        <a-form-item label="IP 白名单" help="每行一个 IP 或网段；留空表示不限制"><a-textarea v-model:value="form.ip" :rows="3" class="num" placeholder="10.12.0.0/16" /></a-form-item>
        <a-form-item label="备注"><a-input v-model:value="form.note" /></a-form-item>
      </a-form>
    </template>
    <template v-else>
      <div class="ak-once">
        <ExclamationCircleOutlined class="tl-attention" />
        <span class="t-14 c-1">密钥明文只显示这一次，平台不保存明文。请现在复制并保存到调用方的密钥管理里。</span>
      </div>
      <div class="t-12 c-2 ak-lbl">{{ secret.key.name }} · {{ secret.key.env }} · {{ secret.key.scopes.join('、') }}</div>
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
          <a-button @click="closeCreate">取消</a-button>
          <a-button type="primary" :disabled="!canCreate" @click="create">生成密钥</a-button>
        </template>
        <a-button v-else type="primary" @click="closeCreate">完成</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.ak { flex: 1; min-height: 0; }
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
.ak-once { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border: 1px solid #ffd591; background: var(--warning-bg); border-radius: var(--radius); }
.ak-lbl { margin: 16px 0 6px; }
.ak-secret { display: flex; gap: 8px; }
.ak-mt { margin-top: 16px; }
</style>
