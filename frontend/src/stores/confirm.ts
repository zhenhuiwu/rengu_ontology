import { defineStore } from 'pinia';

export interface ConfirmField { label: string; value: string }
export interface ConfirmRequest {
  title: string;                 // 动作名，例如“补发出库单”
  target: string;                // 作用对象，例如“订单 SO2026…”
  fields?: ConfirmField[];       // 参数
  impact?: string;               // 影响说明
  irreversible?: boolean;        // 不可逆写回：需要勾选确认
  okText?: string;
  tag?: string;                  // 标题旁的状态标签，例如“待确认”
}

/** 所有动作共用一张确认卡：const ok = await useConfirmStore().ask({...}) */
export const useConfirmStore = defineStore('confirm', {
  state: () => ({ open: false, req: null as ConfirmRequest | null, resolver: null as null | ((ok: boolean) => void) }),
  actions: {
    ask(req: ConfirmRequest) {
      this.req = req;
      this.open = true;
      return new Promise<boolean>((resolve) => { this.resolver = resolve; });
    },
    settle(ok: boolean) {
      this.open = false;
      this.resolver?.(ok);
      this.resolver = null;
    },
  },
});
