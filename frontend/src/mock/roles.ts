/**
 * 角色与权限示例数据（PDE工程师）。
 * - 功能权限：按导航的区 / 模块 / 页面勾选（查看 / 管理）。
 * - 对象与动作权限：每个对象类型的 查看 / 导出，以及可执行的动作（动作名与 actionEvent.ts 一致）。
 * - 行级范围：只能基于对象类型里标为“权限依据”的属性（大区、门店、品类、仓库），例如 大区 = 华东。
 * - 成员：前 5 个角色取 ADMIN_USERS 里的 role；后 3 个角色按固定规则从用户里挑，人数都超过一页。
 */
import { ADMIN_USERS, type AdminUser } from './admin';
import { NAV } from '@/nav';

export type FuncLevel = 'none' | 'view' | 'manage';
export interface RowScope { id: string; objType: string; prop: string; op: '=' | '∈'; values: string[] }
export interface ObjPerm { objType: string; api: string; view: boolean; export: boolean; actions: string[] }
export interface Role {
  id: string; name: string; desc: string; builtIn: boolean; updatedAt: string; updatedBy: string;
  func: Record<string, FuncLevel>;        // 页面 key → 权限
  objs: ObjPerm[];
  scopes: RowScope[];
  /** 业务查看类角色：没有配置行级范围的对象类型默认不可见（其余角色默认可见全部行） */
  defaultDeny?: boolean;
}

/** 对象类型上标为“权限依据”的属性（对象类型页 → 属性 → 权限依据） */
export const SCOPE_PROPS: { objType: string; prop: string; values: string[] }[] = [
  { objType: '订单', prop: '大区', values: ['华东', '华南', '华北', '华中', '西南', '西北'] },
  { objType: '订单', prop: '门店', values: ['上海徐汇店', '上海静安店', '杭州西湖店', '南京新街口店', '苏州园区店', '宁波天一店'] },
  { objType: '门店', prop: '大区', values: ['华东', '华南', '华北', '华中', '西南', '西北'] },
  { objType: '会员', prop: '常去门店所属大区', values: ['华东', '华南', '华北', '华中', '西南', '西北'] },
  { objType: '商品', prop: '品类', values: ['生鲜', '乳品烘焙', '粮油调味', '休闲零食', '酒水饮料', '日化清洁'] },
  { objType: '库存快照', prop: '仓库', values: ['上海嘉定仓', '杭州余杭仓', '南京江宁仓', '广州白云仓'] },
];

export const PERM_OBJECTS: { name: string; api: string; actions: string[] }[] = [
  { name: '订单', api: 'SalesOrder', actions: ['订单状态修正', '发起物流催派', '补发出库单', '退款审核'] },
  { name: '会员', api: 'Member', actions: ['发放复购召回优惠券', '调整会员等级'] },
  { name: '门店', api: 'Store', actions: ['变更门店状态'] },
  { name: '大区', api: 'Region', actions: ['指派大区经理跟进', '调整大区预算'] },
  { name: '商品', api: 'Product', actions: ['补货申请', '门店调价', '发起调价审批', '调整安全库存'] },
  { name: '库存快照', api: 'InventorySnapshot', actions: [] },
  { name: '告警', api: 'Alert', actions: ['认领告警', '转派告警', '关闭告警'] },
  { name: '员工', api: 'Employee', actions: [] },
];

export const FUNC_PAGES = NAV.flatMap((a) => a.modules.flatMap((m) => m.pages.map((p) => ({ area: a.title, areaKey: a.key, module: m.title, key: p.key, title: p.title }))));

const funcOf = (rule: (p: (typeof FUNC_PAGES)[number]) => FuncLevel) => Object.fromEntries(FUNC_PAGES.map((p) => [p.key, rule(p)]));
const objs = (rule: (o: (typeof PERM_OBJECTS)[number]) => [boolean, boolean, string[]]) =>
  PERM_OBJECTS.map((o) => { const [view, exp, actions] = rule(o); return { objType: o.name, api: o.api, view, export: exp, actions }; });
const sc = (id: string, objType: string, prop: string, values: string[]): RowScope => ({ id, objType, prop, op: values.length > 1 ? '∈' : '=', values });

