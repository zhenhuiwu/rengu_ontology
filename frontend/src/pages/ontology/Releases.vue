<script setup lang="ts">
/** Complete ontology publications only; individual saves stay outside this page. */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useDebouncedRef } from '@/composables/useDebouncedRef';
import { useReadonly } from '@/composables/useReadonly';
import { currentApiContext, isApiError } from '@/api/http';
import { loadMyProfile } from '@/api/profile';
import { fetchCurrentRelease, fetchReleasePage, loadReleases, type ReleaseDto } from '@/api/releases';
import { fetchCurrentAfterWrite, publishFormalRelease } from '@/api/static';
import { useAppStore } from '@/stores/app';

const route = useRoute(), router = useRouter(), app = useAppStore();
const contextKey = () => JSON.stringify(currentApiContext());
const { readonly, reason } = useReadonly();
const search = ref(''), keyword = useDebouncedRef(search);
const tableBox = ref<HTMLElement | null>(null);
const pq = usePagedQuery({
  defaultSize: 20, filters: [keyword],
  adaptive: { container: tableBox, rowHeight: 64, rowSelector: '.ant-table-tbody > tr.ant-table-row', reserve: 56, min: 5, max: 50 },
});
const records = ref<ReleaseDto[]>([]), total = ref(0), current = ref<string | null>(null);
const loading = ref(false), error = ref('');
const mayPublish = ref(false), publishOpen = ref(false), note = ref(''), publishing = ref(false);
const issues = ref<{ section: string; id: string; message: string }[]>([]);
let disposed = false, sequence = 0, profileSequence = 0, publishSequence = 0;
let request: AbortController | undefined;
const readKey = () => JSON.stringify([contextKey(), pq.page.value, pq.size.value, keyword.value]);
async function reload() {
  const seq = ++sequence, key = readKey();
  request?.abort(); request = new AbortController();
  loading.value = true; error.value = '';
  try {
    const page = await fetchReleasePage({ page: pq.page.value, size: pq.size.value, kw: keyword.value || undefined }, request.signal);
    if (disposed || seq !== sequence || key !== readKey()) return;
    records.value = page.items; total.value = page.total; current.value = page.current;
    if (page.page !== pq.page.value) pq.page.value = page.page;
  } catch (cause) {
    if (!disposed && seq === sequence && key === readKey() && (cause as Error).name !== 'AbortError') {
      records.value = []; total.value = 0;
      error.value = cause instanceof Error ? cause.message : '发布记录加载失败';
    }
  } finally { if (!disposed && seq === sequence) loading.value = false; }
}
watch(contextKey, async (key) => {
  ++sequence; request?.abort(); records.value = []; total.value = 0; current.value = null; error.value = '';
  ++publishSequence; publishing.value = false; publishOpen.value = false; note.value = ''; issues.value = [];
  const seq = ++profileSequence; mayPublish.value = false;
  try {
    const profile = await loadMyProfile();
    if (!disposed && seq === profileSequence && key === contextKey()) mayPublish.value = profile?.capabilities.publish === true;
  } catch { /* The list remains readable; publishing requires a confirmed capability. */ }
}, { immediate: true, flush: 'sync' });
watch([contextKey, keyword, pq.page, pq.size], (next, previous) => {
  if (previous && next[0] === previous[0] && next[1] !== previous[1] && pq.page.value !== 1) return;
  void reload();
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++sequence; ++profileSequence; ++publishSequence; request?.abort(); });

