/**
 * API 密钥示例数据（PDE工程师）：46 个密钥，固定种子。
 * 密钥明文只在创建时显示一次；列表里只有前缀 + 后 4 位。
 * 状态：有效 / 即将过期（30 天内）/ 已过期 / 已吊销。今天按 2026-10-25。
 * 每个密钥都绑定身份 + 数据范围：绑定角色（按角色的行级范围查询），或绑定服务账号 + 显式数据范围。
 * 用密钥执行动作时，发起人记为“API 密钥 bi-sync-prod（服务账号 svc_bi_sync）”。
 */
import { seeded } from './format';
import { ADMIN_USERS } from './admin';

export const KEY_SCOPES = ['问数查询', '对象读取', '指标读取', '动作执行', '数据导出', '元数据读取', 'Webhook 回调'] as const;
export type KeyScope = (typeof KEY_SCOPES)[number];
export type KeyStatus = '有效' | '即将过期' | '已过期' | '已吊销';
/** 身份绑定：kind = 角色 时 role 为角色名（mock/roles.ts ROLES），scope 为该角色的行级范围摘要；kind = 服务账号 时 account + scope 必填 */
export interface KeyBinding { kind: '角色' | '服务账号'; role?: string; account?: string; scope: string }
export const SERVICE_ACCOUNTS = ['svc_bi_sync', 'svc_report', 'svc_crm_sync', 'svc_wms_cb', 'svc_oms_push', 'svc_bot', 'svc_export', 'svc_ml_feature', 'svc_erp', 'svc_ops_monitor'];
export const SA_SCOPES = ['大区 = 华东', '大区 = 华南', '大区 = 华东、华南', '大区 = 华东、华中', '门店 = 上海徐汇店', '品类 = 生鲜、乳品烘焙', '仅元数据（不读业务数据）'];
export const bindingText = (b: KeyBinding) => (b.kind === '角色' ? `以 ${b.role} 角色查询 · ${b.scope}` : `以 服务账号 ${b.account} 查询 · ${b.scope}`);
export const keyActor = (k: { name: string; binding: KeyBinding }) => `API 密钥 ${k.name}（${k.binding.kind === '角色' ? `角色 ${k.binding.role}` : `服务账号 ${k.binding.account}`}）`;
const R = (role: string, scope: string): KeyBinding => ({ kind: '角色', role, scope });
const SA = (account: string, scope: string): KeyBinding => ({ kind: '服务账号', account, scope });
/** 按调用方系统固定绑定；-v2 / -bak 沿用原系统的绑定 */
const BINDINGS: Record<string, KeyBinding> = {
  'bi-sync-prod': SA('svc_bi_sync', '大区 = 华东'), 'fanruan-report': R('数据分析师', '大区 = 华东、华南'), 'crm-member-sync': SA('svc_crm_sync', '大区 = 华东、华中'),
  'wms-callback': SA('svc_wms_cb', '仅元数据（不读业务数据）'), 'oms-status-push': SA('svc_oms_push', '大区 = 华东'), 'dingtalk-bot': R('业务查看', '大区 = 华东'),
  'pos-daily-export': SA('svc_export', '大区 = 华东、华南'), 'tableau-extract': R('数据分析师', '大区 = 华东、华南'), 'ml-feature-pull': SA('svc_ml_feature', '大区 = 华东'),
  'erp-replenish': R('动作审批人', '大区 = 华东'), 'mini-program-qa': R('业务查看', '大区 = 华东'), 'store-dashboard': R('门店店长', '门店 = 上海徐汇店'),
  'finance-recon': R('数据分析师', '大区 = 华东、华南'), 'supplier-portal': SA('svc_erp', '品类 = 生鲜、乳品烘焙'), 'price-engine': SA('svc_erp', '大区 = 华东'),
  'coupon-service': R('会员运营', '大区 = 华东、华中'), 'risk-audit': R('数据分析师', '大区 = 华东、华南'), 'data-lake-ingest': SA('svc_bi_sync', '大区 = 华东、华南'),
  'sms-webhook': SA('svc_bot', '仅元数据（不读业务数据）'), 'wecom-agent': SA('svc_bot', '大区 = 华东'), 'ops-monitor': SA('svc_ops_monitor', '仅元数据（不读业务数据）'),
  'uat-smoke-test': R('业务查看', '大区 = 华东'),
};
export interface ApiKey {
  binding: KeyBinding;
  id: string; name: string; prefix: string; last4: string; env: '生产' | '测试';
  scopes: KeyScope[]; creator: string; createdAt: string; lastUsed: string | null; lastIp: string | null;
  expiresAt: string | null; status: KeyStatus; calls7d: number; ipAllow: string[]; revokedAt?: string; revokedBy?: string;
}
export interface KeyCall { time: string; api: string; ip: string; code: number; ms: number }