export const ROLES: Role[] = [
  { id: 'R-01', name: '系统管理员', desc: '管理用户、角色、密钥和系统配置；不默认拥有业务数据导出', builtIn: true, updatedAt: '2026-09-02 10:12', updatedBy: '王敏',
    func: funcOf(() => 'manage'), objs: objs(() => [true, false, []]), scopes: [] },
  { id: 'R-02', name: '本体建模师', desc: '维护对象类型、关系、动作、函数和指标口径，发布需评审', builtIn: true, updatedAt: '2026-10-11 15:40', updatedBy: '王敏',
    func: funcOf((p) => (p.areaKey === 'build' ? 'manage' : p.areaKey === 'use' ? 'view' : 'none')), objs: objs(() => [true, false, []]), scopes: [] },
  { id: 'R-03', name: '数据分析师', desc: '问数、看板和对象查询，可导出明细', builtIn: true, updatedAt: '2026-10-18 09:27', updatedBy: '陈立军',
    func: funcOf((p) => (p.areaKey === 'use' ? 'manage' : ['metrics', 'dimensions', 'glossary', 'ontology-overview'].includes(p.key) ? 'view' : 'none')),
    objs: objs((o) => [true, o.name !== '员工', []]), scopes: [sc('S-31', '订单', '大区', ['华东', '华南'])] },
  { id: 'R-04', name: '动作审批人', desc: '审批需要确认的动作，可执行订单与库存类动作', builtIn: false, updatedAt: '2026-10-22 18:05', updatedBy: '孙浩然',
    func: funcOf((p) => (p.areaKey === 'use' ? (p.key === 'action-center' || p.key === 'alerts' ? 'manage' : 'view') : 'none')),
    objs: objs((o) => [o.name !== '员工', false, ['订单', '商品', '告警'].includes(o.name) ? o.actions.filter((a) => a !== '调整安全库存') : []]),
    scopes: [sc('S-41', '订单', '大区', ['华东']), sc('S-42', '商品', '品类', ['生鲜', '乳品烘焙', '粮油调味'])] },
  { id: 'R-05', name: '业务查看', desc: '只读查看问数结果和对象详情，不能执行动作和导出', builtIn: true, updatedAt: '2026-08-30 11:00', updatedBy: '王敏',
    func: funcOf((p) => (p.areaKey === 'use' && p.key !== 'scenarios' ? 'view' : 'none')), objs: objs((o) => [o.name !== '员工', false, []]),
    scopes: [sc('S-51', '订单', '大区', ['华东']), sc('S-52', '门店', '大区', ['华东']), sc('S-53', '会员', '常去门店所属大区', ['华东'])], defaultDeny: true },
  { id: 'R-06', name: '门店店长', desc: '只看本店订单和会员，可发放召回券、申请补货', builtIn: false, updatedAt: '2026-10-24 20:16', updatedBy: '陈立军',
    func: funcOf((p) => (['qa-workbench', 'object-search', 'object-detail', 'alerts'].includes(p.key) ? 'manage' : 'none')),
    objs: objs((o) => [['订单', '会员', '门店', '商品', '库存快照', '告警'].includes(o.name), false, o.actions.filter((a) => ['发放复购召回优惠券', '补货申请', '认领告警'].includes(a))]),
    scopes: [sc('S-61', '订单', '门店', ['上海徐汇店']), sc('S-62', '库存快照', '仓库', ['上海嘉定仓'])], defaultDeny: true },
  { id: 'R-07', name: '会员运营', desc: '会员分层与召回活动，可发券、调整会员等级', builtIn: false, updatedAt: '2026-10-20 14:33', updatedBy: '吴佳',
    func: funcOf((p) => (p.areaKey === 'use' && p.key !== 'scenarios' ? (p.key === 'qa-workbench' ? 'manage' : 'view') : 'none')),
    objs: objs((o) => [['订单', '会员', '门店', '商品'].includes(o.name), o.name === '会员', o.name === '会员' ? o.actions : []]),
    scopes: [sc('S-71', '会员', '常去门店所属大区', ['华东', '华中'])] },
  { id: 'R-08', name: '大区经理（华东）', desc: '华东大区经营分析，可指派跟进、调整大区预算', builtIn: false, updatedAt: '2026-10-25 09:48', updatedBy: '刘婷',
    func: funcOf((p) => (p.areaKey === 'use' ? 'manage' : p.key === 'metrics' ? 'view' : 'none')),
    objs: objs((o) => [o.name !== '员工', ['订单', '门店'].includes(o.name), o.name === '大区' || o.name === '告警' ? o.actions : []]),
    scopes: [sc('S-81', '订单', '大区', ['华东']), sc('S-82', '门店', '大区', ['华东']), sc('S-83', '会员', '常去门店所属大区', ['华东'])], defaultDeny: true },
];