const publishDisabled = computed(() => readonly.value || !mayPublish.value || publishing.value);
function openPublish() {
  if (publishDisabled.value) return;
  note.value = ''; issues.value = []; publishOpen.value = true;
}
async function publish() {
  if (!publishOpen.value || publishDisabled.value) return;
  const seq = ++publishSequence, key = contextKey();
  const valid = () => !disposed && seq === publishSequence && key === contextKey();
  publishing.value = true; issues.value = [];
  try {
    const base = await fetchCurrentAfterWrite();
    if (!valid() || readonly.value || !mayPublish.value) return;
    const result = await publishFormalRelease(base.lockVersion, note.value.trim());
    if (!valid()) return;
    app.ontologyVersion = result.ver;
    publishOpen.value = false;
    message.success('本体已发布为 ' + result.ver);
    try {
      const [version] = await Promise.all([fetchCurrentRelease(), loadReleases()]);
      if (!valid()) return;
      if (version.current) app.ontologyVersion = version.current;
      app.ontologyNextVersion = version.next;
      app.ontologyPublishedAt = version.formalRelease?.publishedAt ?? '';
      if (pq.page.value !== 1) await router.replace({ query: { ...route.query, page: undefined } });
      else await reload();
    } catch (cause) {
      if (valid()) message.warning('本体已发布，记录刷新失败：' + (cause instanceof Error ? cause.message : '请刷新页面'));
    }
  } catch (cause) {
    if (!valid()) return;
    if (isApiError(cause) && cause.code === 'FORMAL_RELEASE_BLOCKED') {
      const details = cause.details as { issues?: typeof issues.value } | null;
      if (Array.isArray(details?.issues)) issues.value = details.issues;
    }
    message.error(cause instanceof Error ? cause.message : '本体发布失败');
  } finally { if (valid()) publishing.value = false; }
}
function viewVersion(release: ReleaseDto) {
  void router.push({ path: '/build/ontology/overview', query: { ontology: currentApiContext().ontologyId, ver: release.ver } });
}
const columns = [
  { title: '版本', key: 'version', width: 230 },
  { title: '发布说明', key: 'note', ellipsis: true },
  { title: '发布人', dataIndex: 'by', key: 'by', width: 160 },
  { title: '发布时间', dataIndex: 'publishedAt', key: 'publishedAt', width: 200 },
  { title: '操作', key: 'action', width: 130 },
];
</script>

<template>
  <section class="panel release-history">
    <div class="release-toolbar">
      <span class="t-16 fw-600">发布记录</span>
      <div class="release-tools">
        <a-input-search v-model:value="search" allow-clear placeholder="搜索版本或发布说明" aria-label="搜索版本或发布说明" />
        <a-button :loading="loading" @click="reload">刷新</a-button>
        <a-tooltip :title="readonly ? reason : !mayPublish ? '当前账号没有发布权限' : ''">
          <a-button type="primary" :disabled="publishDisabled" @click="openPublish">发布本体</a-button>
        </a-tooltip>
      </div>
    </div>
    <a-alert v-if="error" type="error" show-icon :message="error" class="release-error">
      <template #action><a-button size="small" @click="reload">重试</a-button></template>
    </a-alert>
    <div ref="tableBox" class="release-table">
      <a-table row-key="ver" size="middle" :columns="columns" :data-source="records" :pagination="false"
        :loading="loading" :scroll="{ x: 960 }" table-layout="fixed"
        :locale="{ emptyText: error ? '发布记录暂不可用' : keyword ? '没有符合条件的发布记录' : '暂无本体发布记录' }">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'version'" class="release-version">
            <span class="num fw-600">{{ record.ver }}</span><a-tag v-if="record.ver === current" color="blue">当前发布</a-tag>
          </span>
          <span v-else-if="column.key === 'note'" :title="record.note">{{ record.note || '—' }}</span>
          <a-button v-else-if="column.key === 'action'" type="link" @click="viewVersion(record)">查看本体</a-button>
        </template>
      </a-table>
    </div>
    <div class="release-footer">
      <AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value" v-model:mode="pq.mode.value"
        :total="total" :empty="false" />
    </div>
  </section>
  <a-modal :open="publishOpen" title="发布本体" ok-text="发布" :confirm-loading="publishing"
    :closable="!publishing" :mask-closable="!publishing" :keyboard="!publishing"
    @cancel="publishOpen = false" @ok="publish">
    <a-form layout="vertical">
      <a-form-item label="发布说明"><a-textarea v-model:value="note" :rows="3" :maxlength="500" placeholder="填写本次本体发布的内容（可选）" /></a-form-item>
    </a-form>
    <a-alert v-if="issues.length" type="error" show-icon message="发布校验未通过">
      <template #description><div v-for="(issue, i) in issues" :key="i">{{ issue.section }} · {{ issue.id }}：{{ issue.message }}</div></template>
    </a-alert>
  </a-modal>
</template>

<style scoped>
.release-history { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.release-toolbar { flex: none; min-height: 72px; padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: var(--border); }
.release-tools { display: flex; align-items: center; gap: 12px; }
.release-tools :deep(.ant-input-search) { width: 320px; }
.release-error { flex: none; margin: 16px 20px 0; }
.release-table { flex: 1; min-height: 0; min-width: 0; overflow: auto; padding: 0 20px; }
.release-version { display: inline-flex; align-items: center; gap: 12px; }
.release-version :deep(.ant-tag) { margin: 0; }
.release-footer { flex: none; padding: 12px 20px; border-top: var(--border); }
@media (max-width: 1100px) { .release-toolbar { align-items: flex-start; flex-wrap: wrap; } .release-tools { flex: 1; flex-wrap: wrap; } .release-tools :deep(.ant-input-search) { width: auto; min-width: 220px; flex: 1; } }
</style>
