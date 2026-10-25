/** 外部系统（动作写回用）示例数据：系统配置 + 最近调用记录。固定种子，每次一样。 */
import { seeded } from './format';
import { ORDERS } from './orders';

export type CallResult = '成功' | '失败' | '结果未知';
export interface ExtSystem {
  id: string; name: string; vendor: string; category: string; endpoint: string; auth: string; authDetail: string;
  idempotent: boolean; idemKey: string; timeoutMs: number;
  /** 外部明确返回失败时的最多重试次数；超时 / 无响应一律不重试（结果未知），不支持幂等的系统为 0 */
  maxRetry: number; retry: string; rateLimit: string; owner: string;
  status: '正常' | '降级' | '停用'; actions: string[]; env: string;
}
export interface ExtCall {
  id: string; sysId: string; time: string; action: string; target: string; method: string; path: string;
  idemKey: string | null; ms: number; http: number | null; result: CallResult; attempt: number; actor: string; msg: string;
}

export const EXT_SYSTEMS: ExtSystem[] = [
  { id: 'EXT-CRM', name: '会员 CRM', vendor: '销售易', category: 'CRM', endpoint: 'https://api.xiaoshouyi.com/rest/data/v2.0', auth: 'OAuth 2.0（客户端凭证）', authDetail: 'client_id = xl-ontology-prod · 令牌 2 小时自动续期', idempotent: true, idemKey: 'X-Idempotency-Key', timeoutMs: 5000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '50 次/秒', owner: '刘婷', status: '正常', actions: ['发放会员券', '调整会员等级', '标记流失风险', '同步会员标签'], env: '生产' },
  { id: 'EXT-ERP', name: '用友 ERP', vendor: '用友 NC Cloud', category: 'ERP', endpoint: 'https://nc.xianlin.local/nccloud/api/pu', auth: 'AppID + AppSecret（OpenAPI）', authDetail: 'AppID = ontology-prod · 令牌 1 小时自动续期', idempotent: true, idemKey: 'srcBillNo（来源单号）', timeoutMs: 10000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '10 次/秒', owner: '孙浩然', status: '正常', actions: ['补货申请', '生成采购订单'], env: '生产' },
  { id: 'EXT-OMS', name: '订单 OMS', vendor: '自研 OMS', category: 'OMS', endpoint: 'https://oms.xianlin.local/api/v2', auth: 'mTLS + 服务令牌', authDetail: '客户端证书 ontology-svc（2027-03 到期）', idempotent: true, idemKey: 'X-Request-Id', timeoutMs: 5000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '100 次/秒', owner: '陈立军', status: '正常', actions: ['订单状态修正', '取消订单'], env: '生产' },
  { id: 'EXT-WMS', name: '仓储 WMS', vendor: '富勒 FLUX', category: 'WMS', endpoint: 'https://wms.xianlin.local/flux/api/v3', auth: 'API Key + HMAC 签名', authDetail: 'AppKey = ONT-0071 · 签名 HMAC-SHA256（时间戳 + 请求体）', idempotent: true, idemKey: 'requestNo', timeoutMs: 8000, maxRetry: 2, retry: '外部明确失败时最多 2 次（3s/6s）；超时不重试，标记结果未知', rateLimit: '20 次/秒', owner: '孙浩然', status: '正常', actions: ['补发出库单', '取消出库', '调整安全库存'], env: '生产' },
  { id: 'EXT-SMS', name: '短信平台', vendor: '阿里云短信', category: 'SMS', endpoint: 'https://dysmsapi.aliyuncs.com', auth: 'AccessKey（RAM 子账号）', authDetail: 'AccessKeyId = LTAI5t••••••••Qm2 · 仅 SendSms 权限', idempotent: false, idemKey: '-', timeoutMs: 3000, maxRetry: 0, retry: '不重试（不支持幂等）：外部明确失败转人工；超时标记结果未知，人工对账', rateLimit: '同一手机号 1 条/分钟', owner: '刘婷', status: '正常', actions: ['发送到店提醒', '发送补货到货通知'], env: '生产' },
  { id: 'EXT-SF', name: '顺丰速运', vendor: '顺丰开放平台', category: '物流', endpoint: 'https://sfapi.sf-express.com/std/service', auth: 'OAuth 2.0 + 数字签名', authDetail: 'partnerID = XLSX_ONT · checkWord 已加密保存', idempotent: true, idemKey: 'orderId（客户订单号）', timeoutMs: 6000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '30 次/秒', owner: '孙浩然', status: '降级', actions: ['下单寄件', '取消寄件', '查询路由'], env: '生产' },
  { id: 'EXT-WX', name: '企业微信', vendor: '腾讯', category: '通知', endpoint: 'https://qyapi.weixin.qq.com/cgi-bin', auth: 'CorpID + Secret', authDetail: 'AgentId = 1000017 · access_token 缓存 2 小时', idempotent: false, idemKey: '-', timeoutMs: 3000, maxRetry: 0, retry: '不重试（不支持幂等）：外部明确失败转人工；超时标记结果未知，人工对账', rateLimit: '每应用 2 万次/分钟', owner: '王敏', status: '正常', actions: ['通知店长', '推送告警'], env: '生产' },
  { id: 'EXT-MT', name: '美团闪购', vendor: '美团', category: '渠道', endpoint: 'https://waimaiopen.meituan.com/api/v1', auth: 'AppID + 签名', authDetail: 'app_id = 8812 · MD5 签名', idempotent: true, idemKey: 'app_poi_code + 请求序号', timeoutMs: 5000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '每门店 10 次/秒', owner: '陈立军', status: '正常', actions: ['商品下架', '调整门店营业时间'], env: '生产' },
  { id: 'EXT-OA', name: '泛微 OA', vendor: '泛微 e-cology', category: '审批', endpoint: 'https://oa.xianlin.local/api/workflow', auth: 'Token（服务账号）', authDetail: '服务账号 svc_ontology · 令牌 30 天轮换', idempotent: true, idemKey: 'requestId', timeoutMs: 10000, maxRetry: 2, retry: '外部明确失败时最多 2 次（3s/6s）；超时不重试，标记结果未知', rateLimit: '10 次/秒', owner: '何静', status: '正常', actions: ['调价审批'], env: '生产' },
  { id: 'EXT-SMS-UAT', name: '短信平台（测试）', vendor: '阿里云短信', category: 'SMS', endpoint: 'https://dysmsapi.aliyuncs.com', auth: 'AccessKey（RAM 子账号）', authDetail: 'AccessKeyId = LTAI5t••••••••Xa9 · 仅测试签名', idempotent: false, idemKey: '-', timeoutMs: 3000, maxRetry: 0, retry: '不重试（不支持幂等）：外部明确失败转人工；超时标记结果未知，人工对账', rateLimit: '-', owner: '刘婷', status: '停用', actions: [], env: '测试' },
];
export const extSystemById = (id?: string | null) => EXT_SYSTEMS.find((s) => s.id === id);

