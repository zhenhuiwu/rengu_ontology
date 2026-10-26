<script setup lang="ts">
/**
 * 角色与权限（PDE工程师）。
 * 左侧角色列表，右侧详情页签：功能权限、对象与动作权限、行级范围、成员（full 分页，?mpage=&msize=）。
 * 功能 / 对象权限就地编辑，有修改时底部出现保存条（useUnsavedGuard：切换角色、离开页面要确认）；内置角色和历史版本只读。
 * 行级范围只能基于对象类型里标为“权限依据”的属性（如 订单.大区 = 华东）。
 * 指标按基础对象的范围过滤，再叠加维度对象的范围；业务查看类角色（defaultDeny）没配范围的对象默认不可见。
 * “以此用户预览”：抽屉里汇总该用户所有角色合并后的有效权限，可进入只读预览。
 * URL：?role=R-04&tab=func|obj|row|members&form=edit|scope|members&preview=U010231
 * 数据：开关 admin 切到真接口且后台可用时走 /api/admin/v1/roles（src/api/roles.ts），每次写成功后整份重新拉取；
 * 否则用本地 mock（原逻辑不变）。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import { useConfirmStore } from '@/stores/confirm';
import { useReadonly } from '@/composables/useReadonly';
import { usePreviewStore, firstAllowedPage } from '@/stores/preview';
import { useUnsavedGuard, confirmDiscard } from '@/composables/useUnsavedGuard';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { isApiError } from '@/api/http';
import { addRoleMembers, addRoleScope, createRole, deleteRole as apiDeleteRole, loadRolesState, removeRoleMember, removeRoleScope, saveRolePermissions, updateRole, type RemoteRole } from '@/api/roles';
import { ADMIN_USERS, paginate, EMPTY, type AdminUser } from '@/mock';
import { ROLES, FUNC_PAGES, PERM_OBJECTS, SCOPE_PROPS, roleMembers, effectiveAccess, scopeText, type Role, type FuncLevel, type ObjPerm, type RowScope } from '@/mock/roles';

const route = useRoute();
const router = useRouter();
const q = (k: string) => (typeof route.query[k] === 'string' ? (route.query[k] as string) : '');
const setQuery = (patch: Record<string, string | undefined>) => router.replace({ query: { ...route.query, ...patch } });
const { readonly, reason } = useReadonly();
const confirm = useConfirmStore();
const previewStore = usePreviewStore();
const nowStr = '2026-10-25 17:10';

// 本页内修改叠加在示例数据上
const roles = ref<Role[]>(JSON.parse(JSON.stringify(ROLES)));
const members = ref<Record<string, AdminUser[]>>(Object.fromEntries(ROLES.map((r) => [r.id, roleMembers(r)])));
const allUsers = ref<AdminUser[]>(ADMIN_USERS);
/** true = 数据来自后台（写操作走接口） */
const remote = ref(false);
async function reload() {
  const st = await loadRolesState();
  if (!st) { remote.value = false; return; }
  roles.value = st.roles;
  members.value = st.members;
  allUsers.value = st.users;
  remote.value = true;
}
/** 后台写操作：成功后重新拉取；失败提示后台的报错（不改本地数据） */
async function call<T>(fn: () => Promise<T>): Promise<T | undefined> {
  try {
    const r = await fn();
    await reload();
    return r;
  } catch (e) {
    message.error(isApiError(e) ? e.message : '操作失败，请稍后重试');
    return undefined;
  }
}
const lockOf = (r: Role) => (r as Partial<RemoteRole>).lockVersion;
onMounted(async () => { await reload(); if (remote.value) resetDraft(); });
const role = computed(() => roles.value.find((r) => r.id === q('role')) ?? roles.value[0]);
const locked = computed(() => readonly.value || role.value.builtIn);
const lockReason = computed(() => reason.value || (role.value.builtIn ? '内置角色不能修改，可复制后再改' : ''));
const tab = computed({ get: () => q('tab') || 'func', set: (v: string) => setQuery({ tab: v === 'func' ? undefined : v }) });

