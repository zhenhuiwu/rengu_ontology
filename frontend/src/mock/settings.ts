/**
 * 模型与系统配置示例数据（PDE工程师）。只给 pages/admin/Settings.vue 用。
 * 大模型连接：只做前端配置，不发网络请求；测试连接是模拟结果。
 * 通知渠道里的企业微信、短信复用外部系统 EXT-WX / EXT-SMS（重试规则以 mock/extsys.ts 为准）。
 */

export interface TenantSettings {
  name: string; shortName: string; tenantId: string; industry: string; defaultOrg: string;
  timezone: string; currency: string; fiscalStart: number; weekStart: '周一' | '周日'; dataCutoff: string; admin: string; contact: string;
}
export interface SecuritySettings {
  pwdMin: number; pwdRules: string[]; pwdExpireDays: number; lockAfter: number; lockMinutes: number;
  sessionMinutes: number; mfa: '关闭' | '仅管理员' | '全部用户'; ipAllow: string; watermark: boolean; exportApproval: boolean; auditKeepDays: number;
}
export type ChannelId = 'inbox' | 'email' | 'wecom' | 'dingtalk' | 'sms';
export interface NotifyChannel {
  id: ChannelId; name: string; enabled: boolean; locked?: boolean; via: string; extId?: string;
  target: string; secret?: string; receivers: string; lastTest: string; lastResult: '成功' | '失败' | '未测试'; note?: string;
}
export interface NotifyEvent { key: string; name: string; desc: string; level: '一般' | '重要' | '紧急' }

/** 大模型连接：只在前端配置，密钥只存掩码（明文只在填写当次可见） */
export type LlmUsage = '问数解析' | '向量化' | '结果解释';
export type LlmKind = 'OpenAI 兼容' | 'Azure OpenAI' | 'DashScope' | 'Ollama';
export type LlmDeploy = '私有化部署' | '公有云';
export interface LlmTest { at: string; ok: boolean; latencyMs: number; version: string; msg?: string }
export interface LlmConnection {
  id: string; name: string; kind: LlmKind; deploy: LlmDeploy; baseUrl: string; model: string;
  /** 掩码，如 sk-••••••••7c2e；空串表示无需密钥（如内网 Ollama） */
  keyMask: string; timeoutSec: number; maxConcurrency: number; usages: LlmUsage[]; enabled: boolean;
  createdAt: string; createdBy: string; note?: string; lastTest?: LlmTest;
}
/** schema：只发送字段名和口径；masked：发送脱敏样例；detail：发送明细（只允许私有化部署） */
export type EgressPolicy = 'schema' | 'masked' | 'detail';
export interface LlmSettings {
  connections: LlmConnection[];
  /** 每个用途的默认连接 id */
  defaults: Record<LlmUsage, string>;
  egress: { policy: EgressPolicy; sampleRows: number; maskFields: string[] };
}

export interface SystemSettings {
  tenant: TenantSettings;
  llm: LlmSettings;
  security: SecuritySettings;
  channels: NotifyChannel[];
  /** 事件 → 发送渠道 */
  routes: Record<string, ChannelId[]>;
}

export const NOTIFY_EVENTS: NotifyEvent[] = [
  { key: 'action-pending', name: '动作待确认', desc: '规则暂存的动作，发给该规则的固定确认人（如 R-027 → 吴佳）', level: '一般' },
  { key: 'action-failed', name: '动作执行失败', desc: '外部系统明确返回失败', level: '重要' },
  { key: 'action-unknown', name: '结果未知 · 待对账', desc: '超时或无响应，不会自动重试', level: '紧急' },
  { key: 'quality-failed', name: '数据质量检查不通过', desc: '阻断级规则不通过', level: '重要' },
  { key: 'sync-delay', name: '数据同步延迟', desc: '超过连接设置的延迟阈值', level: '一般' },
  { key: 'release-review', name: '发布待评审', desc: '本体变更提交评审', level: '一般' },
  { key: 'key-expiring', name: 'API 密钥即将过期', desc: '到期前 30 天、7 天各提醒一次', level: '一般' },
  { key: 'login-locked', name: '账号登录锁定', desc: '连续登录失败触发锁定', level: '重要' },
];

export const CHANNEL_ORDER: ChannelId[] = ['inbox', 'email', 'wecom', 'dingtalk', 'sms'];

