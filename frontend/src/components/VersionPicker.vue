<script setup lang="ts">
/** 顶栏版本号：点开选历史发布版本（只读查看，URL ?ver=）。 */
import { ref } from 'vue';
import { useAppStore } from '@/stores/app';
import { useVersion } from '@/composables/useVersion';
import { RELEASES } from '@/api/releases';

const app = useAppStore();
const { view } = useVersion();
const visible = ref(false);
const shown = () => app.viewVersion ?? app.ontologyVersion;
function pick(v: string) { visible.value = false; view(v); }
</script>

<template>
  <a-popover v-model:open="visible" trigger="click" placement="bottomRight">
    <template #content>
      <div class="vp">
        <div class="vp-head fw-600">发布记录</div>
        <ul class="vp-list">
          <li v-for="r in RELEASES" :key="r.ver" class="vp-item" :class="{ on: r.ver === shown() }" :title="r.note" @click="pick(r.ver)">
            <div class="vp-row">
              <span class="num fw-600">{{ r.ver }}</span>
              <a-tag v-if="r.ver === app.ontologyVersion" class="vp-tag">当前</a-tag>
              <span class="vp-sp" />
              <span class="vp-by">{{ r.by }}</span>
              <span class="num vp-at">{{ r.publishedAt }}</span>
            </div>
          </li>
        </ul>
      </div>
    </template>
    <a class="vp-trigger t-14 num" :class="app.viewMode === 'history' ? 'c-warning' : 'c-2'">
      本体 {{ shown() }}<span v-if="app.viewMode === 'history'">（历史）</span> <DownOutlined class="vp-caret" />
    </a>
  </a-popover>
</template>

<style scoped>
.vp { width: 360px; }
.vp-head { padding-bottom: 8px; border-bottom: var(--border); }
.vp-list { list-style: none; margin: 0; padding: 0; }
.vp-item { padding: 8px; border-bottom: var(--border); cursor: pointer; }
.vp-item:hover { background: var(--g3); }
.vp-item.on { background: var(--brand-bg); }
.vp-row { display: flex; align-items: center; gap: 8px; }
.vp-tag { margin: 0; }
.vp-sp { flex: 1; }
.vp-by { flex: none; white-space: nowrap; }
.vp-at { flex: none; white-space: nowrap; }
.vp-caret { color: var(--g8); }
</style>