const pad = (n: number) => String(n).padStart(2, '0');
const at = (secsAgo: number) => {
  const t = new Date(Date.UTC(2026, 9, 25, 16, 30, 0) - secsAgo * 1000);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}:${pad(t.getUTCSeconds())}`;
};
const ACTORS = ['王敏', '陈立军', '刘婷', '孙浩然', '何静', '规则 R-021（自动）', '规则 R-014（自动）'];
const PATHS: Record<string, [string, string]> = {
  'EXT-CRM': ['POST', '/coupon/grant'], 'EXT-WMS': ['POST', '/outbound/create'], 'EXT-SMS': ['POST', '/?Action=SendSms'],
  'EXT-SF': ['POST', '/EXP_RECE_CREATE_ORDER'], 'EXT-WX': ['POST', '/message/send'], 'EXT-MT': ['POST', '/retail/sellStatus'], 'EXT-OA': ['POST', '/request/create'],
  'EXT-ERP': ['POST', '/praybill/save'], 'EXT-OMS': ['PUT', '/orders/status'],
};

const cache = new Map<string, ExtCall[]>();
/** 某个系统最近的调用（倒序），每个系统 60～140 条 */
export function extCalls(sys: ExtSystem): ExtCall[] {
  const hit = cache.get(sys.id);
  if (hit) return hit;
  if (!sys.actions.length) { cache.set(sys.id, []); return []; }
  const r = seeded(sys.id.split('').reduce((a, c) => a * 31 + c.charCodeAt(0), 7) >>> 0);
  const n = 60 + Math.round(r() * 80);
  const degraded = sys.status === '降级';
  let t = 40;
  const out: ExtCall[] = Array.from({ length: n }, (_, k) => {
    t += Math.round(60 + r() * 900);
    const x = r();
    const result: CallResult = degraded && k < 6 ? (k % 3 === 0 ? '结果未知' : '失败')
      : x < 0.06 ? '失败' : x < 0.09 ? '结果未知' : '成功';
    const o = ORDERS[Math.floor(r() * ORDERS.length)];
    const action = sys.actions[Math.floor(r() * sys.actions.length)];
    const [method, path] = PATHS[sys.id] ?? ['POST', '/'];
    return {
      id: `${sys.id.slice(4)}-${String(n - k).padStart(5, '0')}`, sysId: sys.id, time: at(t), action,
      target: sys.category === 'CRM' || sys.category === 'SMS' ? `会员 M${String(100000 + Math.floor(r() * 899999))}` : sys.category === 'ERP' ? `补货单 PR2026${String(100000 + Math.floor(r() * 899999))}` : `订单 ${o.id}`,
      method, path, idemKey: sys.idempotent ? `act-${(1e9 + Math.floor(r() * 9e9)).toString(36)}` : null,
      ms: result === '结果未知' ? sys.timeoutMs : Math.round(80 + r() * (result === '失败' ? 1500 : 900)),
      http: result === '结果未知' ? null : result === '失败' ? [400, 401, 429, 500, 502][Math.floor(r() * 5)] : 200,
      // 只有外部明确失败才重试；超时 / 无响应 = 结果未知，不自动重试；不支持幂等的系统从不重试
      result, attempt: result === '成功' ? 1 + (sys.maxRetry && r() < 0.1 ? 1 : 0) : result === '失败' ? 1 + sys.maxRetry : 1,
      actor: ACTORS[Math.floor(r() * ACTORS.length)],
      msg: result === '成功' ? 'OK' : result === '结果未知'
        ? `请求超时（${sys.timeoutMs / 1000} 秒），未自动重试，对方可能已处理；${sys.idempotent ? '对账后可用同一幂等键安全重试' : '不支持幂等，需人工对账后再处理'}`
        : sys.maxRetry ? `对方明确返回失败，已重试 ${sys.maxRetry} 次仍失败` : '对方明确返回失败；不支持幂等，未重试，已转人工',
    };
  });
  cache.set(sys.id, out);
  return out;
}