// 功能 / 对象权限草稿
const draft = reactive<{ func: Record<string, FuncLevel>; objs: ObjPerm[] }>({ func: {}, objs: [] });
const snap = ref('');
const resetDraft = () => {
  draft.func = { ...role.value.func };
  draft.objs = JSON.parse(JSON.stringify(role.value.objs));
  snap.value = JSON.stringify(draft);
};
watch(() => role.value.id, resetDraft, { immediate: true });
const dirty = computed(() => JSON.stringify(draft) !== snap.value);
const guard = useUnsavedGuard(dirty);
const changedCount = computed(() => {
  const f = FUNC_PAGES.filter((p) => draft.func[p.key] !== role.value.func[p.key]).length;
  const o = draft.objs.filter((x, i) => JSON.stringify(x) !== JSON.stringify(role.value.objs[i])).length;
  return { f, o };
});
async function saveDraft() {
  const n = members.value[role.value.id].length;
  const ok = await confirm.ask({
    title: '保存权限', target: `角色「${role.value.name}」`, okText: '保存',
    fields: [{ label: '功能权限', value: `${changedCount.value.f} 个页面有变化` }, { label: '对象与动作', value: `${changedCount.value.o} 个对象类型有变化` }, { label: '影响成员', value: `${n} 人` }],
    impact: `保存后立即对 ${n} 名成员生效；已登录的成员在下次操作时按新权限校验。修改会记入审计日志。`,
  });
  if (!ok) return;
  if (remote.value) {
    const id = role.value.id;
    if (!(await call(() => saveRolePermissions(id, { func: { ...draft.func }, objs: JSON.parse(JSON.stringify(draft.objs)), lockVersion: lockOf(role.value) })))) return;
    resetDraft();
    message.success(`已保存：${role.value.name}`);
    return;
  }
  Object.assign(role.value, { func: { ...draft.func }, objs: JSON.parse(JSON.stringify(draft.objs)), updatedAt: nowStr, updatedBy: '王敏' });
  snap.value = JSON.stringify(draft);
  message.success(`已保存：${role.value.name}`);
}
const select = (id: string) => guard(() => setQuery({ role: id, form: undefined, preview: undefined, mpage: undefined, msize: undefined }));

// 功能权限：按区分三列
const areas = computed(() => ['使用', '构建', '管理'].map((a) => ({ area: a, pages: FUNC_PAGES.filter((p) => p.area === a) })));
const funcStat = (lv: FuncLevel) => FUNC_PAGES.filter((p) => draft.func[p.key] === lv).length;
const LEVELS = [{ value: 'none', label: '无' }, { value: 'view', label: '查看' }, { value: 'manage', label: '管理' }];
const objActions = (name: string) => PERM_OBJECTS.find((o) => o.name === name)?.actions ?? [];
const ocols = [
  { title: '对象类型', key: 'obj', width: 200 },
  { title: '查看', key: 'view', width: 70, align: 'center' as const },
  { title: '导出', key: 'export', width: 70, align: 'center' as const },
  { title: '可执行的动作', key: 'actions' },
  { title: '行级范围', key: 'scope', width: 260 },
];
const scopesOf = (obj: string) => role.value.scopes.filter((s) => s.objType === obj);
/** 没有配置范围时的默认：业务查看类角色不可见，其余全部行 */
const noScopeText = computed(() => (role.value.defaultDeny ? '不可见（未配置范围，默认）' : '全部行'));
const unscopedObjs = computed(() => draft.objs.filter((o) => o.view && !scopesOf(o.objType).length).map((o) => o.objType));

// 行级范围
const scols = [
  { title: '对象类型', dataIndex: 'objType', key: 'objType', width: 140 },
  { title: '权限依据属性', dataIndex: 'prop', key: 'prop', width: 180 },
  { title: '条件', key: 'cond' },
  { title: '操作', key: 'op', width: 90 },
];
async function removeScope(s: RowScope) {
  const ok = await confirm.ask({
    title: '删除行级范围', target: `${s.objType}：${scopeText(s)}`, okText: '删除', danger: true,
    fields: [{ label: '角色', value: role.value.name }, { label: '影响成员', value: `${members.value[role.value.id].length} 人` }],
    impact: `删除后，该角色看${s.objType}${scopesOf(s.objType).length > 1 ? '时仍受其余范围限制' : role.value.defaultDeny ? '将不可见（业务查看类角色未配置范围默认不可见）' : '将不再按范围限制，可见全部行'}。`,
  });
  if (!ok) return;
  if (remote.value) {
    const id = role.value.id;
    if ((await call(() => removeRoleScope(id, s.id))) === undefined) return;
    message.success('已删除行级范围');
    return;
  }
  role.value.scopes = role.value.scopes.filter((x) => x.id !== s.id);
  message.success('已删除行级范围');
}
const scopeForm = reactive<{ key?: string; values: string[] }>({ key: undefined, values: [] });
const scopeOpts = SCOPE_PROPS.map((p) => ({ value: `${p.objType}.${p.prop}`, label: `${p.objType} · ${p.prop}` }));
const scopeProp = computed(() => SCOPE_PROPS.find((p) => `${p.objType}.${p.prop}` === scopeForm.key));
const scopeDirty = computed(() => q('form') === 'scope' && (!!scopeForm.key || scopeForm.values.length > 0));
watch(() => scopeForm.key, () => { scopeForm.values = []; });
const openScope = () => { scopeForm.key = undefined; scopeForm.values = []; setQuery({ form: 'scope' }); };
const closeScope = async () => { if (scopeDirty.value && !(await confirmDiscard())) return; setQuery({ form: undefined }); };
async function addScope() {
  const p = scopeProp.value;
  if (!p || !scopeForm.values.length) { message.warning('请选择属性和取值'); return; }
  if (remote.value) {
    const id = role.value.id;
    const body = { objType: p.objType, prop: p.prop, op: (scopeForm.values.length > 1 ? '∈' : '=') as RowScope['op'], values: [...scopeForm.values] };
    if (!(await call(() => addRoleScope(id, body)))) return;
  } else role.value.scopes.push({ id: `S-${Date.now() % 100000}`, objType: p.objType, prop: p.prop, op: scopeForm.values.length > 1 ? '∈' : '=', values: [...scopeForm.values] });
  scopeForm.key = undefined; scopeForm.values = [];
  setQuery({ form: undefined });
  message.success('已添加行级范围');
}

