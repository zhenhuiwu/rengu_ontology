import { useRouter } from 'vue-router';
import { useAppStore } from '@/stores/app';

/** 切换查看的本体版本：选历史版本写入 ?ver=，返回当前版本时去掉 ?ver=。 */
export function useVersion() {
  const app = useAppStore();
  const router = useRouter();
  function view(ver: string) {
    if (ver === app.ontologyVersion) return exit();
    app.setViewVersion(ver);
    const r = router.currentRoute.value;
    router.push({ query: { ...r.query, ver } });
  }
  function exit() {
    app.setViewVersion(null); // 先清 store，守卫才不会把 ?ver= 加回去
    const { ver: _v, ...rest } = router.currentRoute.value.query;
    router.push({ query: rest });
  }
  return { view, exit };
}
