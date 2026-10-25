/**
 * 后台没起来时的回退（动作类型 / 事件类型 / 外部系统页共用，同 StaticGate 的做法）：
 * real 模式下读接口遇到 isBackendDown（http.ts）→ 记 backend.down，整页改用 mock 渲染，只提示一次；
 * 其余接口错误照旧由页面提示。写操作不回退（不假装成功），照旧报错。
 * 【推断】回退在本次会话内保持（后台起来后刷新页面即恢复真接口），避免真 / 假数据混在一页里。
 */
import { reactive } from 'vue';
import { message } from 'ant-design-vue';
import { isBackendDown } from './http';

export const backend = reactive({ down: false });
let warned = false;

/** 是后台没起来：标记回退、提示一次并返回 true；否则返回 false（调用方照旧处理错误） */
export function fallBackIfDown(e: unknown): boolean {
  if (!isBackendDown(e)) return false;
  backend.down = true;
  if (!warned) { warned = true; message.warning('后台未启动，暂时显示演示数据'); }
  return true;
}
