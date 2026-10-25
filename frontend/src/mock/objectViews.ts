/**
 * 建在对象类型上的视图（示例数据，固定种子）：我的页面里的卡片、对象搜索里保存的对象列表、工作台视图。
 * 对象类型页 → 使用与治理 → 视图 页签在用。props 是视图用到的属性显示名，用来判断哪些视图会受草稿改动影响。
 */
import { seeded } from './format';

export type ViewKind = '我的页面' | '对象列表' | '工作台视图';
export type ViewVisibility = '仅自己' | '指定人员' | '角色' | '全员';
export interface ObjectView {
  id: string; objectId: string; name: string; kind: ViewKind; owner: string;
  visibility: ViewVisibility; visibleTo: string; lastUsed: string; uses30d: number;
  filter: string; props: string[];
}

const OWNERS = ['王敏', '陈立军', '刘婷', '周晓峰', '孙浩然', '何静', '吴佳', '赵磊', '钱雨', '林可'];
const ROLES = ['门店店长', '大区经理（华东）', '会员运营', '数据分析师', '业务查看'];
const PEOPLE = ['刘婷、周晓峰', '陈立军、何静、吴佳', '华东运营组 6 人', '财务 BP 3 人', '商品部 9 人'];

/** 每个对象类型的视图主题：[名称, 条件摘要, 用到的属性] */
type Seed = [string, string, string[]];
const SEEDS: Record<string, Seed[]> = {
  order: [
    ['今日待出库订单', '订单状态 = 待出库；下单时间 = 今天', ['订单状态', '下单时间', '门店']],
    ['小程序渠道近 7 天订单', '渠道 = 小程序；出库时间 = 近 7 天', ['渠道', '出库时间']],
    ['大额订单（≥ 2,000 元）', '应付金额（不含税）≥ 2,000', ['应付金额（不含税）', '会员']],
    ['外卖平台取消单', '渠道 = 外卖平台；订单状态 = 已取消', ['渠道', '订单状态', '取消时间']],
    ['华东大区出库及时率看板', '大区 = 华东；按门店分组', ['门店', '出库时间', '订单状态']],
    ['社区团购履约跟踪', '渠道 = 社区团购；配送方式 = 自提', ['渠道', '配送方式', '履约门店']],
    ['测试单巡检', '是否测试单 = 是', ['是否测试单']],
    ['优惠券核销订单', '优惠券码 非空', ['优惠券码', '优惠金额']],
    ['跨店履约订单', '门店 ≠ 履约门店', ['门店', '履约门店']],
    ['门店日销看板', '出库时间 = 昨天；按门店分组', ['门店', '出库时间', '实付金额']],
    ['退款高发订单', '关联退款单 ≥ 1', ['订单号']],
    ['支付方式分布', '按支付方式分组', ['支付方式']],
  ],
  store: [
    ['华东营业门店', '大区 = 华东；门店状态 = 营业', ['所属大区', '门店状态']],
    ['筹备中门店', '门店状态 = 筹备', ['门店状态', '开业日期']],
    ['社区店经营看板', '业态 = 社区店', ['业态', '营业面积']],
    ['同店口径门店清单', '开业日期 ≤ 今天 − 12 个月', ['开业日期']],
    ['按业态门店数', '按业态分组', ['业态']],
    ['补货仓分布', '按补货仓分组', ['补货仓']],
    ['停业待复核门店', '门店状态 = 停业', ['门店状态']],
    ['加盟店名录', '业态 = 加盟店', ['业态', '门店电话']],
    ['大区门店地图', '按所属大区；经纬度', ['所属大区', '经度', '纬度']],
    ['mini 店坪效', '业态 = mini 店', ['业态', '营业面积']],
  ],
  member: [
    ['沉睡会员（90 天未下单）', '最近下单时间 ≤ 今天 − 90 天', ['最近下单时间']],
    ['金卡及以上会员', '会员等级 ∈ 金卡、钻石、至尊', ['会员等级']],
    ['本月新注册会员', '注册时间 = 本月', ['注册时间', '注册渠道']],
    ['储值余额 TOP 500', '储值余额 降序', ['储值余额']],
    ['冻结会员复核', '会员状态 = 冻结', ['会员状态']],
    ['门店注册会员', '注册门店 非空；按门店分组', ['注册门店']],
    ['生日券候选', '生日 = 下月', ['生日']],
    ['积分即将清零', '积分余额 ≥ 500', ['积分余额']],
  ],
};
const GENERIC: Seed[] = [
  ['全部记录', '无筛选', []],
  ['最近 7 天更新', '源数据更新时间 = 近 7 天', ['源数据更新时间']],
  ['按状态分组', '按状态分组', ['状态']],
  ['异常记录巡检', '数据质量检查未通过的记录', []],
  ['我关注的', '收藏的记录', []],
];
const KINDS: ViewKind[] = ['对象列表', '我的页面', '工作台视图'];
const pad = (n: number) => String(n).padStart(2, '0');

const cache: Record<string, ObjectView[]> = {};
/** 某个对象类型上的视图，按最近使用时间倒序 */
export function viewsOf(objectId: string, objName: string): ObjectView[] {
  if (cache[objectId]) return cache[objectId];
  const seeds = SEEDS[objectId] ?? GENERIC.map(([n, f, p]) => [`${objName}${n === '全部记录' ? '' : ' · '}${n === '全部记录' ? '全部记录' : n}`, f, p] as Seed);
  const r = seeded([...objectId].reduce((s, c) => s * 31 + c.charCodeAt(0), 7));
  const out: ObjectView[] = [];
  // 同一主题会被不同人另存成多个视图（个人副本、看板卡片），总数超过一页
  const copies = SEEDS[objectId] ? 2 : 3;
  for (let c = 0; c < copies; c += 1) {
    seeds.forEach(([name, filter, props], i) => {
      const kind = KINDS[(i + c) % 3];
      const vis = (['仅自己', '指定人员', '角色', '全员'] as ViewVisibility[])[Math.floor(r() * 4)];
      const visibleTo = vis === '仅自己' ? '--' : vis === '全员' ? '全部用户' : vis === '角色' ? ROLES[Math.floor(r() * ROLES.length)] : PEOPLE[Math.floor(r() * PEOPLE.length)];
      const mins = Math.floor(r() * 60 * 24 * 40 * (c + 1));
      const t = new Date(Date.UTC(2026, 9, 25, 10, 0) - mins * 60000);
      out.push({
        id: `V-${objectId.slice(0, 3).toUpperCase()}${pad(c)}${pad(i)}`, objectId,
        name: c === 0 ? name : `${name}${kind === '我的页面' ? '（卡片）' : '（副本）'}`,
        kind, owner: OWNERS[Math.floor(r() * OWNERS.length)], visibility: vis, visibleTo,
        lastUsed: `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`,
        uses30d: Math.floor(r() * (c === 0 ? 400 : 60)), filter, props,
      });
    });
  }
  out.sort((a, b) => (a.lastUsed < b.lastUsed ? 1 : -1));
  return (cache[objectId] = out);
}
