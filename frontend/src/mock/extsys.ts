/** 外部系统（动作写回用）示例数据：系统配置 + 最近调用记录。固定种子，每次一样。 */
import { seeded } from './format';
import { ORDERS } from './orders';

export type CallResult = '成功' | '失败' | '结果未知';
export interface ExtSystem {
  id: string; name: string; vendor: string; category: string; endpoint: string; auth: string; authDetail: string;
  idempotent: boolean; idemKey: string; timeoutMs: number;
  /** 外部明确返回失败时的最多重试次数；超时 / 无响应一律不重试（结果未知），不支持幂等的系统为 0 */
  maxRetry: number; retry: string; rateLimit: string; owner: string;
  status: '正常' | '降级' | '停用'; env: string;
  /**
   * 不做动作写回的集成（短信、企业微信、顺丰、美团）：用途说明和调用记录里的操作名。
   * 做动作写回的系统不在这里写动作名：“关联动作”由页面按动作类型的 sysId（mock/actionEvent.ts）计算，避免两处维护。
   */
  usage?: { text: string; ops: string[]; actor?: string };
}
/**
 * 调用记录里的一种操作：动作写回取动作类型名、endpoint 和该动作的执行记录（samples，页面从 mock/actionEvent.ts 取好传入），
 * 其他集成取 usage.ops。对象、发起人都来自执行记录：对象类型 = 动作类型的目标对象；发起人是人，或“规则 R-012 · 确认人 周晓峰”（规则只生成动作，由确认人提交）。
 */
export interface ExtSample { obj: string; actor: string; runId: string; idemKey: string; unknown: boolean; /** 动作中心里该次调用的时间（结果未知的调用沿用） */ time?: string }
export interface ExtOp { name: string; method?: string; path?: string; samples?: ExtSample[] }
export interface ExtCall {
  id: string; sysId: string; time: string; action: string; target: string; method: string; path: string;
  idemKey: string | null; ms: number; http: number | null; result: CallResult; attempt: number; actor: string; msg: string;
  /** 对应的动作执行记录（ACT-…）；结果未知的写回调用到动作中心对账 */
  runId?: string;
}

