/**
 * 导航 v5（来源：/workspace/ui/nav_v5.html）——全站唯一的导航定义。
 * 菜单、路由、面包屑都从这里生成；新增二级页面只改这里 + 写页面组件。
 * 三区 → 六个一级模块 → 27 个二级页面。
 */
export type Phase = 1 | 2 | 3 | 4;

export interface NavPage {
  key: string;           // 路由 name，全局唯一，kebab-case
  title: string;         // 菜单与页面标题
  path: string;          // 完整路径：/<区>/<模块>/<页面>
  phase: Phase;          // 第几期
  group?: string;        // 模块内分组（本体模块：资源/语义/自动化/问数调优/发布）
  note?: string;         // v5 里的说明，占位页展示
  component?: () => Promise<unknown>; // 已设计的页面；不填则用占位页
}
export interface NavModule { key: string; title: string; icon: string; pages: NavPage[] }
export interface NavArea { key: 'use' | 'build' | 'admin'; title: string; modules: NavModule[] }

export const NAV: NavArea[] = [
  {
    key: 'use', title: '使用',
    modules: [
      { key: 'qa', title: '智能问数', icon: 'MessageOutlined', pages: [
        { key: 'qa-workbench', title: '问数工作台', path: '/use/qa/workbench', phase: 1, note: '自动选图、下钻、导出；悬停卡预览对象，抽屉看时间线和归因', component: () => import('@/pages/qa/Workbench.vue') },
        { key: 'qa-pages', title: '我的页面与模板库', path: '/use/qa/pages', phase: 1, note: '卡片与固定看板第一期；拖拽编辑、共享、订阅第二期', component: () => import('@/pages/qa/MyPages.vue') },
      ] },
      { key: 'objects', title: '对象', icon: 'AppstoreOutlined', pages: [
        { key: 'object-search', title: '对象搜索', path: '/use/objects/search', phase: 1, note: '全局入口，按类型浏览', component: () => import('@/pages/objects/ObjectSearch.vue') },
        { key: 'object-detail', title: '对象详情', path: '/use/objects/detail', phase: 1, note: '页签：属性、时间线、关联对象；动作就地弹出确认；也做成公共抽屉', component: () => import('@/pages/objects/ObjectDetail.vue') },
      ] },
      { key: 'ops', title: '运行', icon: 'ThunderboltOutlined', pages: [
        { key: 'alerts', title: '告警处理', path: '/use/ops/alerts', phase: 2, note: '含状态机违规这类告警；抽屉看对象和触发规则', component: () => import('@/pages/ops/Alerts.vue') },
        { key: 'action-center', title: '动作中心', path: '/use/ops/actions', phase: 3, note: '页签：待确认、执行记录、结果未知与对账（对账第四期）', component: () => import('@/pages/ops/Actions.vue') },
        { key: 'scenarios', title: '情景推演', path: '/use/ops/scenarios', phase: 4, note: '选定方案转成暂存动作', component: () => import('@/pages/ops/Scenarios.vue') },
      ] },
    ],
  },
  {
    key: 'build', title: '构建',
    modules: [
      { key: 'data', title: '数据接入', icon: 'DatabaseOutlined', pages: [
        { key: 'connections', title: '数据连接', path: '/build/data/connections', phase: 1, note: '详情页签：连通测试、元数据采集、同步任务、数据截止时间；含达梦、金仓' },
        { key: 'external-systems', title: '外部系统', path: '/build/data/external', phase: 3, note: '写回目标、Webhook' },
        { key: 'data-quality', title: '数据质量', path: '/build/data/quality', phase: 2, note: '质量规则与告警' },
      ] },
      { key: 'ontology', title: '本体', icon: 'ApartmentOutlined', pages: [
        { key: 'ontology-overview', title: '本体总览', path: '/build/ontology/overview', phase: 1, group: '资源', note: '关系图谱', component: () => import('@/pages/ontology/OntologyOverview.vue') },
        { key: 'object-types', title: '对象类型', path: '/build/ontology/object-types', phase: 1, group: '资源', note: '页签分三组：定义（概览、属性、关系、数据源与映射）、行为（生命周期、动作、自动化）、使用与治理（指标、视图、权限、版本历史）', component: () => import('@/pages/ontology/ObjectTypes.vue') },
        { key: 'link-types', title: '关系类型', path: '/build/ontology/link-types', phase: 1, group: '资源', component: () => import('@/pages/ontology/LinkTypes.vue') },
        { key: 'action-types', title: '动作类型', path: '/build/ontology/action-types', phase: 3, group: '资源', note: '页签：参数、提交条件、写回模式、执行记录', component: () => import('@/pages/ontology/ActionTypes.vue') },
        { key: 'functions', title: '函数', path: '/build/ontology/functions', phase: 3, group: '资源' },
        { key: 'metrics', title: '指标口径', path: '/build/ontology/metrics', phase: 1, group: '语义', note: '允许的维度、版本历史', component: () => import('@/pages/ontology/Metrics.vue') },
        { key: 'dimensions', title: '维度与字典', path: '/build/ontology/dimensions', phase: 1, group: '语义', note: '页签：定义、取值字典、同义词与问数映射、使用情况、版本历史', component: () => import('@/pages/ontology/Dimensions.vue') },
        { key: 'glossary', title: '业务知识', path: '/build/ontology/glossary', phase: 1, group: '语义', note: '同义词、术语；页签：内容、问数中的作用、引用、版本历史', component: () => import('@/pages/ontology/Glossary.vue') },
        { key: 'event-types', title: '事件类型', path: '/build/ontology/event-types', phase: 2, group: '自动化', component: () => import('@/pages/ontology/EventTypes.vue') },
        { key: 'rules', title: '规则与自动化', path: '/build/ontology/rules', phase: 2, group: '自动化', component: () => import('@/pages/ontology/Rules.vue') },
        { key: 'qa-tuning', title: '问数调优', path: '/build/ontology/qa-tuning', phase: 1, group: '调优', note: '页签：认证问答、评测题库、反馈与纠错；高频问题推荐第二期', component: () => import('@/pages/ontology/QaTuning.vue') },
        { key: 'releases', title: '变更与发布', path: '/build/ontology/releases', phase: 1, group: '发布', note: '依赖图（血缘和影响分析合一）、版本对比、评审', component: () => import('@/pages/ontology/Releases.vue') },
      ] },
    ],
  },
  {
    key: 'admin', title: '管理',
    modules: [
      { key: 'system', title: '系统管理', icon: 'SettingOutlined', pages: [
        { key: 'users', title: '用户与组织', path: '/admin/system/users', phase: 1, component: () => import('@/pages/admin/Users.vue') },
        { key: 'roles', title: '角色与权限', path: '/admin/system/roles', phase: 1, note: '含以某用户预览' },
        { key: 'audit', title: '审计日志', path: '/admin/system/audit', phase: 1, component: () => import('@/pages/admin/AuditLog.vue') },
        { key: 'api-keys', title: 'API 密钥', path: '/admin/system/api-keys', phase: 3 },
        { key: 'settings', title: '模型与系统配置', path: '/admin/system/settings', phase: 1 },
      ] },
    ],
  },
];

export interface FlatPage extends NavPage { area: NavArea; module: NavModule }
export const PAGES: FlatPage[] = NAV.flatMap((area) =>
  area.modules.flatMap((module) => module.pages.map((p) => ({ ...p, area, module }))),
);
export const findPage = (key: string) => PAGES.find((p) => p.key === key);

/** 对象类型页的三组页签（v5） */
export const OBJECT_TYPE_TAB_GROUPS = [
  { key: 'def', title: '定义', tabs: [
    { key: 'overview', title: '概览' }, { key: 'properties', title: '属性' },
    { key: 'links', title: '关系' }, { key: 'mapping', title: '数据源与映射' },
  ] },
  { key: 'behavior', title: '行为', tabs: [
    { key: 'lifecycle', title: '生命周期' }, { key: 'actions', title: '动作' }, { key: 'automation', title: '自动化' },
  ] },
  { key: 'governance', title: '使用与治理', tabs: [
    { key: 'metrics', title: '指标' }, { key: 'views', title: '视图' },
    { key: 'permissions', title: '权限' }, { key: 'history', title: '版本历史' },
  ] },
];
