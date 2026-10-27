export const deliveryLabels: Record<string, string> = {
  pending: '待投递', processing: '投递中', delivered: '已投递', confirmed: '人工确认已接收',
  skipped: '已跳过', failed: '投递失败', unknown: '结果待确认', quarantined: '迟到事件已隔离',
};
export const decisionLabels = {
  received: '核查确认已接收', skip: '人工跳过，放行后序',
  retry: '已确认未接收，授权重试', supplement: '通道已修复，申请补投',
};
export const historyLabels: Record<string, string> = {
  attempt_started: '开始尝试', attempt_finished: '尝试结束', attempt_interrupted: '尝试中断',
  preflight: '投递条件检查', before_disposition: '处置前原始记录', disposition: '人工处置',
};
