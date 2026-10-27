export interface HumanCheck { name?: string; rule?: string; reason?: string }
export interface DeclaredEdit { kind?: string; op?: string; obj?: string; field?: string; target?: string; when?: string; note?: string; status?: string }
export interface ActionReview { humanChecks?: HumanCheck[] | null; declaredEdits?: DeclaredEdit[] | null; reviewUnavailable?: string | null }
export const humanCheckText = (item: HumanCheck) => [item.name, item.rule || item.reason].filter(Boolean).join('：');
export const declaredEditText = (item: DeclaredEdit) => [item.kind, item.op, item.obj || item.target, item.field || item.note, item.when].filter(Boolean).join(' · ');
export function reviewFields(review: ActionReview) {
  if (review.reviewUnavailable) return [{ label: '复核清单', value: review.reviewUnavailable }];
  return [
    ...(review.humanChecks?.length ? [{ label: '需人工复核', value: review.humanChecks.map(humanCheckText).join('；') }] : []),
    ...(review.declaredEdits?.length ? [{ label: '外部系统声明效果', value: review.declaredEdits.map(declaredEditText).join('；') }] : []),
  ];
}
