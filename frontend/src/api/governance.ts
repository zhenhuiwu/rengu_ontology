import { apiClient } from './http';
import type { ScopeProp } from './roles';

const api = apiClient('admin');
export interface Vocabulary { code: string; name: string }
export const OPERATIONS = ['read', 'filter', 'aggregate', 'export', 'execute', 'external', 'invoke'] as const;
export type Operation = typeof OPERATIONS[number];
export const OP_NAMES: Record<Operation, string> = { read: '读取', filter: '筛选与排序', aggregate: '聚合', export: '导出', execute: '执行动作与规则', external: '外发', invoke: '调用函数' };
export interface Downgrade { from_level: string | null; to_level: string | null; remove_classes: string[] }
export interface Grant { role: string; purposes: string[]; classes: string[]; level: string | null; operations: Operation[]; unmask: boolean }
export interface ResourcePolicy { resource: string; classes: string[]; level: string | null; purposes: string[]; mask: 'none' | 'null' | 'redact'; dependencies: string[]; dependencies_complete: boolean; downgrade: Downgrade | null; reason: string }
export interface PolicyDefinition { classes: Vocabulary[]; levels: Vocabulary[]; purposes: Vocabulary[]; grants: Grant[]; resources: ResourcePolicy[]; downgrades: Downgrade[] }
export interface ResourceNode { id: string; kind: string; code: string; name: string }
export interface EffectiveState { protected: boolean; classes: string[]; level: string | null; purposes: string[]; mask: string; unresolved: boolean; dependencies: string[]; declared: boolean }
export interface PolicyDocument { revision: number; definition: PolicyDefinition; resources: ResourceNode[]; effective: Record<string, EffectiveState>; roles: Vocabulary[] }
export const loadPolicy = () => api.get<PolicyDocument>('/governance/policy', { readonlyParams: false });
export const savePolicy = (revision: number, definition: PolicyDefinition) => api.put<{ revision: number }>('/governance/policy', { revision, definition }, { readonlyParams: false });
export const policyEffective = (role: string, purpose: string, operation: Operation) => api.get<Record<string, { allowed: boolean; mask: string; reason?: string }>>('/governance/policy/effective', { query: { role, purpose, operation }, readonlyParams: false });
export const myGovernanceAccess = () => api.get<{ purposes: Vocabulary[]; selected: string | null; configured: boolean }>('/governance/my-access', { readonlyParams: false });
export const relationPaths = () => api.get<{ items: ScopeProp[] }>('/governance/relation-paths');
export const relationValues = (objectTypeCode: string, path: string, kw = '') => api.get<{ items: string[]; hasMore: boolean }>('/governance/relation-values', { query: { objectTypeCode, path, kw } });
