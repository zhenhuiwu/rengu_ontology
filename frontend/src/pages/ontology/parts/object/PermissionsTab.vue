<script setup lang="ts">
/**
 * 对象类型 → 使用与治理 → 权限：真模式角色/成员/对象清单与管理后台共用数据库 API。
 * 行级：每个角色能看哪些行；业务查看、门店店长、大区经理（华东）是“默认不可见”，没配范围就一行都看不到。
 *       行级范围在“角色与权限”里配，这里只给入口。
 * 列级：敏感属性默认脱敏，个别角色按对象明文。“敏感”是对象类型定义的一部分，改它要进变更草稿，
 *       草稿视图里逐行“设为敏感 / 取消敏感”，经确认卡后留灰色“修改”标记。
 */
import InfoTip from '@/components/InfoTip.vue';
import { computed, ref, watch, onScopeDispose } from 'vue';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import { paginate } from '@/mock/paginate';
import { ROLES, PERM_OBJECTS, roleMembers, scopeText, type Role } from '@/mock/roles';
import { OBJECT_PROPS, type ObjProp } from '@/mock/objectProps';
import { useConfirmStore } from '@/stores/confirm';
import { useDraftView } from '../useDraftView';
import { objLabel } from './objectGov';
import { queryObjectPermissions, rolesApiOn, type ObjectPermissionRole } from '@/api/roles';
import { currentApiContext } from '@/api/http';
import { OBJECT_TYPE_LIST } from '@/api/static';

const props = defineProps<{ objectId: string; readonly: boolean }>();
const { draft, editTip } = useDraftView('object', () => props.objectId);
const real = rolesApiOn();
let mounted = true;
onScopeDispose(() => { mounted = false; });
const mode = ref<'row' | 'col'>('row');
const rowPage = ref(1), rowSize = ref(real ? 10 : 12), rowTotal = ref(0);
type PermissionRole = Pick<Role, 'id' | 'name' | 'defaultDeny' | 'objs' | 'scopes'> & { memberCount?: number };
const objectHead = ref<{ id: string; name: string; api: string }>();
const object = computed(() => real ? objectHead.value : OBJECT_TYPE_LIST.find((o) => o.id === props.objectId));
const name = computed(() => real ? objectHead.value?.name ?? props.objectId : objLabel(props.objectId));
const roles = ref<PermissionRole[]>(real ? [] : ROLES);
const permObj = computed(() => real ? objectHead.value : PERM_OBJECTS.find(o => object.value?.api ? o.api === object.value.api : o.name === name.value));
const loading = ref(false), loadError = ref('');
let loadSeq = 0, loadScope = '';
async function loadPermissions() {
  const my = ++loadSeq;
  if (!mounted || !real) return;
  const context = JSON.stringify([props.objectId, currentApiContext()]);
  const scope = JSON.stringify([context, mode.value]);
  if (scope !== loadScope) { loadScope = scope; rowTotal.value = 0; objectHead.value = undefined; }
  roles.value = []; loadError.value = ''; loading.value = true;
  if (!props.objectId) { loading.value = false; return; }
  const current = () => mounted && my === loadSeq && context === JSON.stringify([props.objectId, currentApiContext()]);
  try {
    if (mode.value === 'row') {
      const result = await queryObjectPermissions(props.objectId, rowPage.value, rowSize.value);
      if (!current()) return;
      roles.value = result.items; objectHead.value = result.object; rowTotal.value = result.total;
      if (result.page !== rowPage.value) rowPage.value = result.page;
    } else {
      // 列级矩阵必须保留全部角色列，仅取当前对象的轻投影，不取成员明细/用户。
      const rows: ObjectPermissionRole[] = [];
      for (let page = 1; ; page++) {
        const result = await queryObjectPermissions(props.objectId, page, 200);
        if (!current()) return;
        rows.push(...result.items); objectHead.value = result.object;
        if (!result.items.length || rows.length >= result.total) break;
      }
      roles.value = rows;
    }
  } catch (e) { if (current()) loadError.value = `权限加载失败：${e instanceof Error ? e.message : String(e)}`; }
  finally { if (current()) loading.value = false; }
}
watch(() => JSON.stringify([props.objectId, currentApiContext(), mode.value, mode.value === 'row' ? [rowPage.value, rowSize.value] : []]), loadPermissions, { immediate: true, flush: 'sync' });

