<script setup lang="ts">
/**
 * 详情抽屉的一层（DetailDrawer 内部使用，页面不要直接用）。
 * 页签：属性 (+ resolver 返回的额外页签，如“指标”) + 时间线 + 关联对象。
 * 本层 provide 自己的层级，所以抽屉内容里的 <ObjRef> / useDrawer().open() 会自动打开第二层。
 */
import { computed, provide, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { LeftOutlined } from '@ant-design/icons-vue';
import { DRAWER_LEVEL, useDrawer, type DrawerLevel, type DrawerRef } from '@/composables/useDrawer';
import { useConfirmStore } from '@/stores/confirm';
import { resolveObject, checkObject, type ObjKpi } from './objectRegistry';
import StatusTimeline from './StatusTimeline.vue';
import AppPagination from './AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { orderById, storeById, fmtMoney } from '@/mock';
import { anyReal } from '@/api/config';

const props = defineProps<{ target: DrawerRef | null; level: DrawerLevel; parentTitle?: string }>();
provide(DRAWER_LEVEL, props.level);

const { open, back, close } = useDrawer();
const confirm = useConfirmStore();
const view = computed(() => (props.target ? resolveObject(props.target.kind, props.target.id) : null));
const pinned = computed(() => view.value?.tabs?.filter((t) => t.pinToProps && t.kpis?.length) ?? []);
const tab = ref('props');
// 抽屉主体里的列表（关联对象、时间线）直接用 simple 分页，不跳“查看全部”
const REL_SIZE = 10;
const TL_SIZE = 8;
const relPage = ref(1);
const tlPage = ref(1);
const relPaged = computed(() => paginate(view.value?.related ?? [], { page: relPage.value, size: REL_SIZE }));
const tlPaged = computed(() => paginate(view.value?.timeline ?? [], { page: tlPage.value, size: TL_SIZE }));
watch(() => `${props.target?.kind}:${props.target?.id}`, () => { tab.value = 'props'; relPage.value = 1; tlPage.value = 1; });
// 开发环境：关联对象里有解析不到的（点开会是空抽屉）就警告
if (import.meta.env.DEV) {
  watch(view, (v) => v?.related?.forEach((r) => checkObject(r.kind, r.id, `${v.kind}:${v.id} 的关联对象`)), { immediate: true });
}

const onClose = () => (props.level === 2 ? back() : close());
// 属性值单行省略：字符串/数字给 title 看全文
const valTitle = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : undefined);
const toneClass = (k: ObjKpi) => (k.tone === 'down' ? 'c-error' : k.tone === 'up' ? 'c-2' : 'c-3');

// 订单动作“补发出库单”：只在订单抽屉里提供（首页等列表只看不改），仅已出库订单可用
const resendOrder = computed(() => {
  if (anyReal()) return null;
  if (view.value?.kind !== 'order') return null;
  const o = orderById(view.value.id);
  return o && o.status === '已出库' ? o : null;
});
async function resend() {
  if (anyReal()) return;
  const o = resendOrder.value; if (!o) return;
  const ok = await confirm.ask({
    title: '补发出库单',
    target: `订单 ${o.id}`,
    fields: [
      { label: '门店', value: storeById(o.storeId)?.name ?? '--' },
      { label: '发货仓', value: o.warehouse },
      { label: '实付金额', value: `${fmtMoney(o.amount)} 元` },
    ],
    impact: '写回 WMS，生成新的出库单；原出库单保留，状态不变。',
    irreversible: true,
  });
  if (ok) message.success(`已提交补发：${o.id}，可在动作中心查看执行记录`);
}
</script>

