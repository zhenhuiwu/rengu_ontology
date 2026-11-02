import type { DeliveryErrorDetails } from '@/api/action';

export const deliveryLabels: Record<string, string> = {
  pending: '待投递', processing: '投递中', delivered: '已投递', confirmed: '人工确认已接收',
  skipped: '已跳过', failed: '投递失败', unknown: '结果待确认', quarantined: '迟到事件已隔离',
};
export const deliveryErrorLabels: Record<string, string> = {
  EVENT_RELEASE_CHANGED_REQUIRES_REVIEW: '事件入站后本体发布版本已变化；原快照保留，须人工核查',
  ACTION_RELEASE_CHANGED_REQUIRES_REVIEW: '当前本体发布版本不可用或与入站绑定版本不同；须人工核查',
};
export const decisionLabels = {
  received: '核查确认已接收', skip: '人工跳过，放行后序',
  retry: '已确认未接收，授权重试', supplement: '通道已修复，申请补投',
};
export const historyLabels: Record<string, string> = {
  attempt_started: '开始尝试', attempt_finished: '尝试结束', attempt_interrupted: '尝试中断',
  preflight: '投递条件检查', before_disposition: '处置前原始记录', disposition: '人工处置',
};
/** Only format the public diagnostic allowlist; never render arbitrary exception properties. */
export function deliveryErrorPresentation(details?: DeliveryErrorDetails | null) {
  if (!details) return null;
  const count = (value: number | undefined) => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined;
  const fields = (details.fields ?? []).slice(0, 20).map(item => ({
    location: item.field || `字段 #${item.index + 1}（名称未提供）`,
    reason: item.reason === 'FIELD_VALIDATION_FAILED' ? '字段校验未通过' : '原因未提供',
  }));
  const failed = (details.failed ?? []).slice(0, 20).map(item => ({
    location: item.name || `校验项 #${item.index + 1}（名称未提供）`,
    reason: item.reason === 'SUBMISSION_CRITERION_FAILED' ? '提交条件未满足' : '原因未提供',
  }));
  const fieldsCount = count(details.fieldsCount), failedCount = count(details.failedCount);
  return {
    phase: details.phase === 'preflight' ? '投递前检查' : details.phase === 'execution' ? '投递执行' : '',
    status: typeof details.status === 'number' && Number.isInteger(details.status) && details.status >= 400 && details.status <= 599 ? details.status : undefined,
    fields, failed, fieldsCount, failedCount, valuesOmitted: details.valuesOmitted === true,
    fieldsTruncated: fieldsCount !== undefined && fieldsCount > fields.length,
    failedTruncated: failedCount !== undefined && failedCount > failed.length,
  };
}