// 成员
const mkw = ref('');
const mpq = usePagedQuery({ defaultSize: 15, filters: [mkw, () => role.value.id], prefix: 'm' });
const mrows = computed(() => members.value[role.value.id].filter((u) => !mkw.value || u.name.includes(mkw.value) || u.account.includes(mkw.value) || u.id.includes(mkw.value)));
const mpaged = computed(() => paginate(mrows.value, { page: mpq.page.value, size: mpq.size.value }));
const mcols = [
  { title: '用户 ID', key: 'id', width: 100 },
  { title: '姓名', dataIndex: 'name', key: 'name', width: 90 },
  { title: '账号', dataIndex: 'account', key: 'account', width: 240, ellipsis: true },
  { title: '部门', dataIndex: 'dept', key: 'dept', ellipsis: true },
  { title: '状态', key: 'status', width: 80 },
  { title: '最近登录', key: 'lastLogin', width: 150 },
  { title: '操作', key: 'op', width: 170 },
];
const badge = (_s: AdminUser['status']) => 'default'; // 状态灰点 + 文字；橙色只用于 待确认 / 结果未知
async function removeMember(u: AdminUser) {
  const others = rolesOf(u).filter((r) => r.id !== role.value.id);
  const ok = await confirm.ask({
    title: '移出角色', target: `${u.name}（${u.account}）`, okText: '移出', danger: true,
    fields: [{ label: '角色', value: role.value.name }, { label: '保留的其他角色', value: others.length ? others.map((r) => r.name).join('、') : '无' }],
    impact: others.length ? `移出后按其余角色（${others.map((r) => r.name).join('、')}）合并计算权限。` : '移出后该用户没有任何角色，只能登录，看不到任何页面。',
  });
  if (!ok) return;
  if (remote.value) {
    const id = role.value.id;
    if ((await call(() => removeRoleMember(id, u.id))) === undefined) return;
  } else members.value[role.value.id] = members.value[role.value.id].filter((x) => x.id !== u.id);
  message.success(`已将 ${u.name} 移出「${role.value.name}」`);
}
const addIds = ref<string[]>([]);
const addOpts = computed(() => allUsers.value.filter((u) => u.status === '启用' && !members.value[role.value.id].some((m) => m.id === u.id)).map((u) => ({ value: u.id, label: `${u.name} · ${u.dept} · ${u.id}` })));
const openAdd = () => { addIds.value = []; setQuery({ form: 'members' }); };
const closeAdd = async () => { if (q('form') === 'members' && addIds.value.length && !(await confirmDiscard())) return; setQuery({ form: undefined }); };
async function addMembers() {
  const add = allUsers.value.filter((u) => addIds.value.includes(u.id));
  if (remote.value) {
    const id = role.value.id;
    if ((await call(() => addRoleMembers(id, add.map((u) => u.id)))) === undefined) return;
  } else members.value[role.value.id] = [...add, ...members.value[role.value.id]];
  addIds.value = [];
  setQuery({ form: undefined });
  message.success(`已添加 ${add.length} 名成员`);
}

