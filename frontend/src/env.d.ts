/// <reference types="vite/client" />
interface ImportMetaEnv {
  /** mock（默认）| real，见 src/api/config.ts */
  readonly VITE_API_MODE?: 'mock' | 'real';
  /** 逗号分隔的模块名（ontology,qa,data,admin,biz），按模块切真接口 */
  readonly VITE_API_REAL_MODULES?: string;
  /** 仅开发服务器用：/api 代理到的后台地址，默认 http://127.0.0.1:8000 */
  readonly VITE_API_PROXY_TARGET?: string;
  /** 动态：规则/生命周期草稿的提交和放弃走静态变更单接口，'1' 开启，默认关闭 */
  readonly VITE_DYNAMIC_CR_WRITE?: string;
  /** Palantir：行动/事件类型草稿走静态变更单接口，'1' 开启，默认关闭 */
  readonly VITE_PALANTIR_CR_WRITE?: string;
}
interface ImportMeta { readonly env: ImportMetaEnv }
declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<object, object, unknown>;
  export default component;
}
