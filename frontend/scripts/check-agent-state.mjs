// Actual public-chat state with controlled promises; no HTTP, model, browser or database.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as vue from 'vue';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';

function moduleAt(path, deps, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  runInNewContext(js, { exports, AbortController, Error, URL, ...globals, require: key => { assert.ok(key in deps, key); return deps[key]; } });
  return exports;
}
const network = [];
let reply = { ok: true, json: async () => ({ workspaces: [], selected: null }) };
const apiModule = moduleAt('../src/agent/api.ts', {}, { window: { location: { href: 'https://example.test/agent.html' } }, fetch: async (url, options) => {
  network.push({ url, options }); return reply;
} });
await apiModule.publicApi.config('', new AbortController().signal);
await apiModule.publicApi.create('4', new AbortController().signal);
assert.equal(network[0].url, 'https://example.test/api/agent/v1/config');
assert.equal(apiModule.publicApiUrl('/config', 'https://example.test/rengu/agent.html?ontology=4'), 'https://example.test/rengu/api/agent/v1/config', 'subpath stays on the same origin and deployment directory');
assert.equal(network[0].options.credentials, 'omit');
assert.equal(network[0].options.cache, 'no-store');
assert.deepEqual(Object.keys(network[0].options.headers), [], 'no platform identity headers');
assert.equal(JSON.parse(network[1].options.body).ontologyId, 4, 'strict backend integer contract');
reply = { ok: false, status: 404, json: async () => ({ detail: 'Not Found' }) };
await assert.rejects(apiModule.publicApi.config('', new AbortController().signal), /公开问数服务尚未就绪/);
reply = { ok: false, status: 404, json: async () => ({ code: 'NOT_FOUND', message: '本体尚未公开' }) };
await assert.rejects(apiModule.publicApi.config('4', new AbortController().signal), /本体尚未公开/, 'business refusal remains intact');

const stateModule = moduleAt('../src/agent/useAgentChat.ts', { vue, './api': apiModule });
const workspaces = [{ id: '4', label: '公开本体四' }, { id: '5', label: '公开本体五' }];
const configCalls = [], creates = [], asks = [], restores = [];
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
const session = (ontologyId, marker) => ({ ontologyId, id: `P_${marker.repeat(43)}`, title: '新会话', turns: [] });
const api = {
  config: async id => { configCalls.push(id); return { workspaces, selected: workspaces.find(w => w.id === id) ?? null }; },
  create: (id, signal) => { const task = { id, signal, ...deferred() }; creates.push(task); return task.promise; },
  ask: (ontologyId, id, question, signal) => { const task = { ontologyId, id, question, signal, ...deferred() }; asks.push(task); return task.promise; },
  session: async (id, code) => { restores.push({ id, code }); return session(id, id === '5' ? 'b' : 'a'); },
};
const saved = new Map();
const storage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key) };
const chat = stateModule.createAgentChat(api, storage);
await chat.load(undefined);
assert.equal(configCalls.at(-1), '', 'missing ontology loads choices without selecting a default');
assert.equal(chat.ontologyId.value, '');
await chat.load(['4', '5']);
assert.match(chat.error.value, /本体编号无效/);
assert.equal(configCalls.length, 1, 'ambiguous ontology is rejected locally');
await chat.load('99');
assert.equal(chat.config.value, null, 'unpublished ontology never mounts a conversation');
await chat.load('4');
chat.draft.value = '第一次提问';
const cancelledCreate = chat.send();
const oldCreate = creates.at(-1);
chat.newSession();
assert.equal(oldCreate.signal.aborted, true);
oldCreate.resolve(session('4', 'a')); await cancelledCreate;
assert.equal(asks.length, 0, 'late creation cannot start an ask after new conversation');
assert.equal(saved.size, 0);

const firstAsk = chat.send('本体四的问题');
creates.at(-1).resolve(session('4', 'a')); await flush();
const late = asks.at(-1);
await chat.load('5');
assert.equal(late.signal.aborted, true);
late.resolve({ turnId: 'old', turn: { id: 'old', kind: 'answer', question: '旧问题', answer: { card: 'table', text: '旧本体回答' } } });
await firstAsk;
assert.equal(chat.ontologyId.value, '5'); assert.equal(chat.turns.value.length, 0);

