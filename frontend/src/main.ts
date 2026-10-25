import { createApp } from 'vue';
import { createPinia } from 'pinia';
import Antd from 'ant-design-vue';
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

dayjs.locale('zh-cn');

const app = createApp(App);
app.use(createPinia()).use(router).use(Antd);
// 常用图标全局注册；其余图标在组件里按需 import
Object.entries({ SearchOutlined, BellOutlined, QuestionCircleOutlined, CloseOutlined, PlusOutlined, MinusOutlined, ExpandOutlined, DownOutlined })
  .forEach(([n, c]) => app.component(n, c));
app.mount('#app');
