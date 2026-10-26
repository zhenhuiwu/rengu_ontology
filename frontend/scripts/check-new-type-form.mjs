// Run: node scripts/check-new-type-form.mjs; isolated requests, no database writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const vue = require('vue');
const context = vue.reactive({ demoAs: 'DB-USER' });
const open = vue.ref(true), readonly = vue.ref(false);
const route = vue.reactive({ query: {} });
const puts = [], starts = [], errors = [];
let lock = 7, fail = false;
const crChanges = {};
const api = { get: async (path, options) => {
  if (path === '/build/owners') return [{ id: 'DB-USER', name: '真实负责人' }];
  assert.equal(path, '/object-types');
  assert.equal(options.headers['X-Demo-As'], 'DB-USER');
  return options.query.page === 1
    ? { items: Array.from({ length: 200 }, (_, n) => ({ id: `o${n}`, name: `实际对象${n}`, api: `o${n}` })), total: 201 }
    : { items: [{ id: 'o200', name: '实际对象200', api: 'o200' }], total: 201 };
} };
const imports = {
  vue: { ...vue, watch() {} },
  'vue-router': { useRoute: () => route, useRouter: () => ({ replace: async ({ query }) => { route.query = query; }, push: async () => {} }) },
  'ant-design-vue': { message: { info() {}, error: text => errors.push(text) } },
  '@/api/http': { apiClient: () => api, currentApiContext: () => context, isApiError: e => typeof e?.status === 'number' },
  '@/api/action': {
    actionContextKey: (ctx = context) => JSON.stringify(ctx), crWriteOn: () => true, crErrText: e => e.message,
    crStart: async (target, options) => { starts.push({ target, options }); return { join: 'DB-CR' }; },
    fetchCR: async () => ({ id: 'DB-CR', lockVersion: lock }), fetchCRItems: async () => [], crChanges,
    saveCRItems: async (id, body) => {
      puts.push({ id, body: JSON.parse(JSON.stringify(body)) });
      if (fail) throw Object.assign(new Error('保存失败'), { status: 503 });
      assert.equal(body.lockVersion, lock);
      return { id, lockVersion: ++lock, changes: body.rows.length };
    },
  },
  '@/composables/useReadonly': { useReadonly: () => ({ readonly, reason: vue.ref('历史版本') }) },
  '@/stores/confirm': { useConfirmStore: () => ({ ask: async () => true }) },
};
const props = { kind: 'action', options: { title: '实际变更', desc: '真实说明' }, rowForms: {
  params: { title: '参数', blank: () => ({}), fields: () => [{ k: 'api', label: 'API', required: true }] },
} };
const source = readFileSync(new URL('../src/pages/ontology/parts/NewActionEventForm.vue', import.meta.url), 'utf8')
  .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  + '\nexport { load, save, mergeRows, form, rows, objects, canSave, saved, crId };';
const exports = {};
runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
  exports, defineProps: () => props, defineModel: () => open,
  require: name => { assert.ok(name in imports, `Missing dependency: ${name}`); return imports[name]; },
});
const plain = value => JSON.parse(JSON.stringify(value));
await exports.load();
assert.equal(exports.objects.value.length, 201, 'load the complete real object directory');
assert.equal(exports.form.owner, 'DB-USER');
Object.assign(exports.form, { name: '真实动作', api: 'new_action', targetKind: 'o200' });
exports.rows.params.push({ api: 'amount' });
assert.equal(exports.canSave.value, true);
await exports.save();
assert.equal(starts[0].target.isNew, true);
assert.equal(starts[0].target.id, 'new_action');
assert.equal(starts[0].options.desc, '真实说明');
assert.deepEqual(puts.map(p => p.body.lockVersion), [7, 8]);
assert.equal(puts[0].body.section, 'def');
assert.equal(puts[0].body.rows.find(row => row.key === 'owner').after, 'DB-USER');
assert.equal(puts[1].body.section, 'params');
assert.equal(puts[1].body.rows[0].key, 'amount');
assert.equal(exports.saved.value, true);
exports.form.name = '保留本地修改';
assert.equal(exports.saved.value, false, 'editing after a save must restore the unsaved warning');
fail = true; await exports.save();
assert.equal(exports.form.name, '保留本地修改');
assert.equal(exports.crId.value, 'DB-CR', 'retry must retain its database CR rather than create another one');
assert.match(errors.at(-1), /保存失败/);
const writes = puts.length;
readonly.value = true; await exports.save(); assert.equal(puts.length, writes);
readonly.value = false; context.demoAs = 'OTHER'; await exports.save(); assert.equal(puts.length, writes);
const row = (key, after) => ({ key, op: 'add', after });
assert.deepEqual(plain(exports.mergeRows([row('a', 1)], [row('a', 2)], [row('a', 1), row('b', 3)])), [row('a', 2), row('b', 3)]);
assert.throws(() => exports.mergeRows([row('a', 1)], [row('a', 2)], [row('a', 3)]), /已在别处修改/);
assert.deepEqual(plain(exports.mergeRows([row('a', { x: 1, y: 2 })], [row('a', { y: 2, x: 1 })], [row('a', { x: 3, y: 2 })])), [row('a', { x: 3, y: 2 })]);
console.log('New action/event form checks passed (real directories, saved rows, locks, merge, failed writes, readonly/context isolation).');
