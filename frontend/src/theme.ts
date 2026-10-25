/**
 * 全站唯一的主题来源。组件里不准手写 font-size / font-weight / 颜色 / 圆角 / 阴影，
 * 一律用这里的 token（antd 组件）或 styles/tokens.css 里的 CSS 变量与工具类（自写元素）。
 *
 * 字号阶梯（硬性）：12 辅助信息 · 14 正文/表格/表单/导航 · 16 区块标题 · 20 页面标题 · 24 仅限少数关键指标数字
 * 字重（硬性）：400 常规 · 600 强调
 * 颜色：一种品牌蓝 + 灰阶；绿/橙/红只用于状态。
 */
import type { ThemeConfig } from 'ant-design-vue/es/config-provider/context';

export const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Helvetica Neue", Arial, sans-serif';

export const FONT_SIZES = { aux: 12, body: 14, section: 16, page: 20, kpi: 24 } as const;
export const FONT_WEIGHTS = { regular: 400, strong: 600 } as const;

export const BRAND = '#1f5fd1';

/** 灰阶（与 tokens.css 保持一致） */
export const GRAY = {
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
} as const;

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
export const TIMELINE_TONES = { normal: GRAY.g7, attention: STATUS.warning, failed: STATUS.error, current: BRAND } as const;
export type TimelineTone = keyof typeof TIMELINE_TONES;
/** 业务状态 → 时间线语义色。unknown（结果未知）按“需要关注”处理 */
export const timelineTone = (status?: string, selected = false): TimelineTone => {
  if (selected) return 'current';
  if (status === 'error' || status === 'failed') return 'failed';
  if (status === 'warn' || status === 'warning' || status === 'unknown') return 'attention';
  return 'normal';
};

const OVERLAY_SHADOW = '0 2px 8px rgba(0, 0, 0, 0.12)'; // 仅浮层（抽屉/弹窗/下拉）用于区分层级

export const theme: ThemeConfig = {
  token: {
    colorPrimary: BRAND,
    colorInfo: BRAND,
    colorLink: BRAND,
    colorSuccess: STATUS.success,
    colorWarning: STATUS.warning,
    colorError: STATUS.error,
    colorTextBase: GRAY.g10,
    colorBgLayout: GRAY.g3,
    colorBorder: GRAY.g6,
    colorBorderSecondary: GRAY.g4,
    colorSplit: GRAY.g4,
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
    boxShadowTertiary: 'none',
    motionDurationMid: '0.15s',
    motionDurationSlow: '0.2s',
  },
  components: {
    Layout: { colorBgHeader: GRAY.g1, colorBgBody: GRAY.g3 },
    Menu: {
      colorItemBgSelected: '#e8effb',
      colorItemTextSelected: BRAND,
      colorGroupTitle: GRAY.g8,
      radiusItem: 4,
      itemMarginInline: 8,
      colorSubItemBg: GRAY.g1,
      // 菜单项高度 36：两级导航在 1920×1080 下一屏放完
      controlHeightLG: 36,
    } as Record<string, unknown>,
    Table: { borderRadius: 0, borderRadiusLG: 0 } as Record<string, unknown>,
    Card: { boxShadowTertiary: 'none' } as Record<string, unknown>,
    // 标签文字按正文 14（12 只留给时间戳/单位等辅助信息）；头像文字 14（antd 默认 18 不在白名单）
    Tag: { fontSizeSM: 14 } as Record<string, unknown>,
    Avatar: { textFontSize: 14, textFontSizeSM: 14, textFontSizeLG: 16 } as Record<string, unknown>,
  },
};
