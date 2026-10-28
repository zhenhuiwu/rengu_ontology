import { apiClient, type PageResult } from './http';

export interface OntologyWorkspace {
  id: number; code: string; name: string; description: string; domain: string;
  isDefault: boolean; role: 'owner' | 'editor' | 'viewer';
  directPublishEnabled?: boolean;
  workersPaused?: boolean;
}
export interface WorkspaceList extends Partial<Omit<PageResult<OntologyWorkspace>, 'items'>> {
  items: OntologyWorkspace[]; currentOntologyId: number | null; canCreate: boolean;
}
const api = apiClient('versioning');
const platform = { ontologyScope: false, readonlyParams: false };
export const workspaceCatalog = () => api.get<WorkspaceList>('/workspaces/catalog', platform);
export const listWorkspaces = (page?: number, size?: number) => api.get<WorkspaceList>('/workspaces', { ...platform, query: { page, size } });
export const currentWorkspace = (id: string) => api.get<OntologyWorkspace>('/workspaces/current', { ...platform, headers: { 'X-Ontology-Id': id } });
export const createWorkspace = (body: { code: string; name: string; description: string; domain: string }) => api.post<OntologyWorkspace>('/workspaces', body, platform);
export const deletedWorkspaces = () => api.get<{ items: OntologyWorkspace[] }>('/workspaces/deleted', platform);
export const deleteWorkspace = (id: number) => api.post(`/workspaces/${id}:delete`, {}, platform);
export const restoreWorkspace = (id: number) => api.post(`/workspaces/${id}:restore`, {}, platform);
export const resumeWorkspaceWorkers = (id: number) => api.post(`/workspaces/${id}:resume-workers`, {}, platform);

export type WorkspaceRole = OntologyWorkspace['role'];
export interface WorkspaceMember { userId: string; name: string; role: WorkspaceRole }
export interface WorkspaceMemberOption { id: string; name: string }
export interface WorkspaceRoleBindings { roleCodes: string[]; items: { code: string; name: string; description: string }[] }
export const listWorkspaceMembers = (id: number, page: number, size: number) => api.get<PageResult<WorkspaceMember>>(`/workspaces/${id}/members`, { ...platform, query: { page, size } });
export const workspaceMemberOptions = (id: number, kw = '') => api.get<PageResult<WorkspaceMemberOption>>(`/workspaces/${id}/member-options`, { ...platform, query: { kw, page: 1, size: 10 } });
export const saveWorkspaceMember = (id: number, userId: string, role: WorkspaceRole) => api.put<WorkspaceMember>(`/workspaces/${id}/members`, { userId, role }, platform);
export const getWorkspaceRoleBindings = (id: number) => api.get<WorkspaceRoleBindings>(`/workspaces/${id}/role-bindings`, platform);
export const saveWorkspaceRoleBindings = (id: number, roleCodes: string[]) => api.put<{ roleCodes: string[] }>(`/workspaces/${id}/role-bindings`, { roleCodes }, platform);
