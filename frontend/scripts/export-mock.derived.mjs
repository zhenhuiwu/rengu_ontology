/**
 * export-mock 的派生调用清单：mock 里有些种子数据是函数按参数算出来的（顶层导出是函数，主脚本会跳过），
 * 这里列出每个函数用哪些参数调用。主脚本在同一个进程里（TZ 已固定 Asia/Shanghai、mock 已按原顺序加载）逐个调用，
 * 结果写到 ${OUT_DIR}/<模块>/derived/<函数>.json，形如 [{ args: [...], result }]，参数也记进 manifest.derived。
 *
 * 参数里的对象不整份写进 JSON，写成引用：{ ref: '<模块>/<导出名>', key: '<主键字段>', id: '<值>' }，
 * 后台按引用去对应的导出 JSON 里找那一行。
 *
 * 参数来源（spec）：
 *   action  ：docs/backend/action-event-api.md §7.1.1（palantir-backend-doc 分支 cedf5ae）
 *   dynamic ：docs/backend/dynamic-api.md §5.1（dynamic 分支 dfa6111）
 *   static  ：docs/backend/static-api.md 种子一节（103d302）
 *   qa / system：负责人文档没有逐条参数，按函数签名 + 页面调用处 + 导出数据里的 id 推断，manifest 里标 guessed。
 * 「页面拼装」两项（告警页关联动作、外部系统页 opsOf）是照抄页面组件里的逻辑，页面改了这里要跟着改，同样标 guessed。
 *
 * 只调用、不改任何 mock 值。调用顺序固定，结果逐字节可复现。
 */

/** 参数引用 */
const ref = (src, key, row) => ({ ref: src, key, id: row[key] });

/**
 * @param {(file: string) => Promise<Record<string, any>>} load  ssrLoadModule 包装（'/src/...'）
 * @returns {Promise<Array<{ module: string, fn: string, spec: string, guessed?: string, calls: Array<{ args: any[], run: () => any }> }>>}
 */
