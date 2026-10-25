#!/usr/bin/env node
/**
 * 把 src/mock/* 的每个导出原样导成 JSON，给后台种子数据用（npm run export-mock）。
 *   只读 mock，不改任何值；mock 文件本身不动。
 *
 * 输出：${OUT_DIR}/<模块>/<导出名>.json，加 ${OUT_DIR}/manifest.json（源文件、导出名、行数、跳过/失败项）。
 *   OUT_DIR 必须显式给出（相对前端根目录或绝对路径），没有默认值，不给直接报错退出。
 *   例：OUT_DIR=/tmp/export-mock-test npm run export-mock
 *       OUT_DIR=../backend/seeds/json npm run export-mock
 * 派生数据：函数按参数算出的种子（hitsOf、runsOf、viewsOf……）按 scripts/export-mock.derived.mjs 的调用清单，
 *   在同一进程、常量导出之后逐个调用，写到 ${OUT_DIR}/<模块>/derived/<函数>.json（[{ args, result }]），
 *   每次调用的参数、是否推断（guessed）、行数记在 manifest.derived。
 *
 * 时间：mock 按北京时间写（new Date(2026, 9, 25, …)、getHours() 等本地时间函数），
 *   所以脚本先把 TZ 固定为 Asia/Shanghai，再加载 mock，算出的值与浏览器里一致；
 *   Date 对象一律写成带 +08:00 的 ISO 字符串（如 2026-10-25T15:42:18.000+08:00），不按导出时间平移。
 *   字符串形式的时间（'2026-10-25 15:42'）原样保留。
 * 函数：顶层导出是函数的跳过，列在 manifest.skipped；数据里嵌着的函数字段（如 NAV 的 component）
 *   也去掉，路径列在该导出的 droppedFunctionFields 里。
 * 确定性：mock 都用固定种子（format.ts 的 seeded），两次导出结果逐字节一致；键顺序按源对象原顺序。
 * 重名：同一模块内两个文件导出同名时，后出现的写成 <模块>/<文件名>.<导出名>.json（manifest.file 为准）。
 * 加载：用项目自带的 Vite（ssrLoadModule）加载 TS，'@/' 别名与 vite.config.ts 一致，不新增依赖。
 *   某个文件加载失败只记进 manifest.failed，不中断其他文件。
 *
 * 模块映射（对应后台 backend/app/modules/<模块>）：
 *   common  门店/商品/订单等共用业务数据：stores, products, orders, format, paginate
 *   static  静态本体：metrics, links, dimensions, knowledge, objectProps, objectViews, objectHistory,
 *           changes, releases, quality, functions
 *   dynamic 动态本体：dynamic（告警/规则）, actions（动作中心）, rules, lifecycle, scenarios
 *   action  动作类型/事件类型：actionEvent
 *   qa      智能问数：qa, qa-pages, qa-tuning, objects-qa
 *   system  系统管理：admin（用户/审计）, apikeys, roles, settings, datasources, extsys（外部系统）
 *   index.ts 只是汇总再导出，不单独导。新增 mock 文件没写进映射时归到 unmapped，并在终端提示。
 */
process.env.TZ = 'Asia/Shanghai';

import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MOCK_DIR = path.join(ROOT, 'src/mock');
if (!process.env.OUT_DIR) {
  console.error('✗ 未设置 OUT_DIR。请显式指定输出目录，例如：OUT_DIR=/tmp/export-mock-test npm run export-mock');
  process.exit(1);
}
const OUT_DIR = path.resolve(ROOT, process.env.OUT_DIR);

const MODULES = {
  common: ['stores', 'products', 'orders', 'format', 'paginate'],
  static: ['metrics', 'metricCatalog', 'links', 'dimensions', 'knowledge', 'objectProps', 'objectViews', 'objectHistory',
    'changes', 'releases', 'quality', 'functions'],
  dynamic: ['dynamic', 'actions', 'rules', 'lifecycle', 'scenarios'],
  action: ['actionEvent'],
  qa: ['qa', 'qa-pages', 'qa-tuning', 'objects-qa'],
  system: ['admin', 'apikeys', 'roles', 'settings', 'datasources', 'extsys'],
};
const EXCLUDE = new Set(['index']);
const moduleOf = (base) => Object.keys(MODULES).find((m) => MODULES[m].includes(base)) || 'unmapped';

