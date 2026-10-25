import { defineStore } from 'pinia';
import { usePreviewStore } from './preview';

export interface ConfirmField { label: string; value: string }
export interface ConfirmRequest {
  title: string;                 // 动作名，例如“补发出库单”
  target: string;                // 作用对象，例如“订单 SO2026…”
  fields?: ConfirmField[];       // 参数
  impact?: string;               // 影响说明
  irreversible?: boolean;        // 不可逆写回：需要勾选确认
  blocked?: string;              // 有阻断时填原因：确定按钮锁住，卡片里显示原因
  okText?: string;
  tag?: string;                  // 标题旁的状态标签，例如“待确认”
  danger?: boolean;              // 确认按钮用危险色（停用、吊销、删除）
  picker?: ConfirmPicker;        // 附带一个选择项，例如停用用户时选择待办转交人；确认后读 store.pickValue
}
export interface ConfirmPicker {
  label: string;
  placeholder?: string;
  options: { value: string; label: string }[];
  required?: boolean;            // 必选：没选时确认按钮不可点
}

/** 所有动作共用一张确认卡：const ok = await useConfirmStore().ask({...})；带 picker 时，确认后读 pickValue */
export const useConfirmStore = defineStore('confirm', {
  state: () => ({ open: false, req: null as ConfirmRequest | null, pickValue: undefined as string | undefined, resolver: null as null | ((ok: boolean) => void) }),
  actions: {
    ask(req: ConfirmRequest) {
      // “以此用户预览”期间只读：所有写操作的确认卡自动带阻断原因
      const pv = usePreviewStore();
      if (pv.active && !req.blocked) req = { ...req, blocked: `正在以 ${pv.session!.user.name} 的权限预览，只读，不能执行。退出预览后再操作。` };
      this.req = req;
      this.pickValue = undefined;
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
