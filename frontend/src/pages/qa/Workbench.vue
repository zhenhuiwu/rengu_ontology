<script setup lang="ts">
/**
 * 问数工作台（/use/qa/workbench）。
 * 布局：会话列表 | 对话（唯一滚动区）+ 输入框 | 当前对象与可执行动作。
 * 抽屉用公共 DetailDrawer：?drawer=region:REG-EC；确认卡用公共 ConfirmActionModal，打开时 URL 带 ?confirm=1，可直接复现截图。
 * 其余可复现状态：?pin=1 固定到页面弹窗；?panel=favorites|pages 左下抽屉；?apply=1 申请权限抽屉。
 * 答案里的“建议动作”与右栏动作走同一张确认卡，并高亮右栏那一行；动作状态只在右栏显示。
 * 范围/口径是会话级的：中途修改在对话里插入“从这里开始按 … 回答”，之前的答案保留各自依据。
 */
import { onMounted, ref, computed, watch, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { ShareAltOutlined, LayoutOutlined, EllipsisOutlined } from '@ant-design/icons-vue';
import { useDrawer } from '@/composables/useDrawer';
import { useConfirmStore } from '@/stores/confirm';
import { REGION_EC, QA_APPLY } from '@/mock';
import { useQueryParam } from './useQueryParam';
import { useReadonly } from '@/composables/useReadonly';
import { usePreview } from '@/composables/usePreview';
import { useQaSession, errText } from './useQaSession';
import * as qaApi from '@/api/qa';
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
/** 真接口：本次提交的 action_run，右栏「待审批」可跳动作中心 */
const submittedRun = ref<{ id: string; approver: string | null }>();
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
const sess = useQaSession();
/* 真接口：范围/口径跟着当前会话（W3）；用户改了才 PATCH（W4），系统提示由后台作为 system 轮次追加 */
let syncing = false;
watch([scope, caliber], async ([s, c]) => {
  if (!sess.real) { tips.value.push(`从这里开始按 大区 = ${s}、口径 = ${c} 回答`); return; }
  const d = sess.detail.value;
  if (syncing || !d || (d.scope === s && d.caliber === c)) return;
  try { sess.detail.value = await qaApi.patchSession(d.id, { scope: s, caliber: c }); }
  catch (e) { message.error(`修改范围口径失败：${errText(e)}`); syncing = true; scope.value = d.scope ?? '华东'; caliber.value = d.caliber; await nextTick(); syncing = false; }
});
if (sess.real) {
  watch(() => sess.detail.value, async (d) => {
    if (!d) return;
    syncing = true; scope.value = d.scope ?? '华东'; caliber.value = d.caliber; await nextTick(); syncing = false;
  });
  watch(() => sess.activeId.value, (id) => { if (id && sess.detail.value?.id !== id) sess.loadDetail(id); }, { immediate: true });
  const q = route.query.session;
  if (typeof q === 'string' && q && !sess.activeId.value) sess.activeId.value = q;
}
const shareLink = computed(() => `${location.origin}/use/qa/workbench?session=${sess.activeId.value}`);
async function copyLink() {
  try { await navigator.clipboard.writeText(shareLink.value); message.info('链接已复制'); } catch { message.info('浏览器不允许自动复制，请手动选中复制'); }
}
function toPage() {
  if (readonly.value) return;
  const title = sess.live.value ? sess.detail.value?.title ?? '' : sess.sessions.value.find((s) => s.id === sess.activeId.value)?.title ?? '';
  router.push({ path: '/use/qa/pages', query: { newName: title } });
}
function onPinned(page: string) { pin.value = ''; message.success(`已固定到「${page}」`); }
/** 固定到页面：真接口时记下是哪一轮（P7 用）；演示数据固定第一个会话的主回答卡 */
const PIN_DEMO_TITLE = '上个月华东大区复购客户的销售额（按省份）';
const pinTurn = ref<{ id: string; title: string }>();
function openPin(t?: { id: string; title: string }) { pinTurn.value = t; pin.value = '1'; }
/* 真接口（W16）：申请的是点了「申请权限」的那一轮；刷新后（?apply=1 复现）按本会话最后一个未申请的越权轮次 [推断] */
const applyTurn = ref('');
const applying = ref(false);
function openApply(turnId?: string) { applyTurn.value = turnId ?? ''; apply.value = '1'; }
async function onApplied(v: { reason: string; duration: string }) {
  if (!sess.real) { apply.value = ''; applied.value = true; message.success('申请已提交，等待审批'); return; }
  const d = sess.detail.value;
  const tid = applyTurn.value || [...(d?.turns ?? [])].reverse().find((t) => t.kind === 'denied' && !t.applied && (!t.code || t.code === 'NO_PERMISSION'))?.id;
  applying.value = true;
  try {
    await qaApi.createPermissionRequest({ scope: QA_APPLY.scope, object: QA_APPLY.object, reason: v.reason, duration: Number.parseInt(v.duration, 10), sourceTurnId: tid ?? null });
    apply.value = ''; applyTurn.value = '';
    message.success('申请已提交，等待审批');
    if (d) await sess.loadDetail(d.id);
  } catch (e) { message.error(`提交申请失败：${errText(e)}`); } finally { applying.value = false; }
}

function setConfirmQuery(on: boolean, runId?: string) {
  const q = { ...route.query };
  if (on) { q.confirm = '1'; if (runId) q.run = runId; } else { delete q.confirm; delete q.run; }
  router.replace({ query: q });
}

/** 演示数据里的确认卡字段；真接口（dynamic 开关）时动作类型 / 审批人 / 写回 / 幂等键换成 stage 返回的 action_run */
const ACTION_PARAMS = '面额 ¥30，满 ¥199 可用，有效期 14 天';
const ACTION_TARGET = { kind: '客户群', id: 'SEG-EC-60D', label: '华东大区 · 近 60 天未复购' };
function actionFields(run?: qaApi.QaActionRun) {
  return [
    { label: '动作类型', value: run ? `${run.action} ${run.typeVersion ?? ''}`.trim() : '发放复购召回优惠券 v3' },
    { label: '参数', value: run?.params ?? ACTION_PARAMS },
    { label: '影响', value: '3,812 位客户（已排除 30 天内已领券 214 人）' },
    { label: '预算', value: '营销预算 -¥114,360' },
    { label: '提交条件', value: '已满足：操作人有营销权限、预算余额充足' },
    { label: '审批人', value: run ? run.approver ?? '无（确认后直接执行）' : '赵磊（营销总监）' },
    { label: '写回', value: run ? `${run.system ?? '外部系统'}，外部失败则整体不改` : 'CRM 营销系统，外部失败则整体不改' },
    { label: '幂等键', value: run ? run.idemKey : 'act-20261025-ec-0193' },
  ];
}
const staging = ref(false);
async function runAction(reuseId?: string) {
  if (readonly.value || staging.value) return;
  let run: qaApi.QaActionRun | undefined;
  if (qaApi.actionsReal() && reuseId) {
    // 刷新带 ?confirm=1&run= 的页面：复用已暂存的动作；它已确认 / 撤回就不再弹卡，也不新建（不留孤儿记录）
    try {
      const r = await qaApi.getAction(reuseId);
      if (r.can?.confirm ?? r.status === '待确认') run = r;
    } catch { /* 取不到就当已失效 */ }
    if (!run) { setConfirmQuery(false); return; }
  } else if (qaApi.actionsReal()) {
    // X1：打开确认卡前先暂存，确认卡上显示服务端生成的幂等键
    staging.value = true;
    try {
      const d = sess.detail.value;
      run = await qaApi.stageAction({ action: ACTION, source: { type: '问数', session: d?.id, sessionTitle: d?.title }, objKind: ACTION_TARGET.kind, objId: ACTION_TARGET.id, objLabel: ACTION_TARGET.label, params: ACTION_PARAMS });
    } catch (e) { message.error(`发起失败：${errText(e)}`); return; } finally { staging.value = false; }
  }
  setConfirmQuery(true, run?.id);
  const ok = await confirm.ask({
    title: ACTION,
    tag: '待确认',
    target: `${ACTION_TARGET.kind} · ${ACTION_TARGET.label}`,
    fields: actionFields(run),
    impact: '提交后进入审批；外部系统超时会标记为“结果未知”并进入对账，不会自动重试。',
    irreversible: true,
    okText: run && !run.approver ? '确认执行' : '确认提交',
  });
  setConfirmQuery(false);
  if (!run) { if (ok) { submitted.value = true; message.success('已提交，等待 赵磊 审批'); } return; }
  try {
    if (ok) {
      // X2：有审批人 → 待审批；没有 → 执行中
      const r = await qaApi.confirmAction(run.id);
      submitted.value = true; submittedRun.value = { id: r.id, approver: r.approver };
      message.success(r.approver ? `已提交，等待 ${r.approver} 审批` : `已提交（${r.status}）`);
    } else {
      // X2b：确认卡取消 → 已撤回 [推断]
      await qaApi.withdrawAction(run.id);
    }
  } catch (e) { message.error(`${ok ? '提交' : '撤回'}失败：${errText(e)}`); }
}

onMounted(() => {
  if (route.query.confirm !== '1') return;
  const r = route.query.run;
  runAction(typeof r === 'string' && r ? r : undefined);
});
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip v-if="pv.active.value" :title="pv.reason.value"><a-button disabled><template #icon><ShareAltOutlined /></template>分享</a-button></a-tooltip>
    <a-popover v-else trigger="click" placement="bottomRight" title="分享会话">
      <template #content>
        <div class="qa-share">
          <a-input :value="shareLink" readonly size="small" />
          <a-button size="small" @click="copyLink">复制链接</a-button>
        </div>
        <div class="t-12 c-3 qa-share-tip">拿到链接的人按自己的权限看到结果</div>
      </template>
      <a-button><template #icon><ShareAltOutlined /></template>分享</a-button>
    </a-popover>
    <a-tooltip :title="reason || '新建一个页面，再把回答卡片固定上去'"><a-button :disabled="readonly" @click="toPage"><template #icon><LayoutOutlined /></template>把会话整理成页面</a-button></a-tooltip>
    <a-tooltip :title="pv.active.value ? pv.reason.value : '更多操作暂未开放'"><a-button disabled><template #icon><EllipsisOutlined /></template></a-button></a-tooltip>
  </Teleport>
  <div class="qa-wb">
    <QaSessionPanel @panel="(v) => (panel = v)" />
    <section class="panel qa-center">
      <div v-if="pv.active.value" class="qa-pv t-12 c-2">{{ pv.userName.value }} 的数据范围：{{ pvText }}。回答只含范围内的数据，预览中不能追问和发起动作。</div>
      <QaConversation :applied="applied" :tips="tips" @suggest="runAction()" @pin="openPin" @apply="openApply" />
      <QaComposer v-model:scope="scope" v-model:caliber="caliber" />
    </section>
    <QaObjectPanel :pending="pending" :submitted="submitted" :run="submittedRun" :highlight="highlight" @run="runAction()" @detail="drawer.open('region', REGION_EC.id)" />
  </div>
  <QaPinModal :open="pin === '1'" :card="pinTurn?.title ?? PIN_DEMO_TITLE" :turn-id="pinTurn?.id" @close="pin = ''" @done="onPinned" />
  <QaListDrawer :panel="panel" @close="panel = ''" @ask="panel = ''" />
  <QaApplyDrawer :open="apply === '1'" :submitting="applying" @close="apply = ''" @submit="onApplied" />
</template>

<style scoped>
.qa-wb { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.qa-center { flex: 1; background: var(--g3); }
.qa-share { display: flex; gap: 8px; width: 360px; }
.qa-share-tip { margin-top: 8px; }
.qa-pv { flex: none; padding: 8px 16px; border-bottom: var(--border); background: var(--g2); }
</style>