const nextAsk = chat.send('本体五的问题');
creates.at(-1).resolve(session('5', 'b')); await flush();
asks.at(-1).resolve({ turnId: 'new', turn: { id: 'new', kind: 'answer', question: '本体五的问题', answer: { card: 'clarify', text: '请明确范围' } } });
await nextAsk;
assert.equal(chat.turns.value[0].id, 'new');
assert.deepEqual(JSON.parse([...saved.values()][0]), { ontologyId: '5', sessionId: session('5', 'b').id }, 'only public scope and random session pointer are stored');
const restored = stateModule.createAgentChat(api, storage);
await restored.load('5');
assert.equal(restored.sessionId.value, session('5', 'b').id); assert.equal(restores.at(-1).id, '5');

const failed = restored.send('模型不可用时的问题');
asks.at(-1).reject(new Error('模型服务暂不可用')); await failed;
assert.equal(restored.turns.value.length, 0, 'failure produces no mock answer');
assert.equal(restored.draft.value, '模型不可用时的问题');
assert.match(restored.error.value, /模型服务暂不可用/);
chat.dispose(); restored.dispose();

// Execute the real answer SFC computeds with controlled turns; no chart renderer or model.
const answerFile = readFileSync(new URL('../src/agent/AgentAnswer.vue', import.meta.url), 'utf8');
const { descriptor, errors } = parse(answerFile);
assert.equal(errors.length, 0);
compileScript(descriptor, { id: 'agent-answer' });
assert.equal(compileTemplate({ source: descriptor.template.content, filename: 'AgentAnswer.vue', id: 'agent-answer', compilerOptions: { expressionPlugins: ['typescript'] } }).errors.length, 0);
function answerState(question, answer) {
  const exports = {};
  const code = ts.transpileModule(`${descriptor.scriptSetup.content}\nObject.assign(exports, { view, showTable, chartData });`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  runInNewContext(code, { exports, defineProps: () => ({ turn: { id: 'controlled', kind: 'answer', question, answer }, busy: false, navy: false }), defineEmits: () => () => {}, require: key => key === 'vue' ? vue : key === 'echarts/core' ? { use() {} } : {} });
  return exports;
}
const statsTable = { id: 'controlled', title: '按状态统计', columns: [{ key: 'status', title: '状态' }, { key: 'count', title: '数量' }], items: [{ status: '搜救中', count: 3 }], total: 1 };
for (const [question, expected] of [['不限定时间，按状态统计数量，用柱状图显示', 'bar'], ['用柱形图显示', 'bar'], ['用条形图显示', 'bar'], ['用折线图显示', 'line'], ['按状态统计数量', 'table'], [undefined, 'table']]) {
  const state = answerState(question, { card: 'table', text: '真实结果', table: statsTable });
  assert.equal(state.view.value, expected);
  assert.ok(state.chartData.value);
  assert.equal(state.showTable.value, true);
}
const singleton = value => ({ id: 'controlled', title: '查询结果', columns: [{ key: 'value', title: '结果' }], items: [{ value }], total: 1 });
assert.equal(answerState('有哪些对象类型', { text: '事件、任务', table: singleton('事件、任务') }).showTable.value, false);
assert.equal(answerState('数量', { text: '数量为 3', table: singleton(3) }).showTable.value, true);
assert.equal(answerState('数量', { text: '3', table: singleton(3) }).showTable.value, true, 'numeric result is not identical to string prose');
assert.equal(answerState('对象', { text: '事件', table: singleton('事件 ') }).showTable.value, true, 'only exact string equality hides a table');
assert.equal(answerState('对象', { text: '事件', table: { ...singleton('事件'), total: 2 } }).showTable.value, true, 'one visible row of a larger result is not a duplicate singleton');
assert.equal(answerState('对象', { text: '事件', table: { ...singleton('事件'), items: [{ value: '事件' }, { value: '任务' }], total: 2 } }).showTable.value, true);
console.log('Public agent state passed: scope/auth/stale state, exact requested chart defaults and duplicate singleton presentation (controlled dependencies only).');
