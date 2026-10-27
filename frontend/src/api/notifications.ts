import { apiClient, currentApiContext, type PageResult } from './http';

const api = apiClient('notifications');
export const isNotificationsReal = () => api.isReal();
export const notificationContextKey = () => { const ctx = currentApiContext(); return JSON.stringify([ctx.ontologyId, ctx.demoAs]); };
export interface Notification { id: string; kind: 'cr' | 'page'; objId: string; title: string; desc: string; time: string; read: boolean }
const ownContext = () => {
  const ctx = currentApiContext();
  return { readonlyParams: false, headers: { 'X-Demo-As': ctx.demoAs ?? '', 'X-Ontology-Id': ctx.ontologyId ?? '' } };
};
export const listNotifications = (page: number, unreadOnly = false) => api.get<PageResult<Notification> & { unread: number }>('/notifications', { ...ownContext(), query: { page, size: 10, unreadOnly } });
export const unreadNotifications = () => api.get<{ unread: number }>('/notifications/unread', ownContext());
export const markNotificationRead = (id: string) => api.post<{ unread: number }>(`/notifications/${encodeURIComponent(id)}:read`, {}, ownContext());
export const markAllNotificationsRead = () => api.post<{ unread: number }>('/notifications:read-all', {}, ownContext());
