/**
 * 全站唯一的主题来源。组件里不准手写 font-size / font-weight / 颜色 / 圆角 / 阴影，
 * 一律用这里的 token（antd 组件）或 styles/tokens.css 里的 CSS 变量与工具类（自写元素）。
 *
 * 字号阶梯（硬性）：12 辅助信息 · 14 正文/表格/表单/导航 · 16 区块标题 · 20 页面标题 · 24 仅限少数关键指标数字
 * 字重（硬性）：400 常规 · 600 强调
 * 颜色：一种品牌蓝 + 灰阶；绿/橙/红只用于状态。海军蓝是整页换肤，不是只换顶栏。
 */
import { reactive, ref } from 'vue';
import { theme as antdTheme } from 'ant-design-vue';
import type { ThemeConfig } from 'ant-design-vue/es/config-provider/context';

export const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Helvetica Neue", Arial, sans-serif';

export const FONT_SIZES = { aux: 12, body: 14, section: 16, page: 20, kpi: 24 } as const;
export const FONT_WEIGHTS = { regular: 400, strong: 600 } as const;

export type Chrome = 'light' | 'navy';

/** 白色界面。g1 是面板，g3 是页面底。 */
const LIGHT = {
  brand: '#1f5fd1',
  brandBg: '#e8effb',
  mark: '#212D4B',
  g1: '#ffffff',
  g2: '#fafafa',
  g3: '#f5f5f5',
  g4: '#f0f0f0',
  g5: '#e5e6e8',
  g6: '#d9d9d9',
  g7: '#bfbfbf',
  g8: '#8c8c8c',
  g9: '#595959',
  g10: '#262626',
  warningBg: '#fff7e6',
} as const;

/** 海军蓝界面：页面、顶栏、侧栏、面板都是 #212D4B，只用边框和抬起一层区分。 */
const NAVY = {
  brand: '#3B82F6',
  brandBg: '#243A66',
  mark: '#ffffff',
  g1: '#212D4B',
  g2: '#1A2542',
  g3: '#212D4B',
  g4: '#2C3B5C',
  g5: '#3A4C70',
  g6: '#4A5E86',
  g7: '#8EA0C0',
  g8: '#B7C4DA',
  g9: '#D5DEEE',
  g10: '#F4F7FC',
  warningBg: '#3A311C',
} as const;

export const BRAND = LIGHT.brand;

/** 图表和画布读这个对象。切换配色时就地更新，computed 会跟着重算。 */
export const colors = reactive({ ...LIGHT });
export const GRAY = colors;

/** 状态色，只用于状态（标签、圆点、告警），不做装饰 */
export const STATUS = { success: '#389e0d', warning: '#d46b08', error: '#cf1322', info: BRAND } as const;

/**
 * 时间线 / 状态节点语义色（全站统一，只有三种状态色 + 选中蓝）：
 *   normal    灰：正常、已完成（包括成功）
 *   attention 橙：需要关注（预警；“结果未知 · 待对账”也用橙，配问号图标区分）
 *   failed    红：失败
 *   current   蓝：仅用于当前选中的节点
 * 不用紫色、绿色做时间线节点。CSS 变量同名：--tl-normal / --tl-attention / --tl-failed / --tl-current
 */
export const TIMELINE_TONES = { normal: LIGHT.g7, attention: STATUS.warning, failed: STATUS.error, current: BRAND } as const;
export type TimelineTone = keyof typeof TIMELINE_TONES;
/** 业务状态 → 时间线语义色。unknown（结果未知）按“需要关注”处理 */
export const timelineTone = (status?: string, selected = false): TimelineTone => {
  if (selected) return 'current';
  if (status === 'error' || status === 'failed') return 'failed';
  if (status === 'warn' || status === 'warning' || status === 'unknown') return 'attention';
  return 'normal';
};

const CHROME_KEY = 'ui-vue:chrome';
const OVERLAY_SHADOW = '0 2px 8px rgba(0, 0, 0, 0.12)';

