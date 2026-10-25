/** 管理区示例数据：用户（120 人）与审计日志（512 条，按时间倒序）。固定种子，每次一样。 */
import { seeded as rng } from './format';

const SURNAMES = '王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤'.split('');
const GIVEN = ['敏', '伟', '芳', '娜', '静', '磊', '洋', '勇', '艳', '杰', '涛', '明', '超', '霞', '平', '刚', '桂英', '晓峰', '立军', '婷', '佳', '卫国', '建华', '海燕', '志强', '晨', '雪', '浩', '宇轩', '梓涵'];
const DEPTS = ['华东事业部 / 运营中心', '华东事业部 / 财务数据组', '华东事业部 / 供应链', '华东事业部 / 会员中心', '集团总部 / 数据平台', '集团总部 / 信息安全', '华南事业部 / 运营中心'];
const ROLES = ['业务查看', '业务查看', '业务查看', '数据分析师', '数据分析师', '本体建模师', '动作审批人', '系统管理员'];

export interface AdminUser {
  id: string; name: string; account: string; dept: string; role: string;
  status: '启用' | '停用' | '待激活'; lastLogin: string | null; createdAt: string;
}

const pad = (n: number, w = 2) => String(n).padStart(w, '0');

export const ADMIN_USERS: AdminUser[] = (() => {
  const r = rng(20261025);
  const out: AdminUser[] = [];
  for (let i = 0; i < 120; i++) {
    const name = SURNAMES[Math.floor(r() * SURNAMES.length)] + GIVEN[Math.floor(r() * GIVEN.length)];
    const st = r();
    const status = st < 0.86 ? '启用' : st < 0.95 ? '停用' : '待激活';
    const d = Math.floor(r() * 30);
    out.push({
      id: `U${pad(10231 + i * 7, 6)}`, name,
      account: `${['wang', 'li', 'zhang', 'liu', 'chen', 'yang', 'zhao', 'zhou'][i % 8]}.${pad(i + 3, 3)}@xianlin-retail.com`,
      dept: DEPTS[Math.floor(r() * DEPTS.length)], role: ROLES[Math.floor(r() * ROLES.length)],
      status,
      lastLogin: status === '待激活' ? null : `2026-${d > 24 ? '09' : '10'}-${pad(d > 24 ? d : 25 - d)} ${pad(8 + Math.floor(r() * 12))}:${pad(Math.floor(r() * 60))}`,
      createdAt: `202${4 + Math.floor(r() * 3)}-${pad(1 + Math.floor(r() * 12))}-${pad(1 + Math.floor(r() * 28))}`,
    });
  }
  return out;
})();
export const ADMIN_DEPTS = DEPTS;
/** 用户名下待处理的审批数（动作审批、口径变更评审），停用前需要转交 */
export const pendingApprovals = (u: AdminUser) => (u.status === '启用' ? (Number(u.id.slice(1)) * 13) % 5 : 0);

export interface AuditRow { id: string; time: string; actor: string; action: string; target: string; ip: string; result: '成功' | '失败' | '拒绝' }
const ACTIONS: [string, (r: () => number) => string][] = [
  ['登录', () => '本体智能平台'],
  ['查询', (r) => `问数会话 QS${2610250000 + Math.floor(r() * 9999)}`],
  ['导出', (r) => `订单明细（${1000 + Math.floor(r() * 40000)} 行）`],
  ['审批动作', (r) => `发放复购召回优惠券 #A${80000 + Math.floor(r() * 999)}`],
  ['变更角色', (r) => `用户 U${pad(10231 + Math.floor(r() * 120) * 7, 6)}`],
  ['创建 API 密钥', () => 'bi-sync-prod'],
];

export const AUDIT_LOGS: AuditRow[] = (() => {
  const r = rng(7781);
  const out: AuditRow[] = [];
  let t = new Date(2026, 9, 25, 16, 12, 41).getTime();
  for (let i = 0; i < 512; i++) {
    t -= Math.floor(20 + r() * 900) * 1000;
    const d = new Date(t);
    const [action, target] = ACTIONS[Math.floor(r() * (i % 5 === 0 ? ACTIONS.length : 3))];
    const u = ADMIN_USERS[Math.floor(r() * ADMIN_USERS.length)];
    const x = r();
    out.push({
      id: `AL${t}`,
      time: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
      actor: `${u.name}（${u.id}）`, action, target: target(r),
      ip: `10.${12 + Math.floor(r() * 4)}.${Math.floor(r() * 255)}.${Math.floor(r() * 255)}`,
      result: x < 0.94 ? '成功' : x < 0.98 ? '失败' : '拒绝',
    });
  }
  // 口径与本体只通过变更单修改：创建变更单 → 提交审核 → 审核通过 → 发布本体（与 releases.ts 的 RELEASES / DRAFTS 一致）
  const fixed: [string, string, string, string][] = [
    ['2026-10-25 15:02:18', '王敏（U010001）', '更新变更单', 'CR-0012 会员对象拆分“储值卡”'],
    ['2026-10-25 11:20:09', '刘婷（U010004）', '提交审核', 'CR-0010 退款率改按退款完成时间统计'],
    ['2026-10-25 09:41:05', '王敏（U010001）', '创建变更单', 'CR-0012 会员对象拆分“储值卡”'],
    ['2026-10-24 21:17:06', '王敏（U010001）', '发布本体', '版本 v2026.10.3（含 CR-0007）'],
    ['2026-10-24 20:55:12', '陈立军（U010002）', '审核通过', 'CR-0007 销售额改为不含税口径；新增 gmv_tax'],
    ['2026-10-24 18:40:33', '何静（U010003）', '创建变更单', 'CR-0011 毛利率口径扣除促销补贴'],
    ['2026-10-24 16:05:44', '王敏（U010001）', '审核通过', 'CR-0008 出库及时率补充同义词“发货及时率”'],
    ['2026-10-24 11:26:41', '王敏（U010001）', '提交审核', 'CR-0007 销售额改为不含税口径；新增 gmv_tax'],
    ['2026-10-23 16:08:27', '王敏（U010001）', '创建变更单', 'CR-0007 销售额改为不含税口径；新增 gmv_tax'],
  ];
  fixed.forEach(([time, actor, action, target], k) =>
    out.push({ id: `ALCR${k}`, time, actor, action, target, ip: `10.12.40.${17 + k}`, result: '成功' }));
  out.sort((a, b) => (a.time < b.time ? 1 : a.time > b.time ? -1 : 0));
  return out;
})();
