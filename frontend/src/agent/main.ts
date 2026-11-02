import { createApp } from 'vue';
import Antd from 'ant-design-vue';
import 'ant-design-vue/dist/reset.css';
import '../styles/tokens.css';
import AgentApp from './AgentApp.vue';
import { agentRouter } from './router';

createApp(AgentApp).use(agentRouter).use(Antd).mount('#app');
