<script setup lang="ts">
/**
 * 函数：本体里可被动作、规则调用的 Python 函数。
 * 左侧函数列表（simple 分页），右侧详情页签：定义（签名、入参 / 出参按本体对象类型标注、被谁使用）、测试运行、版本历史（simple 分页）。
 * 编辑在抽屉里（?form=edit），受 useReadonly 控制（看历史版本时只读）；有未保存修改时关闭 / 离开要确认。
 * URL：?fn=FN-003&tab=def|test|versions&form=edit
 */
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { paginate } from '@/mock';
import { FUNCTIONS, FN_PENDING, fnVersions, fnSignature, type OntFunction, type FnVersion } from '@/mock/functions';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();
const confirm = useConfirmStore();

// 本页内修改（编辑保存 / 回滚）叠加在示例数据上
const patches = ref<Record<string, Partial<OntFunction>>>({});
const extraVers = ref<Record<string, FnVersion[]>>({});
const fns = computed(() => FUNCTIONS.map((f) => ({ ...f, ...patches.value[f.id] })));

// 列表
const kw = ref('');
const statusF = ref<string>('全部');
const listRows = computed(() => fns.value.filter((f) =>
  (statusF.value === '全部' || f.status === statusF.value)
  && (!kw.value || `${f.name} ${f.title}`.toLowerCase().includes(kw.value.trim().toLowerCase()))));
const LSIZE = 12;
const lpage = ref(1);
watch([kw, statusF], () => { lpage.value = 1; });
const listPaged = computed(() => paginate(listRows.value, { page: lpage.value, size: LSIZE }));
// 状态一律灰点 + 文字（橙色只用于 待确认 / 结果未知）
const statusBadge = (_s: OntFunction['status']) => 'default';

const fn = computed(() => fns.value.find((f) => f.id === q('fn')) ?? fns.value[0]);
const tab = computed({ get: () => q('tab') || 'def', set: (v: string) => setQuery({ tab: v === 'def' ? undefined : v }) });

// 版本历史
const pending = computed(() => FN_PENDING[fn.value.id]);
const versions = computed<FnVersion[]>(() => [...(pending.value ? [pending.value] : []), ...(extraVers.value[fn.value.id] ?? []), ...fnVersions(fn.value.id).map((v, i) => ({ ...v, status: (i === 0 && extraVers.value[fn.value.id]?.length ? '历史' : v.status) as FnVersion['status'] }))]);
const VSIZE = 12;
const vpage = ref(1);
watch(() => fn.value.id, () => { vpage.value = 1; resetTest(); });
const vPaged = computed(() => paginate(versions.value, { page: vpage.value, size: VSIZE }));
const vcols = [
  { title: '版本', key: 'ver', width: 100 },
  { title: '时间', key: 'time', width: 150 },
  { title: '作者', dataIndex: 'author', key: 'author', width: 90 },
  { title: '说明', dataIndex: 'note', key: 'note', ellipsis: true },
  { title: '状态', key: 'status', width: 80 },
  { title: '操作', key: 'op', width: 110 },
];
const bump = (v: string) => { const p = v.replace(/^v/, '').split('.').map(Number); p[2] += 1; return `v${p.join('.')}`; };
const nowStr = '2026-10-25 16:58';
async function rollback(v: FnVersion) {
  const ok = await confirm.ask({
    title: '回滚到此版本', target: `${fn.value.name} ${v.ver}`, okText: '回滚',
    fields: [{ label: '当前版本', value: fn.value.version }, { label: '回滚到', value: `${v.ver}（${v.time} · ${v.author}）` }, { label: '受影响', value: fn.value.usedBy.length ? fn.value.usedBy.map((u) => `${u.kind}「${u.name}」`).join('、') : '没有动作或规则使用' }],
    impact: `会生成新版本 ${bump(fn.value.version)}，内容与 ${v.ver} 相同；使用它的动作和规则下次调用即生效。`,
  });
  if (!ok) return;
  addVersion(`回滚到 ${v.ver}`);
  message.success(`已回滚，新版本 ${fn.value.version}`);
}
function addVersion(note: string, extra: Partial<OntFunction> = {}) {
  const ver = bump(fn.value.version);
  extraVers.value[fn.value.id] = [{ ver, time: nowStr, author: '王敏', note, status: '当前' }, ...(extraVers.value[fn.value.id] ?? []).map((x) => ({ ...x, status: '历史' as const }))];
  patches.value[fn.value.id] = { ...patches.value[fn.value.id], ...extra, version: ver, updatedAt: nowStr };
}

