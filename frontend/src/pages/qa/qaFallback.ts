/**
 * 问数（工作台 / 问数调优）后台没起来时的回退：复用 src/api/fallback.ts（同 c988cf5 动作类型页、2c6eae4 动态本体）。
 * 开关 qa 为真接口时，读接口遇到 isBackendDown → fallBackIfDown 记 backend.down 并只提示一次，页面改用 mock 渲染；
 * 其余接口错误照旧由页面提示。写操作不回退（不假装成功），仍按开关走真接口并照旧报错。
 */
import { computed } from 'vue';
import { backend, fallBackIfDown } from '@/api/fallback';
import { qaReal } from '@/api/qa';

/** 渲染 / 读接口用：开关 qa 为真且后台没被判定为未启动 */
export const qaLive = computed(() => qaReal() && !backend.down);
export const qaFallBackIfDown = fallBackIfDown;