/** 角色成员：内置角色按 ADMIN_USERS.role；其余按用户 ID 固定挑选（一个用户可有多个角色） */
export function roleMembers(role: Role): AdminUser[] {
  const n = Number(role.id.slice(2));
  const base = ADMIN_USERS.filter((u) => u.role === role.name);
  // 主角色人数少的内置角色，再按固定规则补一些兼任的用户，保证成员超过一页
  if (base.length >= 20) return base;
  if (base.length) return [...base, ...ADMIN_USERS.filter((u, i) => u.role !== role.name && u.status === '启用' && (i * 7 + n * 3) % 8 === 0)];
  return ADMIN_USERS.filter((u, i) => u.status !== '待激活' && (i * 7 + n * 3) % (n === 6 ? 4 : 5) === 0);
}

/** 用户的全部角色（用于“以此用户预览”） */
export const userRoles = (u: AdminUser) => ROLES.filter((r) => roleMembers(r).some((m) => m.id === u.id));

export const scopeText = (s: RowScope) => `${s.prop} ${s.op} ${s.values.join('、')}`;

/** 一个用户的有效权限：所有角色合并（“以此用户预览”的抽屉和预览模式共用） */
export interface EffectiveObj { name: string; scope: string; actions: string[]; exp: boolean }
export interface EffectiveAccess {
  rs: Role[];
  func: ((typeof FUNC_PAGES)[number] & { lv: FuncLevel })[];   // 只含有权限的页面
  objs: EffectiveObj[];                                        // 只含能查看的对象类型
  areasIn: string[];
}
export function effectiveAccess(u: AdminUser, roles: Role[] = ROLES, membersOf: (r: Role) => AdminUser[] = roleMembers): EffectiveAccess {
  const rs = roles.filter((r) => membersOf(r).some((m) => m.id === u.id));
  const rank = { none: 0, view: 1, manage: 2 } as const;
  // 功能：各角色取最高级别
  const func = FUNC_PAGES.map((p) => ({ ...p, lv: rs.reduce<FuncLevel>((m, r) => (rank[r.func[p.key]] > rank[m] ? r.func[p.key] : m), 'none') })).filter((p) => p.lv !== 'none');
  const objs = PERM_OBJECTS.map((o) => {
    // 业务查看类角色（defaultDeny）对没配范围的对象不授予可见
    const granted = rs.filter((r) => r.objs.find((x) => x.objType === o.name)?.view && !(r.defaultDeny && !r.scopes.some((s) => s.objType === o.name)));
    if (!granted.length) return null;
    // 多个角色：任一角色对该对象不限范围 → 全部；否则各角色范围取并集
    const unlimited = granted.some((r) => !r.scopes.some((s) => s.objType === o.name));
    const scopes = unlimited ? [] : granted.flatMap((r) => r.scopes.filter((s) => s.objType === o.name).map(scopeText));
    const actions = [...new Set(rs.flatMap((r) => r.objs.find((x) => x.objType === o.name)?.actions ?? []))];
    const exp = rs.some((r) => r.objs.find((x) => x.objType === o.name)?.export);
    return { name: o.name, scope: unlimited ? '全部' : [...new Set(scopes)].join('；或 '), actions, exp };
  }).filter((x): x is EffectiveObj => !!x);
  return { rs, func, objs, areasIn: [...new Set(func.map((p) => p.area))] };
}
