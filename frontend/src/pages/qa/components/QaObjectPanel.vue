<script setup lang="ts">
/** 右栏：当前对象（华东大区）摘要、状态时间线、可执行动作（含执行状态） */
import { useReadonly } from '@/composables/useReadonly';
import { ref } from 'vue';
import { QuestionCircleOutlined, ClockCircleOutlined, SyncOutlined } from '@ant-design/icons-vue';
import { REGION_EC, QA_TIMELINE, QA_UNKNOWN_LABEL, type QaTimelineLevel } from '@/mock';

/** highlight：从答案里的“建议动作”打开确认卡时，高亮对应动作行；状态只在本栏显示 */
defineProps<{ pending: boolean; submitted: boolean; highlight?: boolean }>();
const { readonly, reason } = useReadonly();
const emit = defineEmits<{ (e: 'run'): void; (e: 'detail'): void }>();
const tab = ref('summary');
// 时间线颜色统一用 tokens.css 的语义色：灰=正常，橙=需关注（结果未知也是橙 + 问号），红=失败
const dot: Record<QaTimelineLevel, string> = { warn: 'var(--tl-attention)', error: 'var(--tl-failed)', unknown: 'var(--tl-attention)', normal: 'var(--tl-normal)' };
</script>

<template>
  <aside class="panel qa-obj">
    <div class="qa-obj-sec">
      <div class="qa-obj-title">
        <span class="t-16 fw-600">{{ REGION_EC.name }}</span>
        <a-tag color="warning">{{ REGION_EC.status }}</a-tag>
        <a class="qa-obj-link" @click="emit('detail')">打开对象详情</a>
      </div>
      <div class="t-12 c-3">大区 · {{ REGION_EC.id }} · 本轮回答引用</div>
      <a-tabs v-model:activeKey="tab" size="small" class="qa-obj-tabs">
        <a-tab-pane key="summary" tab="摘要" />
        <a-tab-pane key="metrics" tab="指标" />
        <a-tab-pane key="rel" tab="关联" />
      </a-tabs>
      <a-descriptions size="small" :column="1" :label-style="{ width: '72px' }" class="qa-obj-desc">
        <a-descriptions-item label="负责人">{{ REGION_EC.owner }}</a-descriptions-item>
        <a-descriptions-item label="覆盖">{{ REGION_EC.provinces }} 省 · {{ REGION_EC.stores }} 家门店</a-descriptions-item>
        <a-descriptions-item label="销售额"><span class="num">{{ REGION_EC.sales }}（9 月）</span></a-descriptions-item>
        <a-descriptions-item label="复购占比"><span class="num">62.0%（8 月 63.6%）</span></a-descriptions-item>
        <a-descriptions-item label="上级">{{ REGION_EC.parent }}</a-descriptions-item>
      </a-descriptions>
    </div>

    <div class="qa-obj-sec">
      <div class="qa-obj-h"><span class="t-16 fw-600">状态时间线</span><a class="c-3">全部</a></div>
      <a-timeline class="qa-obj-tl">
        <a-timeline-item v-for="t in QA_TIMELINE" :key="t.time" :color="dot[t.level]">
          <template v-if="t.level === 'unknown'" #dot><QuestionCircleOutlined class="tl-attention" /></template>
          <div class="qa-obj-tl-row"><span :class="{ 'tl-attention': t.level === 'unknown' }">{{ t.title }}</span><span class="t-12 c-3 num">{{ t.time.slice(5) }}</span></div>
          <div class="c-3 qa-obj-ell"><span v-if="t.level === 'unknown'" class="tl-attention">{{ QA_UNKNOWN_LABEL }} · </span>{{ t.desc }}</div>
        </a-timeline-item>
      </a-timeline>
    </div>

    <div class="qa-obj-sec qa-obj-last">
      <div class="qa-obj-h"><span class="t-16 fw-600">可执行动作</span></div>
      <div class="qa-act" :class="{ hl: highlight }">
        <div class="qa-act-main">
          <div>发放复购召回优惠券</div>
          <div v-if="submitted" class="c-3 qa-obj-ell">王敏 10-25 15:36 提交 · 待 赵磊 审批</div>
          <div v-else-if="pending" class="c-3 qa-obj-ell">王敏 10-25 15:36 发起 · 赵磊 审批</div>
          <div v-else class="c-3 qa-obj-ell">条件满足 · 影响 3,812 位客户</div>
        </div>
        <span v-if="submitted" class="qa-act-status"><ClockCircleOutlined />待审批</span>
        <span v-else-if="pending" class="qa-act-status"><ClockCircleOutlined />待确认</span>
        <a-tooltip v-else :title="reason"><a-button size="small" type="primary" ghost :disabled="readonly" @click="emit('run')"><span>发起</span></a-button></a-tooltip>
      </div>
      <div class="qa-act">
        <div class="qa-act-main">
          <div>指派大区经理跟进</div>
          <div class="c-3 qa-obj-ell">王敏 10-25 15:20 发起 · 指派 陈杰</div>
        </div>
        <span class="qa-act-status"><SyncOutlined />执行中</span>
      </div>
      <div class="qa-act">
        <div class="qa-act-main">
          <div class="c-3">调整大区预算</div>
          <div class="c-3 qa-obj-ell">条件不满足 · 需财务审批权限</div>
        </div>
        <a-tooltip :title="reason || '条件不满足'"><a-button size="small" disabled><span>发起</span></a-button></a-tooltip>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.qa-obj { width: 344px; flex: none; }
.qa-obj-sec { padding: 12px 16px; border-bottom: var(--border); }
.qa-obj-last { border-bottom: 0; }
.qa-obj-title { display: flex; align-items: center; gap: 8px; }
.qa-obj-title :deep(.ant-tag) { margin-inline-end: 0; }
.qa-obj-link { margin-left: auto; }
.qa-obj-tabs :deep(.ant-tabs-nav) { margin: 4px 0 8px; }
.qa-obj-desc :deep(.ant-descriptions-item) { padding-bottom: 4px !important; }
.qa-obj-h { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.qa-obj-tl :deep(.ant-timeline-item) { padding-bottom: 6px; }
.qa-obj-tl :deep(.ant-timeline-item-last) { padding-bottom: 0; }
.qa-obj-tl :deep(.ant-timeline-item-last > .ant-timeline-item-content) { min-height: 0; }
.qa-obj-tl-row { display: flex; justify-content: space-between; }
.qa-obj-ell { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qa-act { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-top: var(--border); }
.qa-act:first-of-type { border-top: 0; padding-top: 0; }
.qa-act-main { flex: 1; min-width: 0; }
.qa-act.hl { background: var(--brand-bg); border-radius: var(--radius); border-top-color: transparent; margin: 0 -8px; padding: 8px; transition: background .3s; }
.qa-act.hl + .qa-act { border-top-color: transparent; }
.qa-act :deep(.ant-tag) { margin-inline-end: 0; }
/* 状态：无边框灰字 + 小图标；只有可点的“发起”是带边框的按钮 */
.qa-act-status { flex: none; display: inline-flex; align-items: center; gap: 4px; color: var(--g8); font-size: var(--fs-14); line-height: 24px; }
.qa-obj-tl :deep(.ant-timeline-item-head-custom) { font-size: var(--fs-14); }
</style>