// —— 行级 ——
interface RowAccess { role: PermissionRole; view: boolean; scope: string; tone: 'normal' | 'warn' | 'off'; exp: boolean; actions: string[]; members: number }
const access = (r: PermissionRole): RowAccess => {
  const o = r.objs.find((x) => object.value?.api ? x.api === object.value.api : x.objType === name.value);
  const view = !!o?.view;
  const sc = real ? r.scopes : r.scopes.filter((s) => s.objType === (o?.objType ?? name.value));
  const base = { role: r, view, exp: !!o?.export, actions: o?.actions ?? [], members: real ? r.memberCount ?? 0 : roleMembers(r as Role).length };
  if (!view) return { ...base, scope: o ? '不可查看' : '未纳入对象权限 · 默认不可见', tone: 'off' };
  if (sc.length) return { ...base, scope: sc.map(scopeText).join('；'), tone: 'normal' };
  if (r.defaultDeny) return { ...base, view: false, scope: '未配置范围 · 默认不可见', tone: 'warn' };
  return { ...base, scope: '全部行', tone: 'normal' };
};
const rowAll = computed(() => roles.value.map(access));
const rowItems = computed(() => real ? rowAll.value : paginate(rowAll.value, { page: rowPage.value, size: rowSize.value }).items);
const rowCols = [
  { title: '角色', key: 'role', width: 260 },
  { title: '可查看', key: 'view', width: 90 },
  { title: '行级范围', key: 'scope' },
  { title: '导出', key: 'exp', width: 80 },
  { title: '可执行动作', key: 'actions', width: 300 },
  { title: '成员', key: 'members', width: 72, align: 'right' as const },
  { title: '', key: 'op', width: 90 },
];

// —— 列级 ——
/** 仅显式 mock 模式的明文演示授权；真模式没有对应权限契约。 */
const PLAIN: Record<string, string[]> = { 'R-07': ['member'], 'R-04': ['order', 'refund'] };
const sensEdits = ref<Record<string, boolean>>({});
watch(() => props.objectId, () => { sensEdits.value = {}; });
const isSens = (p: ObjProp) => sensEdits.value[p.api] ?? !!p.sensitive;
const PROPS = computed(() => OBJECT_PROPS[props.objectId] ?? []);
// 已发布版的敏感属性排在前面；草稿里改了标记不挪位置，免得行跳走
const colAll = computed(() => [...PROPS.value].sort((a, b) => Number(!!b.sensitive) - Number(!!a.sensitive)));
const colPage = ref(1);
const colSize = ref(12);
const colItems = computed(() => paginate(colAll.value, { page: colPage.value, size: colSize.value }).items);
const cell = (p: ObjProp, r: RowAccess) => {
  if (!r.view) return { text: '不可见', cls: 'c-3' };
  if (!isSens(p)) return { text: '明文', cls: 'c-2' };
  if (real) return { text: '—', cls: 'c-3' };
  const plain = (PLAIN[r.role.id] ?? []).includes(props.objectId);
  return plain ? { text: '明文', cls: 'c-warning' } : { text: '脱敏', cls: 'c-2' };
};
const shortRole = (n: string) => n.replace('（华东）', '');
const colCols = computed(() => [
  { title: '属性', key: 'prop', width: 200, fixed: 'left' as const },
  { title: '敏感', key: 'sens', width: 110 },
  ...rowAll.value.map((r) => ({ title: shortRole(r.role.name), key: r.role.id, width: 100 })),
  { title: '', key: 'op', width: 100 },
]);
const sensCount = computed(() => PROPS.value.filter(isSens).length);

async function toggleSens(p: ObjProp) {
  if (real || props.readonly || !draft.value) return;
  const to = !isSens(p);
  const ok = await useConfirmStore().ask({
    title: to ? '设为敏感属性' : '取消敏感属性',
    target: `${name.value}.${p.name}（${p.api}）`,
    tag: `草稿 ${draft.value.id}`,
    fields: [
      { label: '改动', value: to ? '不敏感 → 敏感' : '敏感 → 不敏感' },
      { label: '写入', value: `变更单 ${draft.value.id}（发布后生效）` },
    ],
    impact: to
      ? '发布后除按对象明文授权的角色外，问数结果、对象详情、导出里这一列都脱敏显示。'
      : '发布后所有能查看该对象的角色都能看到这一列明文，请确认不涉及个人信息。',
    okText: '写入草稿',
    danger: !to,
  });
  if (!ok) return;
  if (to === !!p.sensitive) { const n = { ...sensEdits.value }; delete n[p.api]; sensEdits.value = n; }
  else sensEdits.value = { ...sensEdits.value, [p.api]: to };
  message.success(`已写入草稿 ${draft.value.id}`);
}
</script>

