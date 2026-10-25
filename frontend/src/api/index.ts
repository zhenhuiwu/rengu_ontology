/**
 * 前后端联调入口（docs/backend/integration.md）。
 * - setupApi(pinia)：注册请求上下文（演示身份 → X-Demo-As，历史版本 → ?ver=，预览 → ?previewAs=）；
 * - bootstrapApi(pinia)：real 模式下启动前先拉需要同步的数据（发布记录 / 当前版本）。
 * mock 模式下 main.ts 不调用 bootstrapApi，启动流程和现在一样。
 */
import type { Pinia } from 'pinia';
import { useAppStore, PERSONAS, type PersonaKey } from '@/stores/app';
import { usePreviewStore } from '@/stores/preview';
import { setApiContext } from './http';
import { anyReal, realModules, API_MODE } from './config';
import { loadReleases } from './releases';

export * from './config';
export * from './http';

/** 前端两个演示身份对应的 sys_user.code（api-index §1.4；U010006 李娜是 [推断]） */
export const DEMO_USER_CODES: Record<PersonaKey, string> = { admin: 'U010001', viewer: 'U010006' };

export function setupApi(pinia: Pinia) {
  setApiContext(() => {
    const app = useAppStore(pinia);
    const pv = usePreviewStore(pinia);
    return { demoAs: DEMO_USER_CODES[app.persona], ver: app.viewVersion, previewAs: pv.session?.user.id ?? null };
  });
}

export async function bootstrapApi(pinia: Pinia): Promise<void> {
  if (!anyReal()) return;
  console.info(`[api] VITE_API_MODE=${API_MODE}，走真接口的模块：${realModules().join(', ')}`);
  // 启动请求发生在路由守卫之前：先按 ?as= 定好演示身份（和守卫的处理一样），保证 X-Demo-As 是本次的身份
  const as = new URLSearchParams(window.location.search).get('as');
  const app = useAppStore(pinia);
  if (as && as in PERSONAS && as !== app.persona) app.setPersona(as as PersonaKey);
  try {
    const cur = await loadReleases();
    if (cur) {
      app.ontologyVersion = cur.current;
      app.ontologyPublishedAt = cur.publishedAt;
    }
  } catch (e) {
    // 接口失败不挡启动：发布记录保持 mock 数据，控制台报错（联调时看这里）
    console.error('[api] 发布记录加载失败，仍显示 mock 数据', e);
  }
}
