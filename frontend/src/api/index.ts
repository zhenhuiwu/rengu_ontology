/**
 * 前后端联调入口（docs/backend/integration.md）。
 * setupApi(pinia)：注册当前会话、本体与版本上下文；身份由服务端 Cookie 确认。
 */
import type { Pinia } from 'pinia';
import { useAppStore } from '@/stores/app';
import { useAuthStore } from '@/stores/auth';
import { usePreviewStore } from '@/stores/preview';
import { setApiContext } from './http';

export * from './config';
export * from './http';

export function setupApi(pinia: Pinia) {
  setApiContext(() => {
    const app = useAppStore(pinia);
    const pv = usePreviewStore(pinia);
    return { ontologyId: app.ontologyId, demoAs: useAuthStore(pinia).user?.code ?? null, ver: app.viewVersion, previewAs: pv.session?.user.id ?? null };
  });
}