// 以此用户预览：所有角色合并后的有效权限（用本页正在编辑的角色和成员计算，与 roles.ts effectiveAccess 同一套规则）
const rolesOf = (u: AdminUser) => roles.value.filter((r) => members.value[r.id].some((m) => m.id === u.id));
const pvUser = computed(() => allUsers.value.find((u) => u.id === q('preview')));
const pv = computed(() => {
  const u = pvUser.value;
  if (!u) return null;
  return { u, ...effectiveAccess(u, roles.value, (r) => members.value[r.id] ?? []) };
});
const openPreview = (u: AdminUser) => setQuery({ preview: u.id });
async function enterPreview() {
  const p = pv.value;
  if (!p) return;
  const ok = await confirm.ask({
    title: '以此用户预览', target: `${p.u.name}（${p.u.account}）`, okText: '进入预览',
    fields: [{ label: '角色', value: p.rs.map((r) => r.name).join('、') || '无' }, { label: '可进入', value: p.areasIn.join(' / ') || '无' }],
    impact: '预览期间界面按该用户的权限显示，所有操作只读、不会执行动作；预览的开始和结束都会记入审计日志。',
  });
  if (!ok) return;
  // 记住回来的位置（角色页，关掉预览抽屉），进入预览后到该用户第一个有权限的页面
  const back = router.resolve({ path: route.path, query: { ...route.query, preview: undefined } }).fullPath;
  previewStore.start(p.u, back, p);
  const first = firstAllowedPage('use') ?? firstAllowedPage();
  router.push(first ? { name: first } : '/');
  message.success(`已进入预览：${p.u.name}（只读）`);
}

// 角色本身：编辑 / 复制 / 删除
const form = reactive({ name: '', desc: '' });
const formSnap = ref('');
const formOpen = computed(() => q('form') === 'edit');
watch([formOpen, () => role.value.id], () => { if (!formOpen.value) return; Object.assign(form, { name: role.value.name, desc: role.value.desc }); formSnap.value = JSON.stringify(form); }, { immediate: true });
const formDirty = computed(() => formOpen.value && JSON.stringify(form) !== formSnap.value);
const closeForm = async () => { if (formDirty.value && !(await confirmDiscard())) return; setQuery({ form: undefined }); };
async function saveForm() {
  if (!form.name.trim()) { message.warning('请填写角色名'); return; }
  if (roles.value.some((r) => r.id !== role.value.id && r.name === form.name.trim())) { message.warning('角色名已存在'); return; }
  if (remote.value) {
    const id = role.value.id;
    if (!(await call(() => updateRole(id, { name: form.name.trim(), desc: form.desc, lockVersion: lockOf(role.value) })))) return;
  } else Object.assign(role.value, { name: form.name.trim(), desc: form.desc, updatedAt: nowStr, updatedBy: '王敏' });
  formSnap.value = JSON.stringify(form);
  setQuery({ form: undefined });
  message.success('已保存');
}
function copyRole() {
  guard(async () => {
    if (remote.value) {
      const src = role.value;
      const r = await call(() => createRole({ name: `${src.name}（副本）`, desc: src.desc, copyFrom: src.id }));
      if (!r) return;
      setQuery({ role: r.id, form: 'edit', tab: undefined });
      message.success(`已复制为「${r.name}」，没有成员`);
      return;
    }
    const id = `R-${String(roles.value.length + 1).padStart(2, '0')}`;
    const r: Role = { ...JSON.parse(JSON.stringify(role.value)), id, name: `${role.value.name}（副本）`, builtIn: false, updatedAt: nowStr, updatedBy: '王敏' };
    roles.value.push(r);
    members.value[id] = [];
    setQuery({ role: id, form: 'edit', tab: undefined });
    message.success(`已复制为「${r.name}」，没有成员`);
  });
}
async function deleteRole() {
  const n = members.value[role.value.id].length;
  const ok = await confirm.ask({
    title: '删除角色', target: `角色「${role.value.name}」`, okText: '删除', danger: true, irreversible: true,
    fields: [{ label: '成员', value: `${n} 人` }, { label: '行级范围', value: `${role.value.scopes.length} 条` }],
    impact: n ? `${n} 名成员会失去这个角色的权限（其他角色不受影响）。删除后不能恢复，可从审计日志查到原配置。` : '该角色没有成员。删除后不能恢复。',
  });
  if (!ok) return;
  const name = role.value.name;
  if (remote.value) {
    const id = role.value.id;
    if ((await call(() => apiDeleteRole(id))) === undefined) return;
  } else roles.value = roles.value.filter((r) => r.id !== role.value.id);
  setQuery({ role: undefined, tab: undefined });
  message.success(`已删除角色：${name}`);
}

