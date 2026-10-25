import { computed, inject, type InjectionKey } from 'vue';
import { useRoute, useRouter, type LocationQuery } from 'vue-router';

/**
 * 详情抽屉状态写在 URL 查询参数里，最多两层：
 *   ?drawer=<kind>:<id>                     一层，例如 ?drawer=metric:gross_margin
 *   ?drawer=<kind>:<id>&drawer2=<kind>:<id>  两层，第二层叠在第一层之上
 * 刷新、后退、复制链接都能还原；打开抽屉不改变当前路由，左侧导航选中状态不变。
 *
 * open(kind, id) 会自动判断层级（向后兼容，页面代码不用改）：
 *   - 在页面里调用（不在抽屉内）→ 设第一层，并清掉第二层；
 *   - 在第一层抽屉里调用（如抽屉内的 ObjRef）→ 打开第二层；
 *   - 在第二层抽屉里调用 → 替换第二层（最多两层）。
 * 也可以显式指定：open(kind, id, { level: 1 | 2 })。
 */
export interface DrawerRef { kind: string; id: string }
export type DrawerLevel = 1 | 2;

/** DetailDrawer 在每层内容外 provide 当前层级，useDrawer 据此决定 open 打开哪一层 */
export const DRAWER_LEVEL: InjectionKey<DrawerLevel> = Symbol('drawer-level');

const parse = (raw: LocationQuery[string] | undefined): DrawerRef | null => {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (!v) return null;
  const i = v.indexOf(':');
  if (i < 0) return null;
  return { kind: v.slice(0, i), id: v.slice(i + 1) };
};

export function useDrawer() {
  const route = useRoute();
  const router = useRouter();
  const hostLevel = inject(DRAWER_LEVEL, null);

  /** 第一层（兼容旧 API：current 一直表示第一层） */
  const current = computed(() => parse(route.query.drawer));
  /** 第二层；没有第一层时忽略 */
  const current2 = computed(() => (current.value ? parse(route.query.drawer2) : null));
  /** 当前最上面那层 */
  const top = computed(() => current2.value ?? current.value);
  const depth = computed<0 | 1 | 2>(() => (current2.value ? 2 : current.value ? 1 : 0));

  const without = (...keys: string[]) => {
    const q = { ...route.query };
    keys.forEach((k) => delete q[k]);
    return q;
  };

  /** 回到 target：如果上一条历史就是 target（通常是刚才 push 过来的），用浏览器后退，避免历史里堆积；否则 replace */
  const goBackTo = (query: LocationQuery) => {
    const target = router.resolve({ path: route.path, query, hash: route.hash }).fullPath;
    const back = (window.history.state as { back?: string } | null)?.back;
    if (back === target) router.back();
    else router.replace({ query });
  };

  const open = (kind: string, id: string, opts?: { level?: DrawerLevel }) => {
    const level: DrawerLevel = opts?.level ?? (hostLevel ? 2 : 1);
    const val = `${kind}:${id}`;
    if (level === 2 && current.value) {
      if (route.query.drawer2 === val) return;
      return router.push({ query: { ...without('drawer2'), drawer2: val } });
    }
    if (route.query.drawer === val && !route.query.drawer2) return;
    return router.push({ query: { ...without('drawer', 'drawer2'), drawer: val } });
  };

  /** 第二层“返回”：回到第一层 */
  const back = () => goBackTo(without('drawer2'));
  /** 关闭全部抽屉 */
  const close = () => goBackTo(without('drawer', 'drawer2'));

  return { current, current2, top, depth, open, back, close };
}