<template>
  <div class="ot-body">
    <a-alert v-if="loadError" type="error" show-icon :message="loadError"><template #action><a-button size="small" @click="loadPermissions">重试</a-button></template></a-alert>
    <div class="pm-tool">
      <a-segmented v-model:value="mode" :options="[{ label: '行级权限', value: 'row' }, { label: '列级权限', value: 'col' }]" />
      <template v-if="mode === 'row'">
        <InfoTip text="行级范围按角色在“角色与权限”里配置，保存即生效，不走本体变更单" />
        <span v-if="!loading && !loadError && !permObj" class="t-12 c-warning">{{ name }}还没纳入角色的对象权限</span>
      </template>
      <template v-else>
        <InfoTip :text="real ? `敏感 ${sensCount} 个` : `敏感 ${sensCount} 个 · 敏感属性默认脱敏，橙色“明文”为按对象单独授权${readonly ? '。已发布版本只读，改敏感标记请先发起变更' : ''}`" />
        <span v-if="!readonly && Object.keys(sensEdits).length" class="t-12 c-3">本草稿改了 {{ Object.keys(sensEdits).length }} 处敏感标记</span>
      </template>
      <span class="pm-sp" />
      <router-link v-if="mode === 'row'" to="/admin/system/roles">角色与权限</router-link>
    </div>

    <template v-if="mode === 'row'">
      <a-table :loading="loading" :columns="rowCols" :data-source="rowItems" :pagination="false" size="middle" :row-key="(r: RowAccess) => r.role.id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'role'">
            <span class="t-14 c-1">{{ record.role.name }}</span>
            <a-tooltip v-if="record.role.defaultDeny" title="默认不可见：没配置行级范围的对象类型一行都看不到">
              <a-tag class="pm-tag">默认不可见</a-tag>
            </a-tooltip>
          </template>
          <template v-else-if="column.key === 'view'"><span :class="record.view ? 'c-2' : 'c-3'">{{ record.view ? '可查看' : '不可见' }}</span></template>
          <template v-else-if="column.key === 'scope'">
            <span :class="record.tone === 'warn' ? 'c-warning' : record.tone === 'off' ? 'c-3' : 'c-1'">{{ record.scope }}</span>
          </template>
          <template v-else-if="column.key === 'exp'"><span :class="record.exp ? 'c-2' : 'c-3'">{{ record.exp ? '可导出' : '--' }}</span></template>
          <template v-else-if="column.key === 'actions'">
            <span v-if="record.actions.length" class="c-2 pm-ell">{{ record.actions.join('、') }}</span>
            <span v-else class="c-3">--</span>
          </template>
          <template v-else-if="column.key === 'members'"><span class="num">{{ record.members }}</span></template>
          <template v-else-if="column.key === 'op'">
            <router-link :to="`/admin/system/roles?role=${record.role.id}&tab=row`">{{ record.tone === 'warn' ? '配置范围' : '去配置' }}</router-link>
          </template>
        </template>
      </a-table>
      <AppPagination v-model:page="rowPage" v-model:size="rowSize" variant="simple" :total="real ? rowTotal : rowAll.length" unit="个角色" :empty="false" />
    </template>

    <template v-else>
      <a-table :columns="colCols" :data-source="colItems" :pagination="false" size="small" row-key="api">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'prop'">
            <span class="t-14 c-1" :title="`${record.name} · ${record.api}`">{{ record.name }}</span>
            <a-tag v-if="sensEdits[record.api] != null" class="pm-tag">修改</a-tag>
          </template>
          <template v-else-if="column.key === 'sens'">
            <a-tag v-if="isSens(record)" class="pm-tag0">敏感</a-tag>
            <span v-else class="c-3">--</span>
          </template>
          <template v-else-if="column.key === 'op'">
            <a-tooltip :title="real ? '暂不可修改' : readonly ? editTip : ''">
              <a-button type="link" size="small" :disabled="real || readonly" class="pm-op" @click="toggleSens(record)">{{ isSens(record) ? '取消敏感' : '设为敏感' }}</a-button>
            </a-tooltip>
          </template>
          <template v-else>
            <span :class="cell(record, rowAll.find((r) => r.role.id === column.key)!).cls">{{ cell(record, rowAll.find((r) => r.role.id === column.key)!).text }}</span>
          </template>
        </template>
        <template #emptyText><span class="t-14 c-3">还没有属性</span></template>
      </a-table>
      <AppPagination v-model:page="colPage" v-model:size="colSize" variant="simple" :total="colAll.length" unit="个属性" :empty="false" />
    </template>
  </div>
</template>

<style scoped>
.ot-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 12px; }
.pm-tool { display: flex; align-items: center; gap: 12px; flex: none; }
.pm-sp { flex: 1; }
.pm-tag { margin-inline: 8px 0; }
.pm-tag0 { margin-inline-end: 0; }
.pm-ell { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pm-op { padding: 0; }
</style>
