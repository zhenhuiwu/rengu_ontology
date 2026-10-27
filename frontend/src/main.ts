import { createApp } from 'vue';
import { createPinia } from 'pinia';
import Antd, { message } from 'ant-design-vue';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import {
  SearchOutlined, BellOutlined, QuestionCircleOutlined, CloseOutlined, PlusOutlined, MinusOutlined,
  ExpandOutlined, DownOutlined,
} from '@ant-design/icons-vue';
import 'ant-design-vue/dist/reset.css';
import './styles/tokens.css';
import App from './App.vue';
import { router } from './router';
import { anyReal, bootstrapApi, setupApi } from './api';

dayjs.locale('zh-cn');

const app = createApp(App);
const pinia = createPinia();
setupApi(pinia);
function start() {
  app.use(pinia).use(router).use(Antd);
  // 全局提示：同时只显示 1 条；顶栏高度以 tokens.css 的 --topbar-h（48px）为准，提示放在顶栏下沿再往下 8px（= 56px），不挡顶栏搜索框
  // 有模块走真接口时不限条数，启动时的多条接口提示不会互相吞掉；全 mock 时和原来一样只显示 1 条
  message.config({ top: 'calc(var(--topbar-h) + 8px)', ...(anyReal() ? {} : { maxCount: 1 }) });
  // 常用图标全局注册；其余图标在组件里按需 import
  Object.entries({ SearchOutlined, BellOutlined, QuestionCircleOutlined, CloseOutlined, PlusOutlined, MinusOutlined, ExpandOutlined, DownOutlined })
    .forEach(([n, c]) => app.component(n, c));
  app.mount('#app');
}
// 启动先确定身份，工作空间与发布记录由路由守卫验证；真实失败保留错误态。
if (anyReal()) {
  bootstrapApi(pinia)
    .catch((e: unknown) => { console.error('[api] 启动数据加载异常', e); return ['接口启动失败，请刷新后重试']; })
    .then((warnings) => { start(); warnings.forEach((t) => message.warning(t)); });
}
else start();
