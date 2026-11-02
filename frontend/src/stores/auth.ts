import { defineStore } from 'pinia';
import { getSession, login, logout, changePassword, clearSessionStorage, type AuthUser } from '@/api/auth';
import { invalidateWorkspaceRequests, isApiError } from '@/api/http';
import { useAppStore } from './app';

let restoring: Promise<boolean> | null = null;
export const useAuthStore = defineStore('auth', {
  state: () => ({ user: null as AuthUser | null, checked: false, restoreError: '', leaving: false }),
  getters: { authenticated: (s) => !!s.user && !s.leaving },
  actions: {
    applyUser(user: AuthUser) {
      this.user = user; this.checked = true; this.restoreError = '';
      const app = useAppStore();
      app.user = { name: user.name || user.account, role: '管理员' };
      app.permissions = ['use', 'build', 'admin'];
    },
    async restore(): Promise<boolean> {
      if (this.checked) return this.authenticated;
      if (!restoring) restoring = (async () => {
        try { this.applyUser((await getSession()).user); return true; }
        catch (e) {
          this.user = null; this.checked = true;
          this.restoreError = isApiError(e) && e.status === 401 ? '' : '暂时无法连接服务，请重试登录';
          return false;
        } finally { restoring = null; }
      })();
      return restoring;
    },
    async signIn(username: string, password: string) {
      this.leaving = false;
      this.applyUser((await login(username, password)).user);
    },
    async changePassword(currentPassword: string, newPassword: string) {
      this.applyUser((await changePassword(currentPassword, newPassword)).user);
    },
    clear() {
      this.leaving = true; this.user = null; this.checked = true;
      invalidateWorkspaceRequests(); clearSessionStorage();
      const app = useAppStore();
      app.clearWorkspace(); app.user = { name: '', role: '' }; app.permissions = [];
      app.notices = []; app.lastRoute = {}; app.notificationUnread = 0;
    },
    async signOut() {
      await logout();
      this.clear();
      // A fresh document discards every module-level data and conversation cache.
      window.location.replace('/login');
    },
    expire() {
      if (this.leaving || !this.user) return;
      const returnUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      this.clear();
      window.location.replace(`/login?expired=1&returnUrl=${encodeURIComponent(returnUrl)}`);
    },
  },
});
