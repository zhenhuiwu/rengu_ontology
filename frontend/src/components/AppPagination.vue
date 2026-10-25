<script setup lang="ts">
/**
 * 全站统一分页（规则见 README“分页规范”）。三种变体：
 *   full   “共 1,286 条” + 页码 + 每页条数（一屏（N）, 10, 20, 50）+ 跳转（超过 10 页才显示）；主表格、卡片网格，右下对齐
 *          一屏条数的选项显示为“一屏（21）”，其余显示“20 条/页”
 *   simple “共 N 条    ‹ 1/3 ›”；主从布局左侧列表、抽屉、侧栏、问数回答表格
 *   cursor 只有 上一页 / 下一页，不显示总数；审计日志、事件流等海量数据
 * total 为 0 时隐藏分页；empty=true（默认）时显示空状态。表格自带空状态，表格下方用时传 :empty="false"。
 * 插槽 #extra：放在分页旁边的操作（如“导出”）。
 */
import { computed, watch } from 'vue';
import { Empty } from 'ant-design-vue';
import { LeftOutlined, RightOutlined } from '@ant-design/icons-vue';
import { fmtInt } from '@/mock/format';

const props = withDefaults(defineProps<{
  variant?: 'full' | 'simple' | 'cursor';
  total?: number;
  defaultSize?: number;   // full：一屏条数，作为每页条数第一个选项
  unit?: string;          // “共 N 条”里的量词，如 “行”“个指标”
  empty?: boolean;
  emptyText?: string;
  hasPrev?: boolean;      // cursor
  hasNext?: boolean;      // cursor
}>(), { variant: 'full', total: 0, unit: '条', empty: true, emptyText: '暂无数据' });

const page = defineModel<number>('page', { default: 1 });
const size = defineModel<number>('size', { default: 10 });
const emit = defineEmits<{ prev: []; next: [] }>();

const pages = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, size.value))));
const sizeOptions = computed(() =>
  [...new Set([props.defaultSize ?? size.value, 10, 20, 50])].map(String));
// 一屏条数选项显示“一屏（21）”，选中后输入框里也是这个文字
const optionText = ({ value }: { value: string | number }) =>
  props.defaultSize != null && Number(value) === props.defaultSize ? `一屏（${value}）` : `${value} 条/页`;
const totalText = computed(() => `共 ${fmtInt(props.total)} ${props.unit}`);
const isEmpty = computed(() => props.variant !== 'cursor' && props.total === 0);
// 筛选后总数变少时，页码自动收回到最后一页
watch(pages, (n) => { if (props.variant !== 'cursor' && page.value > n) page.value = n; });

function onFull(p: number, s: number) {
  if (s !== size.value) size.value = s; else page.value = p;
}
</script>

<template>
  <a-empty v-if="isEmpty && empty" class="pg-empty" :image="Empty.PRESENTED_IMAGE_SIMPLE" :description="emptyText" />
  <div v-else-if="!isEmpty" class="pg" :class="`pg-${variant}`">
    <template v-if="variant === 'full'">
      <slot name="extra" />
      <span class="pg-sp" />
      <a-pagination
        size="small" :current="page" :page-size="size" :total="total"
        show-size-changer :show-quick-jumper="pages > 10" :page-size-options="sizeOptions"
        :build-option-text="optionText"
        :show-total="() => totalText" @change="onFull"
      />
    </template>
    <template v-else-if="variant === 'simple'">
      <span class="t-12 c-3 num">{{ totalText }}</span>
      <span class="pg-sp" />
      <slot name="extra" />
      <span v-if="pages > 1" class="pg-simple">
        <a-button size="small" type="text" :disabled="page <= 1" title="上一页" @click="page = page - 1"><LeftOutlined /></a-button>
        <span class="t-12 c-2 num">{{ page }}/{{ pages }}</span>
        <a-button size="small" type="text" :disabled="page >= pages" title="下一页" @click="page = page + 1"><RightOutlined /></a-button>
      </span>
    </template>
    <template v-else>
      <slot name="extra" />
      <span class="pg-sp" />
      <a-space :size="8">
        <a-button size="small" :disabled="!hasPrev" @click="emit('prev')">上一页</a-button>
        <a-button size="small" :disabled="!hasNext" @click="emit('next')">下一页</a-button>
      </a-space>
    </template>
  </div>
</template>

<style scoped>
.pg { flex: none; display: flex; align-items: center; gap: 8px; min-height: 40px; }
.pg-sp { flex: 1; }
.pg-simple { display: inline-flex; align-items: center; gap: 4px; }
.pg-empty { margin: 32px 0; }
</style>
