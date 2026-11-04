import { apiClient } from './http';

export interface LineageNode { id: string; kind: string; code: string; name: string; version?: string }
export interface LineageEdge { id: string; source: string; target: string; kind: string; evidence: string }
export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
  unresolved: { node: string; location: string; reason: string }[];
  scope: 'current';
  notes: string[];
}

const api = apiClient('static');
export const fetchLineage = (signal: AbortSignal) => api.get<LineageGraph>('/ontology/lineage', { signal });
