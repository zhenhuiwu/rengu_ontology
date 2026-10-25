<script setup lang="ts">
/** 输入框。下方“范围”“口径”是整个会话共用的（v-model 由工作台持有），中途修改会在对话里插入系统提示 */
import { ref } from 'vue';
import { SendOutlined, PaperClipOutlined } from '@ant-design/icons-vue';
import { QA_SCOPE_OPTIONS, QA_CALIBER_OPTIONS } from '@/mock';
const scope = defineModel<string>('scope', { required: true });
const caliber = defineModel<string>('caliber', { required: true });
const text = ref('');
const followUps = ['按门店等级再拆一下', '和去年同期比', '为什么安徽降得最多'];
</script>

<template>
  <div class="qa-composer">
    <div class="qa-cp-input">
      <a-textarea v-model:value="text" :bordered="false" :auto-size="{ minRows: 1, maxRows: 1 }" placeholder="继续追问，Enter 发送，Shift + Enter 换行" />
      <div class="qa-cp-bar">
        <a-space :size="8">
          <a-tooltip title="本会话共用，修改后从下一问开始生效">
            <a-select v-model:value="scope" size="small" class="qa-cp-sel" :options="QA_SCOPE_OPTIONS" />
          </a-tooltip>
          <a-tooltip title="本会话共用，修改后从下一问开始生效">
            <a-select v-model:value="caliber" size="small" class="qa-cp-sel" :options="QA_CALIBER_OPTIONS" />
          </a-tooltip>
          <a-button size="small" type="text"><template #icon><PaperClipOutlined /></template>引用对象</a-button>
          <a-divider type="vertical" />
          <a-tag v-for="f in followUps" :key="f" class="qa-cp-chip">{{ f }}</a-tag>
        </a-space>
        <a-button type="primary"><template #icon><SendOutlined /></template>发送</a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qa-composer { flex: none; padding: 10px 20px 12px; background: var(--g1); border-top: var(--border); }
.qa-cp-input { border: 1px solid var(--g6); border-radius: var(--radius); padding: 8px 12px; }
.qa-cp-input :deep(textarea) { padding: 0; }
.qa-cp-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.qa-cp-sel { width: 150px; }
.qa-cp-chip { cursor: pointer; background: var(--g1); margin-inline-end: 0; }
</style>
