/** Type identifiers are local to a workspace; copying requires one explicit identical binding. */
export function canCopyKeyWorkspace(sourceIds: readonly number[] | undefined, targetId?: string | null): boolean {
  const target = Number(targetId ?? '1');
  return Number.isSafeInteger(target) && target > 0 && Array.isArray(sourceIds)
    && sourceIds.length === 1 && Number.isSafeInteger(sourceIds[0]) && sourceIds[0] === target;
}
