import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/composables/useAdaptivePageSize.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  .replace(/(['"])vue\1/, JSON.stringify(import.meta.resolve('vue')));
const { pageSizeForHeight } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

// Allocated panel heights after app chrome; reserve includes real tools and footer.
assert.equal(pageSizeForHeight(760, 30, 220), 18); // 1920 × 1080 sidebar
assert.equal(pageSizeForHeight(1120, 30, 220), 30); // 2560 × 1440 sidebar
assert.equal(pageSizeForHeight(480, 30, 220), 8); // Smaller window
assert.equal(pageSizeForHeight(620, 48, 56), 11); // Ordinary table
assert.equal(pageSizeForHeight(100, 30, 220), 5);
assert.equal(pageSizeForHeight(5000, 30), 50);
assert.equal(pageSizeForHeight(0, 30), 10);
assert.equal(pageSizeForHeight(900, 0), 10);
console.log('Adaptive page capacity: OK');