// 测试运行
const testInput = ref('');
const testOut = ref<{ text: string; ms: number; at: string } | null>(null);
const runCount = ref(0);
const running = ref(false);
function resetTest() { testInput.value = JSON.stringify(fn.value.sampleIn, null, 2); testOut.value = null; }
resetTest();
function runTest() {
  try { JSON.parse(testInput.value); } catch { message.error('输入不是合法的 JSON'); return; }
  running.value = true;
  setTimeout(() => {
    running.value = false;
    // 本次运行：耗时围绕平均耗时浮动，时间取当前时刻（演示数据）
    runCount.value += 1;
    const base = Math.max(3, fn.value.avgMs || 26);
    const now = new Date();
    const hh = (n: number) => String(n).padStart(2, '0');
    testOut.value = { text: JSON.stringify(fn.value.sampleOut, null, 2), ms: Math.round(base * (0.8 + ((runCount.value * 37) % 40) / 100)), at: `${hh(now.getHours())}:${hh(now.getMinutes())}:${hh(now.getSeconds())}` };
  }, 400);
}

// 编辑抽屉
interface Form { title: string; desc: string; timeoutS: number; code: string }
const form = reactive<Form>({ title: '', desc: '', timeoutS: 5, code: '' });
const formInit = ref('');
const formOpen = computed(() => q('form') === 'edit');
const formDirty = computed(() => formOpen.value && JSON.stringify(form) !== formInit.value);
const guard = useUnsavedGuard(formDirty);
watch([formOpen, () => fn.value.id], () => {
  if (!formOpen.value) return;
  Object.assign(form, { title: fn.value.title, desc: fn.value.desc, timeoutS: fn.value.timeoutS, code: fn.value.code });
  formInit.value = JSON.stringify(form);
}, { immediate: true });
const closeForm = async () => { if (formDirty.value && !(await confirmDiscard())) return; setQuery({ form: undefined }); };
function save() {
  if (!form.title.trim()) { message.warning('请填写中文名'); return; }
  addVersion('在线编辑', { title: form.title, desc: form.desc, timeoutS: form.timeoutS, code: form.code });
  formInit.value = JSON.stringify(form);
  setQuery({ form: undefined });
  message.success(`已保存为 ${fn.value.version}`);
}
const select = (id: string) => guard(() => setQuery({ fn: id, form: undefined }));
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button :type="formOpen ? 'default' : 'primary'" :disabled="readonly">新建函数</a-button></a-tooltip>
  </Teleport>
  <div class="fn">
    <div class="panel fn-list">
      <div class="fn-filters">
        <a-input-search v-model:value="kw" placeholder="函数名 / 中文名" allow-clear />
        <a-select v-model:value="statusF" class="fn-sel" :options="['全部', '已发布', '草稿', '已停用'].map((v) => ({ value: v, label: v }))" />
      </div>
      <div class="fn-items">
        <div v-for="f in listPaged.items" :key="f.id" class="fn-item" :class="{ on: f.id === fn.id }" @click="select(f.id)">
          <div class="fn-l1" :title="`${f.name} · ${f.title} · ${f.version} · ${f.usedBy.length ? f.usedBy.length + ' 处使用' : '未使用'}`">
            <span class="t-14 fw-600 c-1 num fn-ellip">{{ f.name }}</span>
            <a-badge :status="statusBadge(f.status)" :text="f.status" class="fn-badge" />
          </div>
        </div>
      </div>
      <AppPagination v-model:page="lpage" variant="simple" :total="listRows.length" :size="LSIZE" unit="个函数" class="fn-pager" />
    </div>

    <div class="panel fn-detail">
      <div class="fn-head">
        <div class="fn-title">
          <span class="t-20 fw-600 c-1 num">{{ fn.name }}</span>
          <span class="t-16 c-2">{{ fn.title }}<InfoTip :text="fn.desc" /></span>
          <a-tag>{{ fn.runtime }}</a-tag>
          <a-tag><span class="num">{{ fn.version }}</span></a-tag>
          <a-badge :status="statusBadge(fn.status)" :text="fn.status" />
        </div>
        <a-space>
          <a-button @click="tab = 'test'">测试运行</a-button>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="setQuery({ form: 'edit' })">编辑</a-button></a-tooltip>
        </a-space>
      </div>
      <div class="t-12 c-2 fn-sub">负责人 {{ fn.owner }} · 更新于 <span class="num">{{ fn.updatedAt }}</span></div>
      <a-tabs v-model:active-key="tab" class="fn-tabs">
        <a-tab-pane key="def" tab="定义" />
        <a-tab-pane key="test" tab="测试运行" />
        <a-tab-pane key="versions" :tab="`版本历史（${versions.length}）`" />
      </a-tabs>

      <div v-if="tab === 'def'" class="fn-body fn-def">
        <div class="fn-col">
          <div class="t-14 fw-600 c-1 fn-h">签名</div>
          <pre class="fn-pre num">{{ fnSignature(fn) }}</pre>
          <div class="t-14 fw-600 c-1 fn-h">入参</div>
          <a-table size="small" row-key="name" :pagination="false" :data-source="fn.inputs" :columns="[{ title: '参数', key: 'name', width: 120 }, { title: '类型', key: 'type', width: 200 }, { title: '说明', dataIndex: 'desc', key: 'desc' }]">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'name'" class="num c-1">{{ record.name }}</span>
              <span v-else-if="column.key === 'type'"><span class="num">{{ record.type }}</span><a-tag v-if="record.objType" class="fn-obj">对象 · {{ record.objType }}</a-tag></span>
            </template>
          </a-table>
          <div class="t-14 fw-600 c-1 fn-h">出参</div>
          <a-descriptions :column="1" bordered size="small" class="fn-desc">
            <a-descriptions-item label="类型"><span class="num">{{ fn.output.type }}</span><a-tag v-if="fn.output.objType" class="fn-obj">对象 · {{ fn.output.objType }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="说明">{{ fn.output.desc }}</a-descriptions-item>
          </a-descriptions>
          <div class="t-14 fw-600 c-1 fn-h">代码</div>
          <pre class="fn-pre num">{{ fn.code }}</pre>
        </div>
        <div class="fn-side">
          <div class="t-14 fw-600 c-1 fn-h">被引用（{{ fn.usedBy.length }}）</div>
          <div v-if="!fn.usedBy.length" class="t-12 c-3">没有动作或规则使用这个函数</div>
          <div v-for="u in fn.usedBy" :key="u.name" class="fn-use">
            <a-tag>{{ u.kind }}</a-tag><span class="c-1">{{ u.name }}</span>
          </div>
          <div v-if="pending" class="t-12 c-2 fn-pend">变更中：<a @click="router.push({ path: '/build/ontology/releases', query: { cr: pending.cr } })">{{ pending.cr }}</a> · {{ pending.note }}</div>
          <div class="t-14 fw-600 c-1 fn-h">运行参数</div>
          <a-descriptions :column="1" bordered size="small" class="fn-desc">
            <a-descriptions-item label="运行环境">{{ fn.runtime }}</a-descriptions-item>
            <a-descriptions-item label="超时"><span class="num">{{ fn.timeoutS }} 秒</span></a-descriptions-item>
            <a-descriptions-item label="平均耗时"><span class="num">{{ fn.avgMs ? `${fn.avgMs} ms` : '—' }}</span></a-descriptions-item>
            <a-descriptions-item label="近 7 天调用"><span class="num">{{ fn.calls7d.toLocaleString('zh-CN') }} 次</span></a-descriptions-item>
          </a-descriptions>
        </div>
      </div>

      <div v-else-if="tab === 'test'" class="fn-body fn-test">
        <div class="fn-tcol">
          <div class="fn-thead"><span class="t-14 fw-600 c-1">输入</span><a class="t-12" @click="resetTest">恢复示例</a></div>
          <a-textarea v-model:value="testInput" class="fn-ta num" :auto-size="false" />
          <div class="fn-trun">
            <span class="t-12 c-3">在测试环境运行，不写入任何数据</span>
            <a-button :loading="running" @click="runTest">运行</a-button>
          </div>
        </div>
        <div class="fn-tcol">
          <div class="fn-thead">
            <span class="t-14 fw-600 c-1">{{ testOut ? '本次运行结果' : '示例输出' }}</span>
            <span v-if="testOut" class="t-12 c-2 fn-tok"><span class="fn-dot tl-normal" />成功 · 耗时 <span class="num">{{ testOut.ms }} ms</span> · <span class="num">{{ testOut.at }}</span> · 运行于 测试环境 · 不写入数据</span>
            <span v-else class="t-12 c-3 fn-tok">还没有运行；下面是函数登记的示例输出</span>
          </div>
          <pre class="fn-pre fn-out num">{{ testOut ? testOut.text : JSON.stringify(fn.sampleOut, null, 2) }}</pre>
          <div class="t-12 c-3">出参类型 <span class="num">{{ fn.output.type }}</span> · {{ fn.output.desc }}</div>
        </div>
      </div>

      <div v-else class="fn-body fn-vers">
        <div class="fn-vtable">
          <a-table size="small" row-key="ver" :columns="vcols" :data-source="vPaged.items" :pagination="false">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'ver'" class="num c-1">{{ record.ver }}</span>
              <span v-else-if="column.key === 'time'" class="num c-2">{{ record.time }}</span>
              <a-tag v-else-if="column.key === 'status'" >{{ record.status }}</a-tag>
              <span v-else-if="column.key === 'op'">
                <a v-if="record.cr" @click="router.push({ path: '/build/ontology/releases', query: { cr: record.cr } })">查看 {{ record.cr }}</a>
                <a-tooltip v-else-if="record.status !== '当前'" :title="reason"><a :class="{ 'fn-dis': readonly }" @click="!readonly && rollback(record)">回滚到此版本</a></a-tooltip>
              </span>
            </template>
          </a-table>
        </div>
        <AppPagination v-model:page="vpage" variant="simple" :total="versions.length" :size="VSIZE" unit="个版本" class="fn-pager" />
      </div>
    </div>
  </div>

  <a-drawer :open="formOpen" :width="640" :title="`编辑函数 · ${fn.name}`" @close="closeForm">
    <a-form layout="vertical" :model="form">
      <a-form-item label="函数名"><a-input :value="fn.name" disabled /></a-form-item>
      <a-form-item label="中文名" required><a-input v-model:value="form.title" /></a-form-item>
      <a-form-item label="说明"><a-textarea v-model:value="form.desc" :rows="2" /></a-form-item>
      <a-form-item label="超时（秒）"><a-input-number v-model:value="form.timeoutS" :min="1" :max="60" /></a-form-item>
      <a-form-item label="代码"><a-textarea v-model:value="form.code" :rows="10" class="num" /></a-form-item>
    </a-form>
    <div class="t-12 c-3">保存后生成新版本 <span class="num">{{ bump(fn.version) }}</span>；入参 / 出参类型变化会在发布校验里检查使用它的动作和规则。</div>
    <template #footer>
      <div class="fn-foot">
        <a-button @click="closeForm">取消</a-button>
        <a-button type="primary" :disabled="readonly || !formDirty" @click="save">保存</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.fn { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.fn-list { width: 340px; flex: none; }
.fn-filters { display: flex; gap: 8px; padding: 12px 16px; flex: none; }
.fn-sel { width: 100px; flex: none; }
.fn-items { flex: 1; min-height: 0; overflow: hidden; }
.fn-item { padding: 10px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.fn-item:hover { background: var(--g2); }
.fn-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.fn-l1 { display: flex; align-items: center; gap: 8px; min-width: 0; }
.fn-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.fn-badge { margin-left: auto; flex: none; }
.fn-pager { padding: 6px 16px 8px; }
.fn-detail { flex: 1; min-width: 0; }
.fn-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 4px; }
.fn-title { display: flex; align-items: center; gap: 10px; }
.fn-sub { padding: 0 20px; }
.fn-tabs { padding: 0 20px; flex: none; }
.fn-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.fn-body { flex: 1; min-height: 0; padding: 12px 20px 16px; }
.fn-def { display: flex; gap: 24px; }
.fn-col { flex: 1; min-width: 0; }
.fn-side { width: 340px; flex: none; }
.fn-h { margin: 12px 0 6px; }
.fn-h:first-child { margin-top: 0; }
.fn-pre { background: var(--g2); border: var(--border); border-radius: var(--radius); padding: 8px 12px; margin: 0; color: var(--g9); white-space: pre-wrap; }
.fn-obj { margin-left: 8px; }
.fn-desc :deep(.ant-descriptions-item-label) { width: 110px; }
.fn-pend { padding: 6px 0; }
.fn-use { display: flex; align-items: center; gap: 4px; padding: 6px 0; border-bottom: var(--border); }
.fn-test { display: flex; gap: 20px; }
.fn-tcol { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.fn-thead { display: flex; justify-content: space-between; align-items: center; }
.fn-ta { flex: 1; min-height: 0; resize: none; }
.fn-out { flex: 1; min-height: 0; overflow: hidden; }
.fn-trun { display: flex; justify-content: space-between; align-items: center; }
.fn-tok { display: inline-flex; align-items: center; gap: 6px; }
.fn-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.fn-vers { display: flex; flex-direction: column; padding-bottom: 0; }
.fn-vtable { flex: 1; min-height: 0; }
.fn-vers .fn-pager { padding: 6px 0 8px; }
.fn-dis { color: var(--g6); cursor: not-allowed; }
.fn-foot { display: flex; justify-content: flex-end; gap: 8px; }
</style>