const typeTokens = {
  fontFamily: FONT_FAMILY,
  fontSize: 14,
  fontSizeSM: 12,
  fontSizeLG: 16,
  fontSizeXL: 20,
  fontSizeHeading1: 24,
  fontSizeHeading2: 20,
  fontSizeHeading3: 20,
  fontSizeHeading4: 16,
  fontSizeHeading5: 16,
  fontWeightStrong: 600,
  borderRadius: 4,
  borderRadiusLG: 4,
  borderRadiusSM: 2,
  borderRadiusXS: 2,
  borderRadiusOuter: 4,
  wireframe: true,
  boxShadow: OVERLAY_SHADOW,
  boxShadowSecondary: OVERLAY_SHADOW,
  boxShadowTertiary: 'none' as const,
  motionDurationMid: '0.15s',
  motionDurationSlow: '0.2s',
};

const sharedComponents = {
  Table: { borderRadius: 0, borderRadiusLG: 0 } as Record<string, unknown>,
  Card: { boxShadowTertiary: 'none' } as Record<string, unknown>,
  Tag: { fontSizeSM: 14 } as Record<string, unknown>,
  Avatar: { textFontSize: 14, textFontSizeSM: 14, textFontSizeLG: 16 } as Record<string, unknown>,
};

export const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: LIGHT.brand,
    colorInfo: LIGHT.brand,
    colorLink: LIGHT.brand,
    colorSuccess: STATUS.success,
    colorWarning: STATUS.warning,
    colorError: STATUS.error,
    colorTextBase: LIGHT.g10,
    colorBgLayout: LIGHT.g3,
    colorBorder: LIGHT.g6,
    colorBorderSecondary: LIGHT.g4,
    colorSplit: LIGHT.g4,
    ...typeTokens,
  },
  components: {
    ...sharedComponents,
    Layout: { colorBgHeader: LIGHT.g1, colorBgBody: LIGHT.g3 },
    Menu: {
      colorItemBgSelected: LIGHT.brandBg,
      colorItemTextSelected: LIGHT.brand,
      colorGroupTitle: LIGHT.g8,
      radiusItem: 4,
      itemMarginInline: 8,
      colorSubItemBg: LIGHT.g1,
      controlHeightLG: 36,
    } as Record<string, unknown>,
  },
};

export const navyTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: NAVY.brand,
    colorInfo: NAVY.brand,
    colorLink: '#A9C9FF',
    colorSuccess: '#49aa19',
    colorWarning: '#d89614',
    colorError: '#dc4446',
    colorTextBase: NAVY.g10,
    colorBgBase: NAVY.g1,
    colorBgLayout: NAVY.g3,
    colorBgContainer: NAVY.g1,
    colorBgElevated: '#2C3C62',
    colorBorder: NAVY.g5,
    colorBorderSecondary: NAVY.g4,
    colorSplit: NAVY.g4,
    ...typeTokens,
  },
  components: {
    ...sharedComponents,
    Layout: { colorBgHeader: NAVY.g1, colorBgBody: NAVY.g3 },
    Menu: {
      colorItemBg: NAVY.g1,
      colorItemBgSelected: NAVY.brandBg,
      colorItemText: NAVY.g9,
      colorItemTextSelected: '#ffffff',
      colorItemTextHover: '#ffffff',
      colorItemBgHover: 'rgba(255,255,255,0.06)',
      colorGroupTitle: NAVY.g8,
      radiusItem: 4,
      itemMarginInline: 8,
      colorSubItemBg: NAVY.g1,
      controlHeightLG: 36,
    } as Record<string, unknown>,
    Table: { borderRadius: 0, borderRadiusLG: 0, headerBg: NAVY.g2, colorBgContainer: NAVY.g1 } as Record<string, unknown>,
  },
};

/** 兼容旧引用。运行时主题由 App.vue 按 chrome 选择。 */
export const theme = lightTheme;

export const chrome = ref<Chrome>('light');

function readChrome(): Chrome {
  try { return localStorage.getItem(CHROME_KEY) === 'navy' ? 'navy' : 'light'; } catch { return 'light'; }
}

export function applyChrome(next: Chrome) {
  const palette = next === 'navy' ? NAVY : LIGHT;
  Object.assign(colors, palette);
  chrome.value = next;
  if (next === 'navy') document.documentElement.dataset.chrome = 'navy';
  else delete document.documentElement.dataset.chrome;
  try { localStorage.setItem(CHROME_KEY, next); } catch { /* 隐私模式 */ }
}

if (typeof document !== 'undefined') applyChrome(readChrome());