export default async function derived(load) {
  const ae = await load('/src/mock/actionEvent.ts');
  const rel = await load('/src/mock/releases.ts');
  const hist = await load('/src/mock/objectHistory.ts');
  const resolvers = (await load('/src/components/actionEventResolvers.ts')).ACTION_EVENT_RESOLVERS;
  const dyn = await load('/src/mock/dynamic.ts');
  const rules = await load('/src/mock/rules.ts');
  const acts = await load('/src/mock/actions.ts');
  const lc = await load('/src/mock/lifecycle.ts');
  const sc = await load('/src/mock/scenarios.ts');
  const ext = await load('/src/mock/extsys.ts');
  const keys = await load('/src/mock/apikeys.ts');
  const ds = await load('/src/mock/datasources.ts');
  const roles = await load('/src/mock/roles.ts');
  const oqa = await load('/src/mock/objects-qa.ts');
  const qa = await load('/src/mock/qa.ts');
  const stores = await load('/src/mock/stores.ts');
  const products = await load('/src/mock/products.ts');
  const orders = await load('/src/mock/orders.ts');
  const quality = await load('/src/mock/quality.ts');
  const views = await load('/src/mock/objectViews.ts');
  const props = await load('/src/mock/objectProps.ts');
  const dims = await load('/src/mock/dimensions.ts');
  const changes = await load('/src/mock/changes.ts');

  const out = [];
  const add = (module, fn, spec, calls, guessed) => out.push({ module, fn, spec, ...(guessed ? { guessed } : {}), calls });

  /* ---------------- action（action-event-api.md §7.1.1） ---------------- */
  const AT = ae.ACTION_TYPES, ET = ae.EVENT_TYPES;
  const S_ACT = 'action-event-api.md §7.1.1 (cedf5ae)';
  const atRef = (a) => ref('action/ACTION_TYPES', 'id', a);
  const etRef = (e) => ref('action/EVENT_TYPES', 'id', e);
  add('action', 'paramsOf', S_ACT, AT.map((a) => ({ args: [atRef(a)], run: () => ae.paramsOf(a) })));
  add('action', 'condsOf', S_ACT, AT.map((a) => ({ args: [atRef(a)], run: () => ae.condsOf(a) })));
  add('action', 'writebackOf', S_ACT, AT.map((a) => ({ args: [atRef(a)], run: () => ae.writebackOf(a) })));
  add('action', 'runsOf', S_ACT, AT.map((a) => ({ args: [a.id], run: () => ae.runsOf(a.id) })));
  add('action', 'runStats', S_ACT, AT.map((a) => ({ args: [a.id], run: () => ae.runStats(a.id) })));
  add('action', 'runDaily', S_ACT, AT.map((a) => ({ args: [a.id], run: () => ae.runDaily(a.id) })));
  add('action', 'versionsOf.action', S_ACT, AT.map((a) => ({
    args: [a.version, a.owner, { ref: 'action/ACTION_VERSION_NOTES' }], run: () => ae.versionsOf(a.version, a.owner, ae.ACTION_VERSION_NOTES) })));
  add('action', 'writebackSystems', S_ACT, [{ args: [], run: () => ae.writebackSystems() }]);
  add('action', 'payloadOf', S_ACT, ET.map((e) => ({ args: [etRef(e)], run: () => ae.payloadOf(e) })));
  add('action', 'subscribersOf', S_ACT, ET.map((e) => ({ args: [etRef(e)], run: () => ae.subscribersOf(e) })));
  add('action', 'eventsOf', S_ACT, ET.map((e) => ({ args: [e.id], run: () => ae.eventsOf(e.id) })));
  add('action', 'eventDaily', S_ACT, ET.map((e) => ({ args: [e.id, e.weekly], run: () => ae.eventDaily(e.id, e.weekly) })));
  add('action', 'versionsOf.event', S_ACT, ET.map((e) => ({
    args: [e.version, e.owner, { ref: 'action/EVENT_VERSION_NOTES' }], run: () => ae.versionsOf(e.version, e.owner, ae.EVENT_VERSION_NOTES) })));
  const runIds = AT.flatMap((a) => ae.runsOf(a.id).map((r) => r.id));
  const eventIds = ET.flatMap((e) => ae.eventsOf(e.id).map((x) => x.id));
  add('action', 'ACTION_EVENT_RESOLVERS.action', S_ACT, runIds.map((id) => ({ args: [id], run: () => resolvers.action(id) })));
  add('action', 'ACTION_EVENT_RESOLVERS.event', S_ACT, eventIds.map((id) => ({ args: [id], run: () => resolvers.event(id) })));

  /* ---------------- dynamic（dynamic-api.md §5.1） ---------------- */
  const S_DYN = 'dynamic-api.md §5.1 (dfa6111)';
  add('dynamic', 'alertTimeline', S_DYN, dyn.ALERTS.map((a) => ({ args: [ref('dynamic/ALERTS', 'id', a)], run: () => dyn.alertTimeline(a) })));
  add('dynamic', 'storeOfKey', 'guessed', dyn.ALERTS.filter((a) => a.objKind === 'product').map((a) => ({ args: [a.id], run: () => dyn.storeOfKey(a.id) })),
    'dynamic.ts 只对“商品类且没有 storeId 的告警”调用，加载后已回填 storeId，无法区分；这里对全部商品类告警 id 调用。rules.ts 里命中 id 的调用已体现在 hitsOf 结果里');
  add('dynamic', 'hitsOf', S_DYN, rules.RULE_DEFS.map((r) => ({ args: [r.id], run: () => rules.hitsOf(r.id) })));
  // 不传 draftVer 的 16 次放 ruleVersions.json；有草稿的（RULE_DRAFTS）带 draftVer 再调一次，单独放 ruleVersions.draft.json，避免与已发布版本行混在一起
  add('dynamic', 'ruleVersions', S_DYN, rules.RULE_DEFS.map((r) => ({ args: [ref('dynamic/RULE_DEFS', 'id', r)], run: () => rules.ruleVersions(r) })));
  add('dynamic', 'ruleVersions.draft', S_DYN, rules.RULE_DEFS.filter((r) => rules.RULE_DRAFTS[r.id]?.version).map((r) => ({
    args: [ref('dynamic/RULE_DEFS', 'id', r), rules.RULE_DRAFTS[r.id].version], run: () => rules.ruleVersions(r, rules.RULE_DRAFTS[r.id].version) })));
  add('dynamic', 'buildLog', S_DYN, acts.ACTIONS.map((x) => ({ args: [ref('dynamic/ACTIONS', 'id', x)], run: () => acts.buildLog(x) })));
  const done = sc.SCENARIOS.filter((s) => s.status === '已完成' && s.result);
  add('dynamic', 'simulate', S_DYN, done.map((s) => ({
    args: [{ ...ref('dynamic/SCENARIOS', 'id', s), field: 'scope' }, { ...ref('dynamic/SCENARIOS', 'id', s), field: 'drivers' }, s.result.ranAt],
    run: () => sc.simulate(s.scope, s.drivers, s.result.ranAt) })));
  add('dynamic', 'stagePlanOf', S_DYN, done.map((s) => ({ args: [ref('dynamic/SCENARIOS', 'id', s)], run: () => sc.stagePlanOf(s) })));
  add('dynamic', 'changesOf', S_DYN, lc.LIFECYCLES.map((l) => ({ args: [l.objectId], run: () => lc.changesOf(l.objectId) })));
  add('dynamic', 'illegalOf', S_DYN, lc.LIFECYCLES.map((l) => ({ args: [l.objectId], run: () => lc.illegalOf(l.objectId) })));
  add('dynamic', 'lifecycleAsOf', S_DYN, lc.LIFECYCLES.flatMap((l) => l.versions.map((v) => ({ args: [l.objectId, v.at], run: () => lc.lifecycleAsOf(l.objectId, v.at) }))));
  // 照抄 pages/ops/Alerts.vue L23–28：在 ACTIONS 里找 source.alertId 相同的第一条，未关闭的告警补一行日志
  add('dynamic', 'alertActionLink', S_DYN, dyn.ALERTS.map((a) => ({
    args: [ref('dynamic/ALERTS', 'id', a)],
    run: () => {
      const act = acts.ACTIONS.find((x) => x.source.alertId === a.id);
      const open = !(a.status === '已解决' || a.status === '已忽略');
      return {
        alert_id: a.id, action_run_id: act?.id ?? null, idem_key: act?.idemKey ?? null, status: act?.status ?? null,
        log_line: act && open ? { time: act.submittedAt, text: `发起动作「${act.action}」（${act.idemKey}），现为${act.status}`, who: act.initiator } : null,
      };
    },
  })), '页面逻辑照抄（pages/ops/Alerts.vue L23–28），Alerts.vue 改了要同步');
  add('dynamic', 'ACTION_EVENT_RESOLVERS.rule', 'action-event-api.md §7.1.1（可选）', Object.keys(dyn.RULES).map((id) => ({ args: [id], run: () => resolvers.rule(id) })));

  /* ---------------- system（无负责人逐条参数，按页面调用处推断） ---------------- */
  // 照抄 pages/data/ExternalSystems.vue 的 opsOf / samplesOf / fromCenter / fromRun（页面传给 extCalls 的 ops）
  const ruleActor = (rule) => `规则 ${rule} · 确认人 ${acts.RULE_CONFIRMER[rule] ?? '—'}`;
  const withType = (type, label) => (label.includes(type) ? label : `${type} ${label}`);
  const fromCenter = (x) => ({
    obj: withType(acts.OBJ_KIND_LABEL[x.objKind], x.objLabel), actor: x.source.ruleId ? ruleActor(x.source.ruleId) : x.initiator,
    runId: x.id, idemKey: x.idemKey, unknown: x.status === '结果未知',
    time: x.log.find((l) => l.text.startsWith('调用'))?.time ?? x.submittedAt,
  });
  const fromRun = (a, x) => ({
    obj: withType(a.target, x.objLabel.replace(/^分群 · /, '')), actor: x.ruleId ? ruleActor(x.ruleId) : x.initiator,
    runId: x.id, idemKey: x.idemKey, unknown: false,
  });
  const DONE = ['成功', '失败', '结果未知', '已对账', '执行中'];
  const samplesOf = (a) => {
    const center = acts.ACTIONS.filter((x) => x.action === a.name && DONE.includes(x.status));
    if (center.length) return center.map(fromCenter);
    return ae.runsOf(a.id).filter((x) => DONE.includes(x.status)).map((x) => fromRun(a, x));
  };
  const opsOf = (sys) => {
    const as = AT.filter((a) => a.sysId === sys.id);
    if (as.length) return as.filter((a) => a.status !== '草稿').map((a) => { const [method, path] = a.endpoint.split(' '); return { name: a.name, method, path, samples: samplesOf(a) }; });
    return (sys.usage?.ops ?? []).map((name) => ({ name }));
  };
  add('system', 'extCalls', 'guessed', ext.EXT_SYSTEMS.map((s) => ({
    args: [ref('system/EXT_SYSTEMS', 'id', s), { opsOf: s.id, note: 'pages/data/ExternalSystems.vue opsOf(sys)' }], run: () => ext.extCalls(s, opsOf(s)) })),
    'ops 参数照抄 pages/data/ExternalSystems.vue 的 opsOf/samplesOf（页面里算好传入），页面改了要同步');
  add('system', 'keyCalls', 'guessed', keys.API_KEYS.map((k) => ({ args: [ref('system/API_KEYS', 'id', k)], run: () => keys.keyCalls(k) })),
    '按 pages/admin/ApiKeys.vue 调用处：每个密钥一次');
  add('system', 'dsTables', 'guessed', ds.DATASOURCES.map((d) => ({ args: [ref('system/DATASOURCES', 'id', d)], run: () => ds.dsTables(d) })),
    '每个数据源一次');
  add('system', 'roleMembers', 'guessed', roles.ROLES.map((r) => ({ args: [ref('system/ROLES', 'id', r)], run: () => roles.roleMembers(r) })),
    '按 pages/admin/Roles.vue 调用处：每个角色一次');

  /* ---------------- qa（qa-api.md 只写了来源函数，参数推断） ---------------- */
  // OBJ_TYPES 每个类型的函数字段：rows() 逐行求 id / title / kw，以及每个分面 get、每列 get
  add('qa', 'OBJ_TYPES.fields', 'guessed', oqa.OBJ_TYPES.map((t) => ({
    args: [t.kind],
    run: () => t.rows().map((row) => ({
      id: t.id(row), title: t.title(row), kw: t.kw(row),
      facets: Object.fromEntries(t.facets.map((f) => [f.key, f.get(row)])),
      columns: Object.fromEntries(t.columns.map((c) => [c.key, c.get(row)])),
    })),
  })), '每个对象类型对 rows() 的每一行求 id/title/kw/facets[].get/columns[].get');
  const objIds = [
    ...stores.ALL_STORES.map((s) => ['store', s.id]),
    ['region', qa.REGION_EC.id],
    ...products.PRODUCTS.map((p) => ['product', p.sku]),
    ...orders.ORDERS.map((o) => ['order', o.id]),
  ];
  const OBJ_NOTE = '对象详情页按 (kind, id) 调用；遍历 ALL_STORES、REGION_EC、PRODUCTS、ORDERS 全部 id';
  add('qa', 'storeSales', 'guessed', stores.ALL_STORES.map((s) => ({ args: [s.id], run: () => oqa.storeSales(s.id) })), '每个门店 id（ALL_STORES）一次');
  add('qa', 'actionRecords', 'guessed', objIds.map(([k, id]) => ({ args: [k, id], run: () => oqa.actionRecords(k, id) })), OBJ_NOTE);
  add('qa', 'objActions', 'guessed', objIds.map(([k, id]) => ({ args: [k, id], run: () => oqa.objActions(k, id) })), OBJ_NOTE);

  /* ---------------- static（static-api.md 种子一节，103d302） ---------------- */
  const S_ST = 'static-api.md 种子 (103d302)';
  const OT = quality.Q_OBJECT_TYPES; // 与 ObjectTypes.vue 的 TYPES 一致（id = key）
  add('static', 'viewsOf', S_ST, OT.map((t) => ({ args: [t.key, t.name], run: () => views.viewsOf(t.key, t.name) })));
  add('static', 'objectMeta', S_ST, OT.map((t) => ({ args: [t.key, t.name, t.group], run: () => props.objectMeta(t.key, t.name, t.group) })));
  // 草稿单 → 对象类型：mock/releases.ts 的 DRAFT_TARGETS（ObjectTypes.vue / ViewsTab.vue 只对 draftOf('object', id) 命中的单调用）
  const draftObj = Object.entries(rel.DRAFT_TARGETS).flatMap(([cr, t]) => (t.object ?? []).map((id) => [cr, OT.find((o) => o.key === id)]).filter(([, o]) => o));
  add('static', 'draftPropDiff', 'DRAFT_TARGETS (6428b87)；props 用 OBJECT_PROPS，不含页面草稿新增属性 ADDED', draftObj.map(([cr, t]) => ({
    args: [cr, t.name, { ref: 'static/OBJECT_PROPS', key: t.key }], run: () => props.draftPropDiff(cr, t.name, props.OBJECT_PROPS[t.key] ?? []) })));
  // 历史版本页签（HistoryTab.vue / ObjectTypes.vue）按 (对象类型 id, 名称) 调用，每个对象类型一次
  add('static', 'legacyRevs', S_ST, OT.map((t) => ({ args: [t.key, t.name], run: () => hist.legacyRevs(t.key, t.name) })));
  add('static', 'dsTables', S_ST, ds.DATASOURCES.map((d) => ({ args: [ref('system/DATASOURCES', 'id', d)], run: () => ds.dsTables(d) })));
  add('static', 'metricUsage', S_ST, dims.DIM_ITEMS.map((d) => ({ args: [ref('static/DIM_ITEMS', 'id', d)], run: () => dims.metricUsage(d) })));
  add('static', 'reviewLog', S_ST, changes.CHANGE_REQUESTS.map((c) => ({ args: [ref('static/CHANGE_REQUESTS', 'id', c)], run: () => changes.reviewLog(c) })));
  return out;
}