const pad = (n, w = 2) => String(n).padStart(w, '0');
/** Date → 北京时间 ISO（+08:00），与运行环境时区无关。 */
function isoBeijing(d) {
  if (Number.isNaN(d.getTime())) return null;
  const t = new Date(d.getTime() + 8 * 3600000);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}T${pad(t.getUTCHours())}:`
    + `${pad(t.getUTCMinutes())}:${pad(t.getUTCSeconds())}.${pad(t.getUTCMilliseconds(), 3)}+08:00`;
}

/** 转成可 JSON 的纯数据；记录被去掉的函数字段和特殊类型。 */
function toPlain(value, p, ctx, seen) {
  if (value === null) return null;
  const t = typeof value;
  if (t === 'function') { ctx.dropped.push(p || '<root>'); return undefined; }
  if (t === 'bigint') { ctx.notes.add('bigint→string'); return value.toString(); }
  if (t === 'number') { if (!Number.isFinite(value)) { ctx.notes.add('非有限数→null'); return null; } return value; }
  if (t !== 'object') return t === 'undefined' || t === 'symbol' ? undefined : value;
  if (value instanceof Date) { ctx.dates++; return isoBeijing(value); }
  if (seen.has(value)) throw new Error(`循环引用：${p}`);
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((v, i) => { const r = toPlain(v, `${p}[${i}]`, ctx, seen); return r === undefined ? null : r; });
    }
    if (value instanceof Map) {
      ctx.notes.add('Map→object');
      const o = {};
      for (const [k, v] of value) { const r = toPlain(v, `${p}.${String(k)}`, ctx, seen); if (r !== undefined) o[String(k)] = r; }
      return o;
    }
    if (value instanceof Set) {
      ctx.notes.add('Set→array');
      return [...value].map((v, i) => { const r = toPlain(v, `${p}[${i}]`, ctx, seen); return r === undefined ? null : r; });
    }
    if (value instanceof RegExp) { ctx.notes.add('RegExp→string'); return String(value); }
    const o = {};
    for (const k of Object.keys(value)) { const r = toPlain(value[k], p ? `${p}.${k}` : k, ctx, seen); if (r !== undefined) o[k] = r; }
    return o;
  } finally { seen.delete(value); }
}

const rowsOf = (v) => (Array.isArray(v) ? v.length : v && typeof v === 'object' ? Object.keys(v).length : 1);
const shapeOf = (v) => (Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v);

const server = await createServer({
  root: ROOT, configFile: path.join(ROOT, 'vite.config.ts'), logLevel: 'error',
  server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
});

const manifest = { generatedBy: 'scripts/export-mock.mjs', timezone: 'Asia/Shanghai (+08:00)', modules: MODULES, files: [], exports: [], skipped: [], failed: [], derived: [] };
const files = fs.readdirSync(MOCK_DIR).filter((f) => f.endsWith('.ts') && !f.endsWith('.d.ts')).sort();
const written = [];
try {
  // 只清掉本脚本生成的东西（各模块目录 + manifest），OUT_DIR 里其他文件不碰
  for (const m of [...Object.keys(MODULES), 'unmapped']) fs.rmSync(path.join(OUT_DIR, m), { recursive: true, force: true });
  fs.rmSync(path.join(OUT_DIR, 'manifest.json'), { force: true });
  for (const f of files) {
    const base = f.replace(/\.ts$/, '');
    if (EXCLUDE.has(base)) { manifest.files.push({ source: `src/mock/${f}`, module: null, note: '汇总再导出，不单独导' }); continue; }
    const mod = moduleOf(base);
    if (mod === 'unmapped') console.warn(`! src/mock/${f} 不在映射里，归到 unmapped/`);
    let ns;
    try { ns = await server.ssrLoadModule(`/src/mock/${f}`); } catch (e) {
      manifest.failed.push({ source: `src/mock/${f}`, export: '*', error: String(e?.message || e).split('\n')[0] });
      manifest.files.push({ source: `src/mock/${f}`, module: mod, loaded: false });
      continue;
    }
    const names = Object.keys(ns).filter((n) => n !== 'default' || ns.default !== undefined).sort();
    manifest.files.push({ source: `src/mock/${f}`, module: mod, loaded: true, exports: names.length });
    for (const name of names) {
      const entry = { source: `src/mock/${f}`, module: mod, export: name };
      try {
        const v = ns[name];
        if (typeof v === 'function') { manifest.skipped.push({ ...entry, reason: 'function' }); continue; }
        const ctx = { dropped: [], notes: new Set(), dates: 0 };
        const plain = toPlain(v, '', ctx, new WeakSet());
        // 同一模块里两个文件导出同名（如 NEXT_VERSION）时，后者加文件名前缀，不覆盖
        let rel = `${mod}/${name}.json`;
        if (written.includes(rel)) rel = `${mod}/${base}.${name}.json`;
        const out = path.join(OUT_DIR, rel);
        fs.mkdirSync(path.dirname(out), { recursive: true });
        fs.writeFileSync(out, JSON.stringify(plain, null, 2) + '\n');
        written.push(rel);
        manifest.exports.push({
          ...entry, file: rel, shape: shapeOf(plain), rows: rowsOf(plain),
          ...(ctx.dates ? { dateObjects: ctx.dates } : {}),
          ...(ctx.dropped.length ? { droppedFunctionFields: ctx.dropped } : {}),
          ...(ctx.notes.size ? { notes: [...ctx.notes].sort() } : {}),
        });
      } catch (e) {
        manifest.failed.push({ ...entry, error: String(e?.message || e).split('\n')[0] });
      }
    }
  }
  await exportDerived();
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
} finally {
  await server.close();
}

/** 派生调用：按清单逐个调用，写 <模块>/derived/<函数>.json */
async function exportDerived() {
  let list;
  try {
    const { default: derived } = await import('./export-mock.derived.mjs');
    list = await derived((f) => server.ssrLoadModule(f));
  } catch (e) {
    manifest.failed.push({ source: 'scripts/export-mock.derived.mjs', export: '*', error: String(e?.message || e).split('\n')[0] });
    return;
  }
  for (const d of list) {
    const rel = `${d.module}/derived/${d.fn}.json`;
    const ctx = { dropped: [], notes: new Set(), dates: 0 };
    const rows = [], calls = [], errors = [];
    for (const c of d.calls) {
      try {
        const result = toPlain(c.run(), `${d.fn}(${calls.length})`, ctx, new WeakSet());
        const n = Array.isArray(result) ? result.length : result == null ? 0 : 1;
        rows.push({ args: c.args, result: result === undefined ? null : result });
        calls.push({ args: c.args, rows: n });
      } catch (e) {
        errors.push({ args: c.args, error: String(e?.message || e).split('\n')[0] });
      }
    }
    const out = path.join(OUT_DIR, rel);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, JSON.stringify(rows, null, 2) + '\n');
    manifest.derived.push({
      module: d.module, fn: d.fn, file: rel, spec: d.spec, guessed: d.guessed ?? false,
      calls: calls.length, rows: calls.reduce((s, c) => s + c.rows, 0),
      ...(ctx.dates ? { dateObjects: ctx.dates } : {}),
      ...(ctx.dropped.length ? { droppedFunctionFields: ctx.dropped.length } : {}),
      ...(ctx.notes.size ? { notes: [...ctx.notes].sort() } : {}),
      argSets: calls,
    });
    for (const e of errors) manifest.failed.push({ source: 'derived', module: d.module, export: d.fn, args: e.args, error: e.error });
  }
}

const byMod = {};
for (const e of manifest.exports) { byMod[e.module] ??= { exports: 0, rows: 0 }; byMod[e.module].exports++; byMod[e.module].rows += e.rows; }
console.log(`导出到 ${OUT_DIR}`);
for (const [m, s] of Object.entries(byMod)) console.log(`  ${m.padEnd(8)} ${String(s.exports).padStart(3)} 个导出  ${s.rows} 行`);
const dRows = manifest.derived.reduce((s, d) => s + d.rows, 0), dCalls = manifest.derived.reduce((s, d) => s + d.calls, 0);
console.log(`  派生 ${manifest.derived.length} 个函数  ${dCalls} 次调用  ${dRows} 行（推断参数 ${manifest.derived.filter((d) => d.guessed).length} 个）`);
console.log(`  跳过函数 ${manifest.skipped.length} 个，失败 ${manifest.failed.length} 个`);
for (const x of manifest.failed) console.log(`  ✗ ${x.source} ${x.export}: ${x.error}`);
process.exitCode = manifest.failed.length ? 2 : 0;
