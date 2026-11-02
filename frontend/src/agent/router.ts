import { createRouter, createWebHistory } from 'vue-router';
import AgentChat from './AgentChat.vue';

// This entry never imports the platform router or its login/default-ontology guards.
export const agentRouter = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'public-agent', component: AgentChat }],
});
