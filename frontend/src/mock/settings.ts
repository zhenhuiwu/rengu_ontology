/**
 * 模型与系统配置示例数据（PDE工程师）。只给 pages/admin/Settings.vue 用。
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

export interface SystemSettings {
  tenant: TenantSettings;
  security: SecuritySettings;
  channels: NotifyChannel[];
  /** 事件 → 发送渠道 */
  routes: Record<string, ChannelId[]>;
}

export const NOTIFY_EVENTS: NotifyEvent[] = [
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
    'action-failed': ['inbox', 'email', 'wecom'],
    'action-unknown': ['inbox', 'wecom', 'sms'],
    'quality-failed': ['inbox', 'email', 'wecom'],
    'sync-delay': ['inbox', 'wecom'],
    'release-review': ['inbox', 'email'],
    'key-expiring': ['inbox', 'email'],
    'login-locked': ['inbox', 'email', 'sms'],
  },
};

/** 大模型连接（占位：第二期接入，当前只展示规划字段） */
export const LLM_PLACEHOLDER = {
  usages: [
    { use: '问数（自然语言转查询）', model: '平台内置模型', note: '' },
    { use: '结果解读与摘要', model: '平台内置模型', note: '' },
    { use: '业务知识检索', model: '平台内置模型', note: '' },
  ],
  fields: ['服务商', '接口地址', '模型名称', '访问密钥', '超时（秒）', '每日调用上限', '数据脱敏策略'],
};

export const SETTINGS_UPDATED = { tenant: '2026-08-12 10:21 · 王敏', llm: '', notify: '2026-10-24 18:03 · 王敏', security: '2026-10-09 16:47 · 何静' };
