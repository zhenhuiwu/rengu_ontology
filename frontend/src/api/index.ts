/**
 * 前后端联调入口（docs/backend/integration.md）。
 * - setupApi(pinia)：注册请求上下文（演示身份 → X-Demo-As，历史版本 → ?ver=，预览 → ?previewAs=）；
 * - bootstrapApi(pinia)：real 模式下启动前先拉需要同步的数据（发布记录 / 当前版本）。
 * mock 模式下 main.ts 不调用 bootstrapApi，启动流程和现在一样。
 */
import type { Pinia } from 'pinia';
import { useAppStore, PERSONAS, type PersonaKey } from '@/stores/app';
import { usePreviewStore } from '@/stores/preview';
import { isApiError, isBackendDown, setApiContext } from './http';
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

/**
 * real 模式启动数据。不会 reject：失败的项回退 mock，返回要提示给用户的文字（main.ts 挂载后用 message.warning 显示）。
 */
export async function bootstrapApi(pinia: Pinia): Promise<string[]> {
  const warnings: string[] = [];
  if (!anyReal()) return warnings;
  console.info(`[api] VITE_API_MODE=${API_MODE}，走真接口的模块：${realModules().join(', ')}`);
  // 启动请求发生在路由守卫之前：先按 ?as= 定好演示身份（和守卫的处理一样），保证 X-Demo-As 是本次的身份
  const as = new URLSearchParams(window.location.search).get('as');
  const app = useAppStore(pinia);
  if (as && as in PERSONAS && as !== app.persona) app.setPersona(as as PersonaKey);
  try {
    const r = await loadReleases();
    if (r.kind === 'ok') {
      app.ontologyVersion = r.current;
      app.ontologyPublishedAt = r.publishedAt;
    } else if (r.kind === 'empty') {
      console.warn('[api] 发布记录接口返回空列表，仍显示 mock 数据');
      warnings.push('发布记录为空，暂时显示演示数据');
    }
  } catch (e) {
    // 接口失败不挡启动：发布记录保持 mock 数据，控制台报错（联调时看这里）
    console.error('[api] 发布记录加载失败，仍显示 mock 数据', e);
    warnings.push(isBackendDown(e)
      ? '后台未启动，发布记录暂时显示演示数据'
      : `发布记录接口加载失败，暂时显示演示数据（${isApiError(e) ? e.code : '未知错误'}）`);
  }
  return warnings;
}
