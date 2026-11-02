// Offline template contracts; no UI, credentials, network, or store mutation.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parse, compileScript } from '@vue/compiler-sfc';
for (const path of ['components/ConfirmActionModal.vue', 'pages/admin/ApiKeys.vue']) {
  const text = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  const { descriptor, errors } = parse(text);
  assert.equal(errors.length, 0);
  compileScript(descriptor, { id: path, inlineTemplate: true });
  if (path.includes('ConfirmActionModal')) {
    assert.ok(descriptor.template.content.includes(':z-index="1100"'));
    assert.ok(descriptor.template.content.includes('store.req.dangerLabel ??'));
    assert.ok(descriptor.template.content.includes('store.req?.irreversible && !ack'));
    assert.ok(descriptor.template.content.includes('v-if="store.req.irreversible" v-model:checked="ack"'));
    assert.ok(descriptor.template.content.includes('@ok="store.settle(true)"'));
  } else {
    assert.ok(text.includes("irreversible: true, dangerLabel: '旧密钥将立即失效，轮换提交后不能撤销'"));
    assert.ok(text.includes("irreversible: true, dangerLabel: '密钥将立即吊销且不能恢复'"));
  }
}
console.log('Confirmation layer and key impact labels passed (offline SFC only).');