<template>
  <a-drawer
    :open="!!target"
    :width="640"
    :closable="false"
    :body-style="{ padding: '0 24px 16px' }"
    @close="onClose"
  >
    <template #title>
      <div class="dd-title">
        <a-button v-if="level === 2" size="small" type="text" class="dd-back" :title="parentTitle ? `返回 ${parentTitle}` : '返回'" @click="back">
          <LeftOutlined />
        </a-button>
        <template v-if="view">
          <span class="t-16 fw-600 c-1">{{ view.title }}</span>
          <a-tag v-if="view.status" :color="view.status.color">{{ view.status.text }}</a-tag>
          <span class="t-14 fw-400 c-2 num dd-code">{{ view.kindLabel }} · {{ view.code }}</span>
        </template>
        <span v-else class="t-16 fw-600">未找到对象</span>
      </div>
    </template>
    <template #extra>
      <a-space>
        <a-button v-if="resendOrder" size="small" @click="resend">补发出库单</a-button>
        <a-button size="small" type="text" title="关闭全部" @click="close"><CloseOutlined /></a-button>
      </a-space>
    </template>

    <template v-if="view">
      <a-tabs v-model:activeKey="tab" size="small">
        <a-tab-pane key="props" tab="属性">
          <a-descriptions :column="2" size="small" bordered :label-style="{ width: '96px' }" class="dd-desc">
            <a-descriptions-item v-for="f in view.fields" :key="f.label" :label="f.label">
              <span class="num dd-val" :title="valTitle(f.value)">{{ f.value }}</span>
            </a-descriptions-item>
          </a-descriptions>
          <section v-for="t in pinned" :key="t.key" class="dd-sec">
            <div class="t-14 fw-600 c-1 dd-sec-h">关键{{ t.title }}</div>
            <div class="dd-kpis">
              <div v-for="k in t.kpis" :key="k.label" class="dd-kpi">
                <div class="t-12 c-3">{{ k.label }}</div>
                <div class="dd-kv"><span class="t-16 fw-600 c-1 num">{{ k.value }}</span><span v-if="k.unit" class="t-12 c-2">{{ k.unit }}</span><span v-if="k.compare" class="t-12 dd-cmp" :class="toneClass(k)" :title="k.compare">{{ k.compare }}</span></div>
              </div>
            </div>
          </section>
        </a-tab-pane>
        <a-tab-pane v-for="t in view.tabs ?? []" :key="t.key" :tab="t.title">
          <a-table
            v-if="t.kpis?.length" size="small" :pagination="false" row-key="label" :data-source="t.kpis"
            :columns="[{ title: '指标', dataIndex: 'label', key: 'label' }, { title: '值', key: 'value', align: 'right' }, { title: '对比', key: 'compare' }]"
          >
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'value'" class="num">{{ record.value }}<span v-if="record.unit" class="c-3"> {{ record.unit }}</span></span>
              <span v-else-if="column.key === 'compare'" :class="toneClass(record)">{{ record.compare ?? '--' }}</span>
            </template>
          </a-table>
          <a-descriptions v-if="t.fields?.length" :column="2" size="small" bordered :label-style="{ width: '96px' }" class="dd-sec dd-desc">
            <a-descriptions-item v-for="f in t.fields" :key="f.label" :label="f.label"><span class="num dd-val" :title="valTitle(f.value)">{{ f.value }}</span></a-descriptions-item>
          </a-descriptions>
        </a-tab-pane>
        <a-tab-pane v-if="view.timeline" key="timeline" tab="时间线">
          <StatusTimeline :items="tlPaged.items" />
          <AppPagination v-model:page="tlPage" variant="simple" :total="tlPaged.total" :size="TL_SIZE" />
        </a-tab-pane>
        <a-tab-pane v-if="view.related?.length" key="related" tab="关联对象">
          <a-table
            size="small" :pagination="false" row-key="id"
            :data-source="relPaged.items"
            :columns="[{ title: '对象', dataIndex: 'label', key: 'label' }, { title: '说明', dataIndex: 'note', key: 'note' }]"
          >
            <template #bodyCell="{ column, record }">
              <a v-if="column.key === 'label'" @click="open(record.kind, record.id, { level: 2 })">{{ record.label }}</a>
              <span v-else class="num">{{ record.note }}</span>
            </template>
          </a-table>
          <AppPagination v-model:page="relPage" variant="simple" :total="relPaged.total" :size="REL_SIZE" :empty="false" />
        </a-tab-pane>
      </a-tabs>
    </template>
    <a-empty v-else description="对象不存在或已被删除" />
    <slot />
  </a-drawer>
</template>

<style scoped>
.dd-title { display: flex; align-items: center; gap: 8px; }
.dd-back { margin-left: -8px; }
.dd-code { white-space: nowrap; }
.dd-kv { display: flex; align-items: baseline; gap: 4px; min-width: 0; white-space: nowrap; }
.dd-cmp { margin-left: 8px; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.dd-desc :deep(table) { table-layout: fixed; width: 100%; }
.dd-desc :deep(.ant-descriptions-item-label) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dd-desc :deep(.ant-descriptions-item-content) { white-space: nowrap; overflow: hidden; max-width: 0; }
.dd-val { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.dd-sec { margin-top: 16px; }
.dd-sec-h { margin-bottom: 8px; }
.dd-kpis { display: grid; grid-template-columns: repeat(2, 1fr); border: var(--border); border-radius: var(--radius); }
.dd-kpi { padding: 12px 16px; display: flex; flex-direction: column; gap: 2px; }
.dd-kpi:nth-child(2n) { border-left: var(--border); }
.dd-kpi:nth-child(n + 3) { border-top: var(--border); }
</style>
