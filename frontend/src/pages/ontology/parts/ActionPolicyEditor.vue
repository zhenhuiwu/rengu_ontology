<script setup lang="ts">
import { computed, ref } from 'vue';
import type { ActionParam } from '@/mock/actionEvent';
const props = defineProps<{ initial: unknown; params: ActionParam[] }>();
const text = ref(JSON.stringify(props.initial, null, 2));
const error = ref('');
const policy = computed<Record<string, any>>(() => { try { const value = JSON.parse(text.value); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; } });
function set(key: string, value: unknown) { const next = { ...policy.value }; if (value === undefined) delete next[key]; else next[key] = value; text.value = JSON.stringify(next, null, 2); }
function limit(key: string, value: unknown) { const next = { ...policy.value.limits }; if (value === undefined || value === null || value === '') delete next[key]; else next[key] = value; set('limits', next); }
function mode(value: string) { set('mode', value); if (value !== 'auto') set('limits', undefined); }
const amountParams = computed(() => props.params.filter(p => ['金额', '整数'].includes(p.type)).map(p => ({ value: p.api, label: `${p.name}（${p.api}）` })));
function amount(key: string, value: unknown) { if (key === 'param' && !value) { limit('amount', undefined); return; } const next = { ...policy.value.limits?.amount, [key]: value }; if (!next.param && (next.max === undefined || next.max === null)) limit('amount', undefined); else limit('amount', next); }
function validate() {
  error.value = '';
  const fail = (message: string) => { error.value = message; return false; };
  let p: any;
  try { p = JSON.parse(text.value); } catch { return fail('策略 JSON 格式不正确'); }
  if (!p || typeof p !== 'object' || Array.isArray(p)) return fail('策略须为对象');
  if (Object.keys(p).some(k => !['mode', 'callers', 'limits', 'overflow'].includes(k))) return fail('存在不支持的策略字段，请核对 JSON 后再保存');
  if (!['suggest', 'confirm', 'auto'].includes(p.mode)) return fail('请选择执行方式');
  if (p.overflow !== undefined && p.overflow !== 'stage') return fail('超限处理只支持转待确认');
  if (p.callers != null && (!Array.isArray(p.callers) || !p.callers.length || new Set(p.callers).size !== p.callers.length || p.callers.some((c: unknown) => !['人', '规则', 'AI', '服务账号'].includes(String(c))))) return fail('请选择有效且不重复的调用方');
  if (p.mode !== 'auto') return p.limits == null || fail('只有自动执行可配置自动限额');
  const l = p.limits;
  const count = (n: unknown) => typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 10000;
  if (!l || typeof l !== 'object' || Array.isArray(l) || !count(l.dailyCount)) return fail('自动执行须设置1至10000的每日总限额');
  if (Object.keys(l).some(k => !['dailyCount', 'perCallerDailyCount', 'amount', 'objects'].includes(k))) return fail('存在不支持的限额字段');
  if (l.perCallerDailyCount != null && !count(l.perCallerDailyCount)) return fail('每调用方每日限额须为1至10000');
  if (l.amount != null && (!l.amount || typeof l.amount !== 'object' || Array.isArray(l.amount) || Object.keys(l.amount).some(k => !['param', 'max'].includes(k)) || !amountParams.value.some(p => p.value === l.amount.param) || typeof l.amount.max !== 'number' || !Number.isFinite(l.amount.max) || l.amount.max < 0)) return fail('金额限额须选择已有数值参数，并填写非负上限');
  if (l.objects != null && (!Array.isArray(l.objects) || l.objects.length > 500 || l.objects.some((v: unknown) => typeof v !== 'string' || !v.trim() || v.length > 64))) return fail('目标对象编号最多500项，每项1至64字');
  return true;
}
defineExpose({ validate, value: () => JSON.parse(text.value) });
</script>

<template>
  <a-alert v-if="error" type="error" :message="error" show-icon />
  <a-form layout="vertical">
    <a-form-item label="执行方式" required><a-select :value="policy.mode" :options="[{value:'suggest',label:'仅建议'},{value:'confirm',label:'确认后执行'},{value:'auto',label:'满足条件后自动执行'}]" @change="mode" /></a-form-item>
    <a-form-item label="允许调用方" extra="留空表示不额外限制；原有权限仍须满足"><a-select mode="multiple" :value="policy.callers ?? []" :options="['人','规则','AI','服务账号'].map(value => ({value,label:value}))" @change="(value: string[]) => set('callers', value.length ? value : undefined)" /></a-form-item>
    <template v-if="policy.mode === 'auto'">
      <a-form-item label="每日总限额" required><a-input-number :value="policy.limits?.dailyCount" :min="1" :max="10000" :precision="0" @change="(v: number | null) => limit('dailyCount', v)" /></a-form-item>
      <a-form-item label="每调用方每日限额"><a-input-number :value="policy.limits?.perCallerDailyCount" :min="1" :max="10000" :precision="0" @change="(v: number | null) => limit('perCallerDailyCount', v)" /></a-form-item>
      <a-form-item label="金额参数"><a-select allow-clear :value="policy.limits?.amount?.param" :options="amountParams" placeholder="可选，使用已有数值参数" @change="(v: string | undefined) => amount('param', v)" /></a-form-item>
      <a-form-item v-if="policy.limits?.amount?.param" label="金额上限"><a-input-number :value="policy.limits?.amount?.max" :min="0" @change="(v: number | null) => amount('max', v)" /></a-form-item>
      <a-form-item label="目标对象编号" extra="可选，仅限这些对象；最多500项"><a-select mode="tags" :value="policy.limits?.objects ?? []" @change="(v: string[]) => limit('objects', v.length ? v : undefined)" /></a-form-item>
      <a-alert type="info" message="自动执行仍须满足免审、对象范围、调用方及限额条件；不满足时进入待确认。" show-icon />
    </template>
    <a-form-item label="超限处理"><a-select :value="policy.overflow ?? 'stage'" :options="[{value:'stage',label:'转为待确认'}]" @change="(v: string) => set('overflow', v)" /></a-form-item>
    <a-collapse ghost><a-collapse-panel key="json" header="策略 JSON"><a-textarea v-model:value="text" :auto-size="{minRows:4,maxRows:12}" /></a-collapse-panel></a-collapse>
  </a-form>
</template>