// 新建角色
const nrOpen = ref(false);
const nrBlank = () => ({ name: '', desc: '', perms: [] as string[] });
const nr = reactive(nrBlank());
const nrBase = ref('');
const nrDirty = computed(() => nrOpen.value && JSON.stringify(nr) !== nrBase.value);
useUnsavedGuard(nrDirty);
const NR_PERMS = ['查看对象', '导出数据', '执行动作', '发起变更', '审批变更', '管理用户'];
function openNewRole() { Object.assign(nr, nrBlank()); nrBase.value = JSON.stringify(nr); nrOpen.value = true; }
async function closeNewRole() { if (nrDirty.value && !(await confirmDiscard())) return; nrOpen.value = false; }
async function saveNewRole() {
  const ok = await useConfirmStore().ask({ title: '新建角色', target: nr.name.trim(), okText: '新建', fields: [{ label: '权限', value: nr.perms.join('、') || '无' }], impact: '新角色暂无成员；在「成员」页签添加用户后生效。' });
  if (!ok) return;
  if (remote.value) {
    const r = await call(() => createRole({ name: nr.name.trim(), desc: nr.desc, perms: [...nr.perms] }));
    if (!r) return;
    nrBase.value = JSON.stringify(nr); nrOpen.value = false;
    setQuery({ role: r.id, tab: undefined, form: undefined });
    message.success(`已新建角色：${r.name}`);
    return;
  }
  nrBase.value = JSON.stringify(nr); nrOpen.value = false;
  message.success(`已新建角色：${nr.name.trim()}`);
}
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-tooltip :title="reason"><a-button type="primary" :disabled="readonly" @click="openNewRole">新建角色</a-button></a-tooltip>
  </Teleport>
  <a-drawer :open="nrOpen && !readonly" :width="480" title="新建角色" @close="closeNewRole">
    <a-form layout="vertical" :model="nr">
      <a-form-item label="名称" required><a-input v-model:value="nr.name" :maxlength="20" /></a-form-item>
      <a-form-item label="说明"><a-textarea v-model:value="nr.desc" :rows="3" /></a-form-item>
      <a-form-item label="权限"><a-checkbox-group v-model:value="nr.perms" :options="NR_PERMS" /></a-form-item>
    </a-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button @click="closeNewRole">取消</a-button>
        <a-button type="primary" :disabled="!nr.name.trim()" @click="saveNewRole">新建</a-button>
      </div>
    </template>
  </a-drawer>
  <div class="rl">
    <div class="panel rl-list">
      <div class="panel-head"><span class="t-16 fw-600">角色</span><span class="t-14 c-2">共 {{ roles.length }} 个</span></div>
      <div class="rl-items">
        <div v-for="r in roles" :key="r.id" class="rl-item" :class="{ on: r.id === role.id }" @click="select(r.id)">
          <div class="rl-l1" :title="`${r.name}：${r.desc}`">
            <span class="t-14 fw-600 c-1 rl-ellip">{{ r.name }}</span>
            <a-tag v-if="r.builtIn" class="rl-tag">内置</a-tag>
            <span class="t-12 c-2 rl-n num">{{ members[r.id].length }} 人</span>
          </div>
        </div>
      </div>
    </div>

    <div class="panel rl-detail">
      <div class="rl-head">
        <div class="rl-title">
          <span class="t-20 fw-600 c-1">{{ role.name }}<InfoTip :text="role.desc" /></span>
          <a-tag v-if="role.builtIn">内置</a-tag>
          <span class="t-12 c-3 num">{{ role.id }}</span>
        </div>
        <a-space>
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="copyRole">复制角色</a-button></a-tooltip>
          <a-tooltip :title="lockReason"><a-button :disabled="locked" @click="setQuery({ form: 'edit' })">编辑</a-button></a-tooltip>
          <a-tooltip :title="lockReason"><a-button danger :disabled="locked" @click="deleteRole">删除</a-button></a-tooltip>
        </a-space>
      </div>
      <div class="t-12 c-2 rl-sub"><span class="num">{{ members[role.id].length }}</span> 名成员 · 更新于 <span class="num">{{ role.updatedAt }}</span>（{{ role.updatedBy }}）</div>
      <a-tabs v-model:active-key="tab" class="rl-tabs">
        <a-tab-pane key="func" tab="功能权限" />
        <a-tab-pane key="obj" tab="对象与动作权限" />
        <a-tab-pane key="row" :tab="`行级范围（${role.scopes.length}）`" />
        <a-tab-pane key="members" :tab="`成员（${members[role.id].length}）`" />
      </a-tabs>

      <div v-if="tab === 'func'" class="rl-body">
        <div class="t-12 c-2 rl-tip">
          可管理 <span class="num c-1">{{ funcStat('manage') }}</span> · 可查看 <span class="num c-1">{{ funcStat('view') }}</span> · 无权限 <span class="num c-1">{{ funcStat('none') }}</span> 个页面。“管理”包含新建、编辑、发布；“查看”只读。
          <span v-if="lockReason" class="c-3">（{{ lockReason }}）</span>
        </div>
        <div class="rl-areas">
          <div v-for="a in areas" :key="a.area" class="rl-area">
            <div class="t-14 fw-600 c-1 rl-ah">{{ a.area }}<span class="t-12 c-3 fw-400">{{ a.pages.length }} 个页面</span></div>
            <div v-for="p in a.pages" :key="p.key" class="rl-fp">
              <span class="t-12 c-3 rl-mod">{{ p.module }}</span>
              <span class="c-1 rl-ellip">{{ p.title }}</span>
              <a-radio-group v-model:value="draft.func[p.key]" size="small" :disabled="locked" class="rl-lv" :options="LEVELS" option-type="button" />
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="tab === 'obj'" class="rl-body">
        <div class="t-12 c-2 rl-tip">动作还要满足动作类型里的提交条件；能执行动作必须先能查看对象。行级范围在“行级范围”页签维护。<span v-if="lockReason" class="c-3">（{{ lockReason }}）</span></div>
        <a-table size="small" row-key="objType" :columns="ocols" :data-source="draft.objs" :pagination="false">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'obj'"><span class="c-1">{{ record.objType }}</span> <span class="t-12 c-3 num">{{ record.api }}</span></span>
            <a-checkbox v-else-if="column.key === 'view'" v-model:checked="record.view" :disabled="locked" @change="!record.view && ((record.actions = []), (record.export = false))" />
            <a-checkbox v-else-if="column.key === 'export'" v-model:checked="record.export" :disabled="locked || !record.view" />
            <template v-else-if="column.key === 'actions'">
              <span v-if="!objActions(record.objType).length" class="c-3">没有动作</span>
              <a-checkbox-group v-else v-model:value="record.actions" :disabled="locked || !record.view" :options="objActions(record.objType)" />
            </template>
            <span v-else-if="column.key === 'scope'" class="t-12" :class="scopesOf(record.objType).length ? 'c-1' : 'c-3'">{{ scopesOf(record.objType).map(scopeText).join('；') || (record.view ? noScopeText : EMPTY) }}</span>
          </template>
        </a-table>
      </div>

      <div v-else-if="tab === 'row'" class="rl-body">
        <div class="rl-rowhead">
          <span class="t-12 c-2">只能基于对象类型里标为“权限依据”的属性设置。同一对象类型的多条范围取交集；没有范围的对象类型{{ role.defaultDeny ? '默认不可见' : '可见全部行' }}。</span>
          <a-tooltip :title="lockReason"><a-button :disabled="locked" @click="openScope">添加范围</a-button></a-tooltip>
        </div>
        <div class="rl-rule t-12 c-2">
          <div><span class="c-1 fw-600">未配置范围的对象：</span><template v-if="role.defaultDeny">默认<span class="c-1">不可见</span>（业务查看类角色，需逐个对象配置范围才可见）</template><template v-else>可见全部行</template><template v-if="unscopedObjs.length"> · 本角色：{{ unscopedObjs.join('、') }} → {{ role.defaultDeny ? '不可见' : '全部行' }}</template></div>
          <div><span class="c-1 fw-600">指标怎么过滤：</span>先按指标基础对象的范围过滤，再叠加所用维度对象的范围，同时满足才计入。例：门店销售额（基础对象 订单，按 门店 拆分）= 订单 按“大区 = 华东”过滤 + 门店 按“大区 = 华东”过滤{{ role.defaultDeny ? '；维度对象没配范围时该维度不可用' : '' }}。</div>
        </div>
        <a-table size="small" row-key="id" :columns="scols" :data-source="role.scopes" :pagination="false" :locale="{ emptyText: role.defaultDeny ? '没有行级范围：所有对象默认不可见' : '没有行级范围：可见对象的全部行' }">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'cond'" class="c-1">{{ record.prop }} <span class="num">{{ record.op }}</span> {{ record.values.join('、') }}</span>
            <a-tooltip v-else-if="column.key === 'op'" :title="lockReason"><a :class="{ 'rl-dis': locked }" @click="!locked && removeScope(record)">删除范围</a></a-tooltip>
          </template>
        </a-table>
        <div class="t-14 fw-600 c-1 rl-h">可用的权限依据属性</div>
        <div class="rl-props">
          <div v-for="p in SCOPE_PROPS" :key="p.objType + p.prop" class="rl-prop">
            <span class="c-1">{{ p.objType }} · {{ p.prop }}</span>
            <span class="t-12 c-3 rl-ellip">{{ p.values.join('、') }}</span>
          </div>
        </div>
      </div>

      <div v-else class="rl-body rl-mem">
        <div class="rl-rowhead">
          <a-input-search v-model:value="mkw" size="small" placeholder="姓名 / 账号 / ID" class="rl-w220" allow-clear />
          <a-tooltip :title="reason"><a-button :disabled="readonly" @click="openAdd">添加成员</a-button></a-tooltip>
        </div>
        <div class="rl-mtable" :class="{ 'paged-scroll': mpq.overflow.value }">
          <a-table size="small" row-key="id" :columns="mcols" :data-source="mpaged.items" :pagination="false">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'id'" class="num">{{ record.id }}</span>
              <a-badge v-else-if="column.key === 'status'" :status="badge(record.status)" :text="record.status" />
              <span v-else-if="column.key === 'lastLogin'" class="num">{{ record.lastLogin ?? EMPTY }}</span>
              <a-space v-else-if="column.key === 'op'" :size="12">
                <a @click="openPreview(record)">以此用户预览</a>
                <a-tooltip :title="reason"><a :class="{ 'rl-dis': readonly }" @click="!readonly && removeMember(record)">移出</a></a-tooltip>
              </a-space>
            </template>
          </a-table>
        </div>
        <AppPagination
          v-model:page="mpq.page.value" v-model:size="mpq.size.value" class="rl-pager"
          variant="full" :total="mpaged.total" :default-size="mpq.defaultSize" unit="人" :empty="false"
        />
      </div>

      <div v-if="dirty && (tab === 'func' || tab === 'obj')" class="rl-savebar">
        <span class="t-14 c-1">有未保存的修改：功能权限 <span class="num">{{ changedCount.f }}</span> 处，对象与动作 <span class="num">{{ changedCount.o }}</span> 处</span>
        <a-space>
          <a-button @click="resetDraft">放弃修改</a-button>
          <a-button type="primary" @click="saveDraft">保存</a-button>
        </a-space>
      </div>
    </div>
  </div>

  <a-drawer :open="formOpen" :width="520" :title="`编辑角色 · ${role.name}`" @close="closeForm">
    <a-form layout="vertical" :model="form">
      <a-form-item label="角色名" required><a-input v-model:value="form.name" :maxlength="20" show-count /></a-form-item>
      <a-form-item label="说明"><a-textarea v-model:value="form.desc" :rows="3" :maxlength="80" show-count /></a-form-item>
    </a-form>
    <div class="t-12 c-3">权限在详情页签里修改；角色名变化会同步到动作类型提交条件里的“执行人角色”。</div>
    <template #footer>
      <div class="rl-foot"><a-button @click="closeForm">取消</a-button><a-button type="primary" :disabled="!formDirty" @click="saveForm">保存</a-button></div>
    </template>
  </a-drawer>

  <a-drawer :open="q('form') === 'scope'" :width="520" :title="`添加行级范围 · ${role.name}`" @close="closeScope">
    <a-form layout="vertical">
      <a-form-item label="权限依据属性" required><a-select v-model:value="scopeForm.key" :options="scopeOpts" placeholder="对象类型 · 属性" /></a-form-item>
      <a-form-item label="取值" required>
        <a-select v-model:value="scopeForm.values" mode="multiple" :disabled="!scopeProp" :options="(scopeProp?.values ?? []).map((v) => ({ value: v, label: v }))" placeholder="可多选" />
      </a-form-item>
    </a-form>
    <div v-if="scopeProp && scopeForm.values.length" class="rl-preview t-14 c-1">{{ scopeProp.objType }}：{{ scopeProp.prop }} {{ scopeForm.values.length > 1 ? '∈' : '=' }} {{ scopeForm.values.join('、') }}</div>
    <div class="t-12 c-3">只列出对象类型里标为“权限依据”的属性；要用其他属性，先到 构建 → 对象类型 → 属性 里标记。</div>
    <template #footer>
      <div class="rl-foot"><a-button @click="closeScope">取消</a-button><a-button type="primary" :disabled="!scopeProp || !scopeForm.values.length" @click="addScope">添加</a-button></div>
    </template>
  </a-drawer>

  <a-drawer :open="q('form') === 'members'" :width="520" :title="`添加成员 · ${role.name}`" @close="closeAdd">
    <a-form layout="vertical">
      <a-form-item label="用户"><a-select v-model:value="addIds" mode="multiple" show-search option-filter-prop="label" :options="addOpts" placeholder="搜索姓名 / 部门 / ID" /></a-form-item>
    </a-form>
    <div class="t-12 c-3">只能添加“启用”状态的用户。一个用户可以有多个角色，权限取并集。</div>
    <template #footer>
      <div class="rl-foot"><a-button @click="closeAdd">取消</a-button><a-button type="primary" :disabled="!addIds.length" @click="addMembers">添加 {{ addIds.length || '' }}</a-button></div>
    </template>
  </a-drawer>

  <a-drawer :open="!!pv" :width="640" :title="pv ? `以此用户预览 · ${pv.u.name}` : ''" @close="setQuery({ preview: undefined })">
    <template v-if="pv">
      <a-descriptions :column="2" bordered size="small" class="rl-desc">
        <a-descriptions-item label="用户"><span>{{ pv.u.name }}</span> <span class="t-12 c-3 num">{{ pv.u.id }}</span></a-descriptions-item>
        <a-descriptions-item label="部门">{{ pv.u.dept }}</a-descriptions-item>
        <a-descriptions-item label="角色" :span="2">{{ pv.rs.map((r) => r.name).join('、') || '无' }}</a-descriptions-item>
        <a-descriptions-item label="可进入" :span="2">{{ pv.areasIn.join(' / ') || '无' }} · <span class="num">{{ pv.func.length }}</span> 个页面（管理 <span class="num">{{ pv.func.filter((p) => p.lv === 'manage').length }}</span>）</a-descriptions-item>
      </a-descriptions>
      <div class="t-14 fw-600 c-1 rl-h">能看到的数据（多个角色取并集）</div>
      <a-table size="small" row-key="name" :pagination="false" :data-source="pv.objs" :columns="[{ title: '对象类型', dataIndex: 'name', key: 'name', width: 100 }, { title: '可见范围', key: 'scope' }, { title: '导出', key: 'exp', width: 60 }, { title: '可执行的动作', key: 'actions', width: 220 }]">
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'scope'" :class="record.scope === '全部' ? 'c-2' : 'c-1'">{{ record.scope }}</span>
          <span v-else-if="column.key === 'exp'">{{ record.exp ? '可以' : EMPTY }}</span>
          <span v-else-if="column.key === 'actions'" class="t-12">{{ record.actions.join('、') || EMPTY }}</span>
        </template>
      </a-table>
    </template>
    <template #footer>
      <div class="rl-foot">
        <span class="t-12 c-3 rl-fnote">预览为只读，开始和结束都记审计日志</span>
        <a-button @click="setQuery({ preview: undefined })">关闭</a-button>
        <a-button type="primary" @click="enterPreview">进入预览</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.rl { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.rl-list { width: 300px; flex: none; }
.rl-items { flex: 1; min-height: 0; overflow: hidden; }
.rl-item { padding: 10px 16px; border-bottom: var(--border); border-left: 3px solid transparent; cursor: pointer; }
.rl-item:hover { background: var(--g2); }
.rl-item.on { background: var(--brand-bg); border-left-color: var(--brand); }
.rl-l1 { display: flex; align-items: center; gap: 6px; min-width: 0; margin-bottom: 2px; }
.rl-tag { margin: 0; }
.rl-n { margin-left: auto; flex: none; }
.rl-ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; display: block; }
.rl-detail { flex: 1; min-width: 0; position: relative; }
.rl-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px 4px; }
.rl-title { display: flex; align-items: center; gap: 10px; }
.rl-sub { padding: 0 20px; }
.rl-tabs { padding: 0 20px; flex: none; }
.rl-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.rl-body { flex: 1; min-height: 0; padding: 12px 20px 16px; display: flex; flex-direction: column; gap: 10px; }
.rl-tip { flex: none; }
.rl-rule { display: flex; flex-direction: column; gap: 4px; padding: 8px 12px; margin-bottom: 12px; background: var(--g2); border-radius: var(--radius); }
.rl-areas { flex: 1; min-height: 0; display: flex; gap: 16px; }
.rl-area { flex: 1; min-width: 0; border: var(--border); border-radius: var(--radius); padding: 8px 12px; }
.rl-ah { display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 6px; border-bottom: var(--border); }
.rl-fp { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: var(--border); }
.rl-fp:last-child { border-bottom: 0; }
.rl-mod { width: 56px; flex: none; }
.rl-lv { margin-left: auto; flex: none; }
.rl-rowhead { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex: none; }
.rl-h { margin-top: 8px; }
.rl-props { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.rl-prop { border: var(--border); border-radius: var(--radius); padding: 6px 10px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.rl-mem { padding-bottom: 0; }
.rl-mtable { flex: 1; min-height: 0; }
.rl-pager { padding: 4px 0 8px; }
.rl-w220 { width: 220px; }
.rl-dis { color: var(--g6); cursor: not-allowed; }
.rl-savebar { flex: none; display: flex; justify-content: space-between; align-items: center; padding: 10px 20px; border-top: var(--border); background: var(--g1, #fff); }
.rl-foot { display: flex; justify-content: flex-end; align-items: center; gap: 8px; }
.rl-fnote { margin-right: auto; }
.rl-preview { background: var(--g2); border: var(--border); border-radius: var(--radius); padding: 8px 12px; margin-bottom: 8px; }
.rl-desc :deep(.ant-descriptions-item-label) { width: 80px; }
</style>