export const SYSTEM_SETTINGS: SystemSettings = {
  tenant: {
    name: '鲜邻零售集团有限公司', shortName: '鲜邻零售集团', tenantId: 'TN-XL-000317', industry: '零售 · 生鲜连锁', defaultOrg: '华东事业部',
    timezone: 'Asia/Shanghai（UTC+8）', currency: 'CNY 人民币', fiscalStart: 1, weekStart: '周一', dataCutoff: '每日 06:00', admin: '王敏', contact: 'it-ops@xianlin.com',
  },
  llm: {
    connections: [
      {
        id: 'LLM-01', name: '私有化 Qwen（问数）', kind: 'OpenAI 兼容', deploy: '私有化部署', baseUrl: 'http://10.20.8.15:8000/v1', model: 'qwen2.5-72b-instruct',
        keyMask: 'sk-••••••••7c2e', timeoutSec: 60, maxConcurrency: 16, usages: ['问数解析', '结果解释'], enabled: true, createdAt: '2026-08-20 10:12', createdBy: '王敏',
        note: '机房 GPU 集群（vLLM），数据不出内网', lastTest: { at: '2026-10-25 09:12', ok: true, latencyMs: 412, version: 'Qwen2.5-72B-Instruct-GPTQ-Int4 · vLLM 0.6.3' },
      },
      {
        id: 'LLM-02', name: '私有化 bge-m3（向量）', kind: 'OpenAI 兼容', deploy: '私有化部署', baseUrl: 'http://10.20.8.16:8001/v1', model: 'bge-m3',
        keyMask: 'sk-••••••••a91d', timeoutSec: 30, maxConcurrency: 32, usages: ['向量化'], enabled: true, createdAt: '2026-08-20 10:30', createdBy: '王敏',
        note: '对象、指标、业务知识的检索向量', lastTest: { at: '2026-10-25 09:13', ok: true, latencyMs: 38, version: 'bge-m3 · 1024 维 · TEI 1.5.0' },
      },
      {
        id: 'LLM-03', name: '通义千问 qwen-max（公有云）', kind: 'DashScope', deploy: '公有云', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-max',
        keyMask: 'sk-••••••••0b5f', timeoutSec: 60, maxConcurrency: 8, usages: ['问数解析', '结果解释'], enabled: false, createdAt: '2026-06-03 14:20', createdBy: '何静',
        note: '9 月起停用：按数据不出域要求切到私有化部署', lastTest: { at: '2026-09-02 15:30', ok: true, latencyMs: 986, version: 'qwen-max-2025-01-25' },
      },
    ],
    defaults: { 问数解析: 'LLM-01', 向量化: 'LLM-02', 结果解释: 'LLM-01' },
    egress: { policy: 'schema', sampleRows: 5, maskFields: ['手机号', '身份证号', '会员姓名', '收货地址'] },
  },
  security: {
    pwdMin: 10, pwdRules: ['大写字母', '小写字母', '数字'], pwdExpireDays: 90, lockAfter: 5, lockMinutes: 30,
    sessionMinutes: 120, mfa: '仅管理员', ipAllow: '10.0.0.0/8\n172.16.0.0/12\n61.152.80.0/24', watermark: true, exportApproval: true, auditKeepDays: 365,
  },
  channels: [
    { id: 'inbox', name: '站内消息', enabled: true, locked: true, via: '平台内置', target: '顶栏铃铛 · 消息中心', receivers: '按事件的处理人、订阅人', lastTest: '2026-10-25 09:00', lastResult: '成功', note: '始终开启，不能关闭' },
    { id: 'email', name: '邮件', enabled: true, via: 'SMTP', target: 'smtp.xianlin.com:465（SSL）· 发件人 noreply@xianlin.com', secret: '••••••••', receivers: '处理人邮箱；紧急事件抄送 it-ops@xianlin.com', lastTest: '2026-10-20 14:32', lastResult: '成功' },
    { id: 'wecom', name: '企业微信', enabled: true, via: '外部系统 EXT-WX', extId: 'EXT-WX', target: '应用消息 AgentId 1000017 · 群「华东运营告警」', receivers: '处理人（按工号匹配企业微信账号）', lastTest: '2026-10-24 18:05', lastResult: '成功', note: '不支持幂等：发送超时标记结果未知，不重发' },
    { id: 'dingtalk', name: '钉钉', enabled: false, via: '群机器人 Webhook', target: 'https://oapi.dingtalk.com/robot/send?access_token=••••3f9a', secret: 'SEC••••••••c21', receivers: '群「数据平台值班」', lastTest: '2026-09-30 10:12', lastResult: '失败', note: '9 月起改用企业微信，保留配置' },
    { id: 'sms', name: '短信', enabled: true, via: '外部系统 EXT-SMS', extId: 'EXT-SMS', target: '阿里云短信 · 签名「鲜邻」· 模板 SMS_2931xxx', receivers: '值班手机号（仅紧急事件）', lastTest: '2026-10-18 21:40', lastResult: '成功', note: '不支持幂等：发送超时标记结果未知，不重发' },
  ],
  routes: {
    'action-pending': ['inbox', 'wecom'],
    'action-failed': ['inbox', 'email', 'wecom'],
    'action-unknown': ['inbox', 'wecom', 'sms'],
    'quality-failed': ['inbox', 'email', 'wecom'],
    'sync-delay': ['inbox', 'wecom'],
    'release-review': ['inbox', 'email'],
    'key-expiring': ['inbox', 'email'],
    'login-locked': ['inbox', 'email', 'sms'],
  },
};

export const LLM_USAGES: { key: LlmUsage; desc: string }[] = [
  { key: '问数解析', desc: '把自然语言问题解析成对象、指标和筛选条件' },
  { key: '向量化', desc: '对象、指标、业务知识的检索向量（只处理名称和描述）' },
  { key: '结果解释', desc: '对查询结果生成文字解读和摘要' },
];
export const LLM_KINDS: LlmKind[] = ['OpenAI 兼容', 'Azure OpenAI', 'DashScope', 'Ollama'];
export const EGRESS_OPTIONS: { value: EgressPolicy; label: string; desc: string }[] = [
  { value: 'schema', label: '只发送字段名和口径，不发送明细', desc: '发给模型的只有对象 / 字段名称、指标口径和用户的问题；查询在平台内执行。推荐' },
  { value: 'masked', label: '发送脱敏样例', desc: '额外附带少量样例行帮助模型理解取值，敏感字段先脱敏' },
  { value: 'detail', label: '发送明细数据', desc: '把查询结果明细发给模型做解释；只允许私有化部署的连接' },
];
export const MASK_FIELDS = ['手机号', '会员姓名', '收货地址', '身份证号', '银行卡号', '员工工号'];

export const SETTINGS_UPDATED = { tenant: '2026-08-12 10:21 · 王敏', llm: '2026-09-03 10:40 · 何静', notify: '2026-10-24 18:03 · 王敏', security: '2026-10-09 16:47 · 何静' };
