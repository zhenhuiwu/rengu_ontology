/**
 * 问数（工作台 / 问数调优）后台没起来时的回退：复用 src/api/fallback.ts（同 c988cf5 动作类型页、2c6eae4 动态本体）。
 * 开关 qa 为真接口时，读接口遇到 isBackendDown → fallBackIfDown 记 backend.down 并只提示一次，页面改用 mock 渲染；
 * 其余接口错误照旧由页面提示。写操作不回退（不假装成功），仍按开关走真接口并照旧报错。
 */
import { computed } from 'vue';
import { message } from 'ant-design-vue';
import { backend, fallBackIfDown } from '@/api/fallback';
import { isApiError, isBackendDown } from '@/api/http';
import { qaReal } from '@/api/qa';

/** 渲染 / 读接口用：开关 qa 为真且后台没被判定为未启动 */
export const qaLive = computed(() => qaReal() && !backend.down);
export const qaFallBackIfDown = fallBackIfDown;

/**
 * 写操作用（赞踩、我的页面增删改刷新、卡片 / 刷新订阅 / 共享、固定到页面、导出）：只看开关，不看 backend.down。
 * 开关为真时一律调真接口，失败只提示错误：不改演示数据、不假装成功，也不走 fallBackIfDown（不把整页切到演示数据）。
 * 开关为假（纯演示模式）时才走 mock 逻辑。
 */
export const qaWriteReal = computed(() => qaReal());
/** 写失败提示；后台没起来时明确说没保存 */
export function qaWriteFail(what: string, e: unknown) {
  const why = isBackendDown(e) ? '后台未启动，操作没有保存' : isApiError(e) ? `${e.message || e.code}（${e.code}）` : String(e);
  message.error(`${what}失败：${why}`);
}
