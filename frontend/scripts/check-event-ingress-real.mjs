// Execute the actual event drawer resolver with real pending/unknown-latency API data.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
const tick = () => new Promise(setImmediate);
let real = true;
const event = { id: 'producer-actual', typeId: 'actual_type', at: '2026-10-26 10:00:00',
  objId: 'actual-object', objLabel: 'actual-object', summary: '已接收，投递器未配置', outcome: '待投递',
  subscribers: 0, dedupKey: 'actual-key', latencyMs: null, ingestedAt: '2026-10-26 10:00:01',
  source: 'actual-source', traceId: 'actual-trace', ontologyVersion: 'v2026.10.5' };
const dependencies = {
  vue, '@/api/action': { actionApi: { isReal: () => real }, dynamicApi: { isReal: () => true },
    getEvent: async () => event, eventTypes: () => [{ id: 'actual_type', name: '实际类型' }] },
  '@/api/http': { currentApiContext: () => ({ demoAs: 'actual-user' }) },
  '@/api/fallback': { backend: { down: false }, fallBackIfDown: () => false },
  '@/mock/dynamic': { RULES: {}, ALERTS: [] },
  '@/mock/actionEvent': { eventById: () => { throw new Error('real event must never consult mock'); } },
};
const source = readFileSync(new URL('../src/components/actionEventResolvers.ts', import.meta.url), 'utf8');
const result = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const exports = {};
runInNewContext(result, { exports, require: key => { assert.ok(key in dependencies, key); return dependencies[key]; } });
const resolve = exports.ACTION_EVENT_RESOLVERS.event;
assert.equal(resolve(event.id).status.text, '加载中');
await tick();
const view = resolve(event.id);
assert.equal(view.status.text, '待投递');
assert.equal(view.status.color, 'warning');
assert.ok(view.fields.some(f => f.label === '处理延迟' && f.value.includes('未知')));
assert.ok(view.fields.some(f => f.label === '本体版本' && f.value === event.ontologyVersion));
assert.ok(view.fields.some(f => f.label === '生产来源' && f.value === event.source));
assert.equal(view.timeline.length, 1);
assert.equal(view.timeline[0].status, 'warn');
assert.ok(!view.timeline.some(t => /已投递|重试成功|回执成功/.test(t.text)));
const page = readFileSync(new URL('../src/pages/ontology/EventTypes.vue', import.meta.url), 'utf8');
assert.ok(page.includes("s?.p95 ?? null"));
assert.ok(page.includes("record.latencyMs == null ? '未知'"));
assert.ok(page.includes("...(real ? ['待投递'] : [])"));
console.log('Real event pending state, unknown latency, actual ingress metadata and no fabricated delivery checks passed.');
