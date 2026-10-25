import { seeded } from './format';
import { QA_EC_STORES } from './qa';

export interface Store {
  id: string; code: string; name: string; city: string; region: string; format: string;
  manager: string; area: number | null; openedAt: string; status: '营业中' | '装修中' | '已闭店';
}

export const STORES: Store[] = [
  { id: 'SH0031', code: 'SH-PD-0031', name: '上海浦东张江高科店', city: '上海', region: '华东一区', format: '社区店', manager: '王敏', area: 412.6, openedAt: '2019-04-18', status: '营业中' },
  { id: 'SH0047', code: 'SH-XH-0047', name: '上海徐汇漕河泾店', city: '上海', region: '华东一区', format: '写字楼店', manager: '陈立军', area: 268.0, openedAt: '2020-11-02', status: '营业中' },
  { id: 'SH0102', code: 'SH-MH-0102', name: '上海闵行七宝万科广场B1店', city: '上海', region: '华东一区', format: '商场店', manager: '刘婷', area: 655.3, openedAt: '2021-06-26', status: '营业中' },
  { id: 'HZ0012', code: 'HZ-XH-0012', name: '杭州西湖文三路店', city: '杭州', region: '华东二区', format: '社区店', manager: '周晓峰', area: 380.4, openedAt: '2018-09-09', status: '营业中' },
  { id: 'HZ0058', code: 'HZ-BJ-0058', name: '杭州滨江星光大道店', city: '杭州', region: '华东二区', format: '商场店', manager: '吴佳', area: 520.0, openedAt: '2022-03-12', status: '装修中' },
  { id: 'NJ0021', code: 'NJ-GL-0021', name: '南京鼓楼湖南路店', city: '南京', region: '华东三区', format: '社区店', manager: '孙浩然', area: 344.8, openedAt: '2019-12-21', status: '营业中' },
  { id: 'NJ0076', code: 'NJ-JN-0076', name: '南京江宁百家湖店', city: '南京', region: '华东三区', format: '社区店', manager: '赵蕾', area: null, openedAt: '2023-08-05', status: '营业中' },
  { id: 'SZ0009', code: 'SZ-GY-0009', name: '苏州工业园区湖西CBD店', city: '苏州', region: '华东三区', format: '写字楼店', manager: '钱一鸣', area: 236.5, openedAt: '2020-05-30', status: '营业中' },
  { id: 'NB0015', code: 'NB-YZ-0015', name: '宁波鄞州万达店', city: '宁波', region: '华东二区', format: '商场店', manager: '何静', area: 498.2, openedAt: '2021-01-16', status: '营业中' },
  { id: 'HF0004', code: 'HF-ZW-0004', name: '合肥政务区天鹅湖店', city: '合肥', region: '华东四区', format: '社区店', manager: '马晨', area: 401.0, openedAt: '2022-10-01', status: '营业中' },
  { id: 'WX0011', code: 'WX-LX-0011', name: '无锡梁溪崇安寺店', city: '无锡', region: '华东三区', format: '社区店', manager: '朱雅琴', area: 312.7, openedAt: '2017-07-08', status: '已闭店' },
  { id: 'SH0133', code: 'SH-JA-0133', name: '上海静安大悦城店', city: '上海', region: '华东一区', format: '商场店', manager: '林子豪', area: 588.9, openedAt: '2024-04-27', status: '营业中' },
];
/**
 * 全量门店：核心 12 家（STORES，订单/首页排行/告警历史都按这 12 家生成，保持不变）
 * + 问数“华东重点门店”结果表、大区关联门店里出现的其他门店。抽屉、悬停卡按全量解析。
 * 新页面引用门店时，ID 必须在 ALL_STORES 里，否则控制台会报 [objectRegistry] 无法解析。
 */
const CITY_REGION: Record<string, string> = {
  上海: '华东一区', 杭州: '华东二区', 宁波: '华东二区', 南京: '华东三区', 苏州: '华东三区', 无锡: '华东三区',
  合肥: '华东四区', 福州: '华东五区', 南昌: '华东五区', 济南: '华东五区',
};
const MANAGERS = ['张雨桐', '黄志刚', '徐丽', '郑凯', '冯晓燕', '韩冬', '曹静怡', '彭磊', '许佳慧', '邓文博', '谢敏', '宋立新'];
const FORMATS = ['社区店', '商场店', '写字楼店', '社区店', '商场店'];
const EXTRA_STORES: Store[] = (() => {
  const r = seeded(4407);
  return QA_EC_STORES
    .filter((q) => !STORES.some((s) => s.id === q.id))
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((q, i) => {
      const fmt = FORMATS[Math.floor(r() * FORMATS.length)];
      const y = 2017 + Math.floor(r() * 8);
      return {
        id: q.id, code: `${q.id.slice(0, 2)}-${q.id.slice(2)}`, name: q.store, city: q.city,
        region: CITY_REGION[q.city] ?? '华东五区', format: fmt, manager: MANAGERS[i % MANAGERS.length],
        area: Math.round((fmt === '商场店' ? 420 : fmt === '写字楼店' ? 210 : 300) + r() * 220) / 1 + Math.round(r() * 9) / 10,
        openedAt: `${y}-${String(1 + Math.floor(r() * 12)).padStart(2, '0')}-${String(1 + Math.floor(r() * 28)).padStart(2, '0')}`,
        status: '营业中' as const,
      };
    });
})();
export const ALL_STORES: Store[] = [...STORES, ...EXTRA_STORES];
export const storeById = (id: string) => ALL_STORES.find((s) => s.id === id || s.code === id);