const pad = (n: number, w = 2) => String(n).padStart(w, '0');
const NAMES = ['bi-sync-prod', 'fanruan-report', 'crm-member-sync', 'wms-callback', 'oms-status-push', 'dingtalk-bot', 'pos-daily-export', 'tableau-extract', 'ml-feature-pull', 'erp-replenish', 'mini-program-qa', 'store-dashboard', 'finance-recon', 'supplier-portal', 'price-engine', 'coupon-service', 'risk-audit', 'data-lake-ingest', 'sms-webhook', 'wecom-agent', 'ops-monitor', 'uat-smoke-test'];
const SCOPE_SETS: KeyScope[][] = [['问数查询', '指标读取'], ['对象读取', '指标读取'], ['对象读取', '动作执行'], ['Webhook 回调'], ['数据导出', '对象读取'], ['问数查询'], ['元数据读取'], ['对象读取', '动作执行', 'Webhook 回调']];
const DAY = 86400000;
const TODAY = new Date(2026, 9, 25).getTime();
const d2s = (t: number) => { const d = new Date(t); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const t2s = (t: number) => { const d = new Date(t); return `${d2s(t)} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const HEX = '0123456789abcdef';

export const API_KEYS: ApiKey[] = (() => {
  const r = seeded(31337);
  const hex = (n: number) => Array.from({ length: n }, () => HEX[Math.floor(r() * 16)]).join('');
  const creators = ADMIN_USERS.filter((u) => u.role === '系统管理员' || u.role === '本体建模师').slice(0, 6).map((u) => u.name);
  const out: ApiKey[] = [];
  for (let i = 0; i < 46; i++) {
    const env = i % 5 === 4 ? '测试' : '生产';
    const created = TODAY - Math.floor(10 + r() * 420) * DAY;
    const x = r();
    // 有效期：90 / 180 / 365 天或永不过期
    const life = x < 0.15 ? null : [90, 180, 365][Math.floor(r() * 3)];
    let expires = life ? created + life * DAY : null;
    if (i % 9 === 3 && expires) expires = TODAY + Math.floor(3 + r() * 25) * DAY; // 一批即将过期
    const revoked = i % 11 === 7;
    const status: KeyStatus = revoked ? '已吊销' : expires != null && expires < TODAY ? '已过期' : expires != null && expires < TODAY + 30 * DAY ? '即将过期' : '有效';
    const active = status === '有效' || status === '即将过期';
    const used = active && r() > 0.08 ? TODAY + 17 * 3600000 - Math.floor(r() * 3 * DAY) : status === '有效' ? null : created + Math.floor(r() * 60) * DAY;
    const name = i < NAMES.length ? NAMES[i] : `${NAMES[i % NAMES.length]}-${i < 30 ? 'v2' : 'bak'}`;
    out.push({
      id: `AK-${pad(1001 + i, 4)}`, name, env, binding: { ...BINDINGS[NAMES[i % NAMES.length]] },
      prefix: `sk_${env === '生产' ? 'live' : 'test'}_${hex(4)}`, last4: hex(4),
      scopes: SCOPE_SETS[i % SCOPE_SETS.length], creator: creators[i % creators.length] ?? '王敏',
      createdAt: t2s(created + 9 * 3600000 + Math.floor(r() * 9 * 3600000)),
      lastUsed: used ? t2s(used) : null, lastIp: used ? `10.${12 + (i % 4)}.${Math.floor(r() * 250)}.${Math.floor(r() * 250)}` : null,
      expiresAt: expires ? d2s(expires) : null, status,
      calls7d: active && used ? Math.floor(r() * 180000) : 0,
      ipAllow: i % 3 === 0 ? [] : [`10.${12 + (i % 4)}.0.0/16`],
      ...(revoked ? { revokedAt: t2s(TODAY - Math.floor(2 + r() * 40) * DAY + 15 * 3600000), revokedBy: '王敏' } : {}),
    });
  }
  // 最近创建的排前面
  return out.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
})();

const API_PATHS: Record<KeyScope, string[]> = {
  问数查询: ['POST /api/v1/qa/ask', 'GET /api/v1/qa/sessions/{id}'],
  对象读取: ['GET /api/v1/objects/SalesOrder', 'GET /api/v1/objects/Member/{id}', 'GET /api/v1/objects/Store'],
  指标读取: ['GET /api/v1/metrics/gmv', 'GET /api/v1/metrics/gross_margin'],
  动作执行: ['POST /api/v1/actions/correctOrderStatus', 'POST /api/v1/actions/requestReplenishment'],
  数据导出: ['POST /api/v1/exports', 'GET /api/v1/exports/{id}/file'],
  元数据读取: ['GET /api/v1/ontology/object-types', 'GET /api/v1/ontology/link-types'],
  'Webhook 回调': ['POST /api/v1/webhooks/ext/callback'],
};

/** 最近调用（最多 60 条，倒序） */
export function keyCalls(k: ApiKey): KeyCall[] {
  if (!k.lastUsed) return [];
  const r = seeded(Number(k.id.slice(3)));
  const apis = k.scopes.flatMap((s) => API_PATHS[s]);
  const n = 24 + Math.floor(r() * 36);
  let t = new Date(k.lastUsed.replace(' ', 'T') + ':00').getTime();
  const out: KeyCall[] = [];
  for (let i = 0; i < n; i++) {
    const x = r();
    out.push({ time: `${t2s(t)}:${pad(Math.floor(r() * 60))}`, api: apis[Math.floor(r() * apis.length)], ip: k.lastIp ?? '', code: x < 0.9 ? 200 : x < 0.95 ? 429 : 403, ms: 18 + Math.floor(r() * 400) });
    t -= Math.floor(30 + r() * 1800) * 1000;
  }
  return out;
}

/** 演示用：生成一个新密钥明文（只显示一次） */
export function newSecret(env: '生产' | '测试', seed: number) {
  const r = seeded(seed);
  const s = Array.from({ length: 40 }, () => 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(r() * 56)]).join('');
  return `sk_${env === '生产' ? 'live' : 'test'}_${s}`;
}
