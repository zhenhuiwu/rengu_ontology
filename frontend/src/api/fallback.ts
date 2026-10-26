/** 兼容原页面调用：真实模式的读取错误由调用方展示，禁止切换到演示数据。 */
import { reactive } from 'vue';

export const backend = reactive({ down: false });
export function fallBackIfDown(_e: unknown): boolean { return false; }
