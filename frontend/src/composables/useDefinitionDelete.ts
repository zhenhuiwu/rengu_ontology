import { computed, onScopeDispose, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute } from 'vue-router';
import { message } from 'ant-design-vue';
import { apiClient, currentApiContext, isApiError, type RequestOptions } from '@/api/http';
import { invalidateStaticAfterWrite } from '@/api/static';
import { useConfirmStore } from '@/stores/confirm';

const resources = {
  link: ['static', '/link-types', '关系类型'], metric: ['static', '/metrics', '指标'],
  knowledge: ['static', '/knowledge', '业务知识'], function: ['static', '/functions', '函数'],
  action: ['action', '/action-types', '动作类型'], event: ['action', '/event-types', '事件类型'],
  rule: ['dynamic', '/rules', '规则'], external: ['integration', '/ext-systems', '外部系统'],
} as const;
type Target = { id: string; name: string };
type Versions = { lockVersion: number; targetVersion: number; references?: string[] };

/** One confirmation/version/context boundary for persisted definitions, never local new forms. */
export function useDefinitionDelete(kind: keyof typeof resources, options: {
  target: () => Target | undefined; enabled: () => boolean; context: () => string;
  afterDelete: (id: string, currentContext: () => boolean) => Promise<void>;
}) {
  const [module, resource, label] = resources[kind], api = apiClient(module), confirm = useConfirmStore();
  const route = useRoute();
  const busy = ref(false), error = ref('');
  const allowed = computed(() => options.enabled() && !!options.target()?.id && !busy.value);
  let sequence = 0, navigation = 0, alive = true;
  // Invalidate when leaving starts, before async global guards commit the next route.
  onBeforeRouteLeave(() => { navigation++; sequence++; busy.value = false; });
  watch(() => JSON.stringify([options.context(), options.target()?.id]), () => { sequence++; busy.value = false; error.value = ''; }, { flush: 'sync' });
  onScopeDispose(() => { alive = false; sequence++; });
  async function run() {
    const target = options.target();
    if (!allowed.value || !target) return;
    const seq = ++sequence, pageNavigation = navigation, pagePath = route.path, context = options.context(), ctx = { ...currentApiContext() };
    const sameContext = () => alive && navigation === pageNavigation && route.path === pagePath && context === options.context();
    const current = () => sameContext() && seq === sequence && target.id === options.target()?.id && options.enabled();
    const pinned: RequestOptions = { readonlyParams: false, query: { ver: ctx.ver, previewAs: ctx.previewAs }, headers: { 'X-Ontology-Id': ctx.ontologyId ?? '' } };
    const path = `${resource}/${encodeURIComponent(target.id)}`;
    busy.value = true; error.value = '';
    let deleted = false;
    try {
      const versions = await api.get<Versions>(`${path}:delete-check`, pinned);
      if (!current()) return;
      const references = versions.references?.filter(value => typeof value === 'string' && value.trim()) ?? [];
      const ok = await confirm.ask({ title: `删除${label}`, target: target.name, okText: '删除', danger: true,
        fields: [{ label: '标识', value: target.id }, { label: '引用检查', value: references.length ? references.join('；') : '当前未发现引用；删除前由服务端再次校验' }],
        impact: '删除立即生效，不能撤销本次删除。历史快照与已有执行记录保留；不会重发或执行任何动作。',
        ...(references.length ? { blocked: '存在引用，当前不可删除' } : {}),
      });
      if (!ok || !current() || references.length) return;
      const result = await api.post<{ ver: string }>(`${path}:delete`, { lockVersion: versions.lockVersion, targetVersion: versions.targetVersion }, pinned);
      if (!current()) return;
      deleted = true; invalidateStaticAfterWrite(result.ver);
      message.success(`${label}已删除`);
      await options.afterDelete(target.id, sameContext);
    } catch (e) {
      if (deleted ? sameContext() : current()) {
        let detail = isApiError(e) ? `${e.message}（${e.code}）` : e instanceof Error ? e.message : String(e);
        const references = isApiError(e) ? (e.details as { references?: unknown } | null)?.references : undefined;
        if (Array.isArray(references)) {
          const names = references.map(reference => typeof reference === 'string' ? reference : reference && typeof reference === 'object' ? [reference.kind, reference.name, reference.id].filter(value => typeof value === 'string').join(' ') : '').filter(Boolean);
          if (names.length) detail += `：${names.join('；')}`;
        }
        error.value = deleted ? `已删除，刷新失败：${detail}` : detail;
        message.error(error.value);
      }
    } finally { if (alive && seq === sequence) busy.value = false; }
  }
  return { busy, error, allowed, run };
}