export const EXT_SYSTEMS: ExtSystem[] = [
  { id: 'EXT-CRM', name: '会员 CRM', vendor: '销售易', category: 'CRM', endpoint: 'https://api.xiaoshouyi.com/rest/data/v2.0', auth: 'OAuth 2.0（客户端凭证）', authDetail: 'client_id = xl-ontology-prod · 令牌 2 小时自动续期', idempotent: true, idemKey: 'X-Idempotency-Key', timeoutMs: 5000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '50 次/秒', owner: '刘婷', status: '正常', env: '生产' },
  { id: 'EXT-ERP', name: '用友 ERP', vendor: '用友 NC Cloud', category: 'ERP', endpoint: 'https://nc.xianlin.local/nccloud/api/pu', auth: 'AppID + AppSecret（OpenAPI）', authDetail: 'AppID = ontology-prod · 令牌 1 小时自动续期', idempotent: true, idemKey: 'srcBillNo（来源单号）', timeoutMs: 10000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '10 次/秒', owner: '孙浩然', status: '正常', env: '生产' },
  { id: 'EXT-OMS', name: '订单 OMS', vendor: '自研 OMS', category: 'OMS', endpoint: 'https://oms.xianlin.local/api/v2', auth: 'mTLS + 服务令牌', authDetail: '客户端证书 ontology-svc（2027-03 到期）', idempotent: true, idemKey: 'X-Request-Id', timeoutMs: 5000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '100 次/秒', owner: '陈立军', status: '正常', env: '生产' },
  { id: 'EXT-WMS', name: '仓储 WMS', vendor: '富勒 FLUX', category: 'WMS', endpoint: 'https://wms.xianlin.local/flux/api/v3', auth: 'API Key + HMAC 签名', authDetail: 'AppKey = ONT-0071 · 签名 HMAC-SHA256（时间戳 + 请求体）', idempotent: true, idemKey: 'requestNo', timeoutMs: 8000, maxRetry: 2, retry: '外部明确失败时最多 2 次（3s/6s）；超时不重试，标记结果未知', rateLimit: '20 次/秒', owner: '孙浩然', status: '正常', env: '生产' },
  { id: 'EXT-SMS', name: '短信平台', vendor: '阿里云短信', category: 'SMS', endpoint: 'https://dysmsapi.aliyuncs.com', auth: 'AccessKey（RAM 子账号）', authDetail: 'AccessKeyId = LTAI5t••••••••Qm2 · 仅 SendSms 权限', idempotent: false, idemKey: '-', timeoutMs: 3000, maxRetry: 0, retry: '不重试（不支持幂等）：外部明确失败转人工；超时标记结果未知，人工对账', rateLimit: '同一手机号 1 条/分钟', owner: '刘婷', status: '正常', usage: { text: '通知渠道：短信（模型与系统配置 → 通知渠道），只发紧急通知，如动作结果未知、审批超时', ops: ['紧急通知短信'], actor: '通知服务（紧急通知）' }, env: '生产' },
  { id: 'EXT-SF', name: '顺丰速运', vendor: '顺丰开放平台', category: '物流', endpoint: 'https://sfapi.sf-express.com/std/service', auth: 'OAuth 2.0 + 数字签名', authDetail: 'partnerID = XLSX_ONT · checkWord 已加密保存', idempotent: true, idemKey: 'orderId（客户订单号）', timeoutMs: 6000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '30 次/秒', owner: '孙浩然', status: '降级', usage: { text: '物流轨迹查询（只读）：订单抽屉的物流节点、“出库超时”事件取签收时间', ops: ['查询路由'] }, env: '生产' },
  { id: 'EXT-WX', name: '企业微信', vendor: '腾讯', category: '通知', endpoint: 'https://qyapi.weixin.qq.com/cgi-bin', auth: 'CorpID + Secret', authDetail: 'AgentId = 1000017 · access_token 缓存 2 小时', idempotent: false, idemKey: '-', timeoutMs: 3000, maxRetry: 0, retry: '不重试（不支持幂等）：外部明确失败转人工；超时标记结果未知，人工对账', rateLimit: '每应用 2 万次/分钟', owner: '王敏', status: '正常', usage: { text: '通知渠道：企业微信应用消息（模型与系统配置 → 通知渠道），告警、审批和跟进任务提醒', ops: ['告警通知', '审批提醒', '跟进任务提醒'], actor: '通知服务' }, env: '生产' },
  { id: 'EXT-MT', name: '美团闪购', vendor: '美团', category: '渠道', endpoint: 'https://waimaiopen.meituan.com/api/v1', auth: 'AppID + 签名', authDetail: 'app_id = 8812 · MD5 签名', idempotent: true, idemKey: 'app_poi_code + 请求序号', timeoutMs: 5000, maxRetry: 3, retry: '外部明确失败时最多 3 次（1s/2s/4s）；超时不重试，标记结果未知', rateLimit: '每门店 10 次/秒', owner: '陈立军', status: '正常', usage: { text: '渠道订单接入（只读同步）：美团闪购订单同步为订单对象', ops: ['订单同步'], actor: '订单同步任务（每 5 分钟）' }, env: '生产' },
  { id: 'EXT-OA', name: '泛微 OA', vendor: '泛微 e-cology', category: '审批', endpoint: 'https://oa.xianlin.local/api/workflow', auth: 'Token（服务账号）', authDetail: '服务账号 svc_ontology · 令牌 30 天轮换', idempotent: true, idemKey: 'requestId', timeoutMs: 10000, maxRetry: 2, retry: '外部明确失败时最多 2 次（3s/6s）；超时不重试，标记结果未知', rateLimit: '10 次/秒', owner: '何静', status: '正常', env: '生产' },
  { id: 'EXT-SMS-UAT', name: '短信平台（测试）', vendor: '阿里云短信', category: 'SMS', endpoint: 'https://dysmsapi.aliyuncs.com', auth: 'AccessKey（RAM 子账号）', authDetail: 'AccessKeyId = LTAI5t••••••••Xa9 · 仅测试签名', idempotent: false, idemKey: '-', timeoutMs: 3000, maxRetry: 0, retry: '不重试（不支持幂等）：外部明确失败转人工；超时标记结果未知，人工对账', rateLimit: '-', owner: '刘婷', status: '停用', usage: { text: '测试环境短信，已停用', ops: [] }, env: '测试' },
];
export const extSystemById = (id?: string | null) => EXT_SYSTEMS.find((s) => s.id === id);

