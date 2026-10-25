<script setup lang="ts">
/**
 * 问数工作台（/use/qa/workbench）。
 * 布局：会话列表 | 对话（唯一滚动区）+ 输入框 | 当前对象与可执行动作。
 * 抽屉用公共 DetailDrawer：?drawer=region:REG-EC；确认卡用公共 ConfirmActionModal，打开时 URL 带 ?confirm=1，可直接复现截图。
 * 其余可复现状态：?pin=1 固定到页面弹窗；?panel=favorites|pages 左下抽屉；?apply=1 申请权限抽屉。
 * 答案里的“建议动作”与右栏动作走同一张确认卡，并高亮右栏那一行；动作状态只在右栏显示。
 * 范围/口径是会话级的：中途修改在对话里插入“从这里开始按 … 回答”，之前的答案保留各自依据。
 */
import { onMounted, ref, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { ShareAltOutlined, LayoutOutlined, EllipsisOutlined } from '@ant-design/icons-vue';
import { useDrawer } from '@/composables/useDrawer';
import { useConfirmStore } from '@/stores/confirm';
import { REGION_EC } from '@/mock';
import { useQueryParam } from './useQueryParam';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
import QaSessionPanel from './components/QaSessionPanel.vue';
import QaConversation from './components/QaConversation.vue';
import QaComposer from './components/QaComposer.vue';
import QaObjectPanel from './components/QaObjectPanel.vue';
import QaPinModal from './components/QaPinModal.vue';
import QaListDrawer from './components/QaListDrawer.vue';
import QaApplyDrawer from './components/QaApplyDrawer.vue';

const ACTION = '发放复购召回优惠券';
const route = useRoute();
const router = useRouter();
const drawer = useDrawer();
const confirm = useConfirmStore();
const submitted = ref(false);
const pending = computed(() => confirm.open && confirm.req?.title === ACTION);
const pinQ = useQueryParam('pin', '');
const pin = computed({ get: () => (readonly.value ? '' : pinQ.value), set: (v: string) => (pinQ.value = v) });
const panel = useQueryParam('panel', '');
const apply = useQueryParam('apply', '');
const applied = ref(false);

// 右栏动作行只在确认卡打开期间高亮；取消/关闭后恢复“发起”，提交后才变“待审批”
const highlight = pending;
const { readonly, reason } = useReadonly();
/* 预览：回答只含被预览用户范围内的数据，会话顶部写明（问数涉及的对象：订单、门店、会员） */
const pv = usePreview();
const pvText = computed(() => ['订单', '门店', '会员'].map((o) => pv.scopeLabel(o).replace('数据范围：', '')).join('；'));

// 会话级范围与口径
const scope = ref('华东');
const caliber = ref('不含税');
const tips = ref<string[]>([]);
watch([scope, caliber], ([s, c]) => { tips.value.push(`从这里开始按 大区 = ${s}、口径 = ${c} 回答`); });

function onPinned(page: string) { pin.value = ''; message.success(`已固定到「${page}」`); }
function onApplied() { apply.value = ''; applied.value = true; message.success('申请已提交，等待审批'); }

function setConfirmQuery(on: boolean) {
  const q = { ...route.query };
  if (on) q.confirm = '1'; else delete q.confirm;
  router.replace({ query: q });
}

async function runAction() {
  if (readonly.value) return;
  setConfirmQuery(true);
  const ok = await confirm.ask({
    title: ACTION,
    tag: '待确认',
    target: '客户群 · 华东大区 · 近 60 天未复购',
    fields: [
      { label: '动作类型', value: '发放复购召回优惠券 v3' },
      { label: '参数', value: '面额 ¥30，满 ¥199 可用，有效期 14 天' },
      { label: '影响', value: '3,812 位客户（已排除 30 天内已领券 214 人）' },
      { label: '预算', value: '营销预算 -¥114,360' },
      { label: '提交条件', value: '已满足：操作人有营销权限、预算余额充足' },
      { label: '审批人', value: '赵磊（营销总监）' },
      { label: '写回', value: 'CRM 营销系统，外部失败则整体不改' },
      { label: '幂等键', value: 'act-20261025-ec-0193' },
    ],
    impact: '提交后进入审批；外部系统超时会标记为“结果未知”并进入对账，不会自动重试。',
    irreversible: true,
    okText: '确认提交',
  });
  setConfirmQuery(false);
  if (ok) { submitted.value = true; message.success('已提交，等待 赵磊 审批'); }
}

onMounted(() => { if (route.query.confirm === '1') runAction(); });
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-button><template #icon><ShareAltOutlined /></template>分享</a-button>
    <a-tooltip :title="reason"><a-button :disabled="readonly"><template #icon><LayoutOutlined /></template>把会话整理成页面</a-button></a-tooltip>
    <a-button><template #icon><EllipsisOutlined /></template></a-button>
  </Teleport>
  <div class="qa-wb">
    <QaSessionPanel @panel="(v) => (panel = v)" />
    <section class="panel qa-center">
      <div v-if="pv.active.value" class="qa-pv t-12 c-2">{{ pv.userName.value }} 的数据范围：{{ pvText }}。回答只含范围内的数据，预览中不能追问和发起动作。</div>
      <QaConversation :applied="applied" :tips="tips" @suggest="runAction" @pin="pin = '1'" @apply="apply = '1'" />
      <QaComposer v-model:scope="scope" v-model:caliber="caliber" />
    </section>
    <QaObjectPanel :pending="pending" :submitted="submitted" :highlight="highlight" @run="runAction" @detail="drawer.open('region', REGION_EC.id)" />
  </div>
  <QaPinModal :open="pin === '1'" card="上个月华东大区复购客户的销售额（按省份）" @close="pin = ''" @done="onPinned" />
  <QaListDrawer :panel="panel" @close="panel = ''" @ask="panel = ''" />
  <QaApplyDrawer :open="apply === '1'" @close="apply = ''" @submit="onApplied" />
</template>

<style scoped>
.qa-wb { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.qa-center { flex: 1; background: var(--g3); }
.qa-pv { flex: none; padding: 8px 16px; border-bottom: var(--border); background: var(--g2); }
</style>