const pad = (n: number) => String(n).padStart(2, '0');
const at = (secsAgo: number) => {
  const t = new Date(Date.UTC(2026, 9, 25, 16, 30, 0) - secsAgo * 1000);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}:${pad(t.getUTCSeconds())}`;
};
/** 非动作集成（如物流轨迹查询）由人在页面上触发 */
const USERS = ['王敏', '陈立军', '刘婷', '孙浩然', '何静'];
const PATHS: Record<string, [string, string]> = {
  'EXT-CRM': ['POST', '/coupon/grant'], 'EXT-WMS': ['POST', '/outbound/create'], 'EXT-SMS': ['POST', '/?Action=SendSms'],
  'EXT-SF': ['POST', '/EXP_RECE_SEARCH_ROUTES'], 'EXT-WX': ['POST', '/message/send'], 'EXT-MT': ['GET', '/ecommerce/order/getOrderDetail'], 'EXT-OA': ['POST', '/request/create'],
  'EXT-ERP': ['POST', '/praybill/save'], 'EXT-OMS': ['PUT', '/orders/status'],
};

const cache = new Map<string, ExtCall[]>();
/** 某个系统最近的调用（倒序），每个系统 60～140 条；ops = 已发布的关联动作（页面按 sysId 算好、带执行记录样本传入）或 usage.ops */
export function extCalls(sys: ExtSystem, ops: ExtOp[]): ExtCall[] {
  const hit = cache.get(sys.id);
  if (hit) return hit;
  if (!ops.length) { cache.set(sys.id, []); return []; }
  const r = seeded(sys.id.split('').reduce((a, c) => a * 31 + c.charCodeAt(0), 7) >>> 0);
  const n = 60 + Math.round(r() * 80);
  const degraded = sys.status === '降级';
  let t = 40;
  // 动作写回：动作中心里每条“结果未知”的执行记录对应且只对应一条结果未知的调用（均匀插在调用列表里）；其余写回调用不会出现结果未知
  const pending = ops.flatMap((op) => (op.samples ?? []).filter((x) => x.unknown).map((sample) => ({ op, sample })));
  const slots = new Map(pending.map((p, i) => [Math.floor(((i + 0.5) * n) / pending.length), p] as const));
  const out: ExtCall[] = Array.from({ length: n }, (_, k) => {
    t += Math.round(60 + r() * 900);
    const x = r();
    const slot = slots.get(k);
    const op = slot?.op ?? ops[Math.floor(r() * ops.length)];
    let result: CallResult = slot ? '结果未知' : degraded && k < 6 ? (k % 3 === 0 ? '结果未知' : '失败')
      : x < 0.06 ? '失败' : x < 0.09 ? '结果未知' : '成功';
    let sample: ExtSample | undefined = slot?.sample;
    if (!sample && op.samples?.length) {
      if (result === '结果未知') result = '成功';
      const pool = op.samples.filter((s) => !s.unknown); sample = pool[Math.floor(r() * pool.length)] ?? op.samples[0];
    }
    const o = ORDERS[Math.floor(r() * ORDERS.length)];
    const pick = r();
    const [method, path] = op.method && op.path ? [op.method, op.path] : PATHS[sys.id] ?? ['POST', '/'];
    const idem = sys.idempotent ? (sample && result === '结果未知' ? sample.idemKey : `act-${(1e9 + Math.floor(r() * 9e9)).toString(36)}`) : null;
    return {
      id: '', sysId: sys.id, time: slot?.sample.time ?? at(t), action: op.name,
      target: sample ? sample.obj : sys.category === 'SMS' || sys.category === '通知' ? `员工 ${USERS[Math.floor(pick * USERS.length)]}` : `订单 ${o.id}`,
      method, path, idemKey: idem,
      ms: result === '结果未知' ? sys.timeoutMs : Math.round(80 + r() * (result === '失败' ? 1500 : 900)),
      http: result === '结果未知' ? null : result === '失败' ? [400, 401, 429, 500, 502][Math.floor(r() * 5)] : 200,
      // 只有外部明确失败才重试；超时 / 无响应 = 结果未知，不自动重试；不支持幂等的系统从不重试
      result, attempt: result === '成功' ? 1 + (sys.maxRetry && r() < 0.1 ? 1 : 0) : result === '失败' ? 1 + sys.maxRetry : 1,
      actor: sample ? sample.actor : sys.usage?.actor ?? USERS[Math.floor(r() * USERS.length)],
      runId: sample && result === '结果未知' ? sample.runId : undefined,
      msg: result === '成功' ? 'OK' : result === '结果未知'
        ? `请求超时（${sys.timeoutMs / 1000} 秒）未收到响应，结果未知，未自动重试；${sys.idempotent ? '系统每 15 分钟按幂等键查询对方结果，查不到再到动作中心人工对账' : '不支持幂等，无法自动查询，需人工对账'}`
        : sys.maxRetry ? `对方明确返回失败，已重试 ${sys.maxRetry} 次仍失败` : '对方明确返回失败；不支持幂等，未重试，已转人工',
    };
  });
  // 结果未知的调用沿用动作中心的调用时间，按时间倒序重排后编号
  out.sort((a, b) => (a.time < b.time ? 1 : -1)).forEach((c, k) => { c.id = `${sys.id.slice(4)}-${String(n - k).padStart(5, '0')}`; });
  cache.set(sys.id, out);
  return out;
}
