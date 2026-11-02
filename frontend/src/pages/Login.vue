<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { theme } from 'ant-design-vue';
import { EyeOutlined, EyeInvisibleOutlined, ArrowRightOutlined } from '@ant-design/icons-vue';
import { useAuthStore } from '@/stores/auth';
import { safeReturnUrl } from '@/api/auth';
import { isApiError } from '@/api/http';
import { applyChrome, chrome } from '@/theme';
import loginLandscapeDark from '@/assets/login/rengu-ontology-landscape-v3-dark.png';
import loginLandscapeLight from '@/assets/login/rengu-ontology-landscape-v3-light.png';

const auth = useAuthStore(), route = useRoute(), router = useRouter();
const { token } = theme.useToken();
const loginTokens = computed(() => ({
  '--login-control-bg': token.value.colorBgContainer,
  '--login-control-border': token.value.colorBorder,
  '--login-control-hover': token.value.colorPrimaryHover,
  '--login-control-focus': token.value.colorPrimary,
  '--login-control-outline': token.value.controlOutline,
  '--login-control-outline-width': `${token.value.controlOutlineWidth}px`,
  '--login-placeholder': token.value.colorTextPlaceholder,
  '--login-disabled-bg': token.value.colorBgContainerDisabled,
  '--login-disabled-text': token.value.colorTextDisabled,
  '--login-error': token.value.colorErrorText,
  '--login-error-border': token.value.colorError,
  '--login-error-outline': token.value.colorErrorOutline,
  '--login-font': token.value.fontFamily,
}));
const loginHero = computed(() => chrome.value === 'navy' ? loginLandscapeDark : loginLandscapeLight);
const username = ref('admin'), password = ref(''), passwordVisible = ref(false), submitting = ref(false);
const error = ref('');
const passwordInput = ref<HTMLInputElement | null>(null);
const formError = computed(() => error.value || auth.restoreError);
const expired = computed(() => route.query.expired === '1');
function clearError() { error.value = ''; auth.restoreError = ''; }
async function submit() {
  if (submitting.value) return;
  clearError();
  if (!username.value.trim() || !password.value) { error.value = '请输入账号和密码'; return; }
  submitting.value = true;
  try {
    await auth.signIn(username.value.trim(), password.value);
    password.value = '';
    await router.replace(safeReturnUrl(route.query.returnUrl));
  } catch (e) {
    if (isApiError(e) && e.status === 401) {
      error.value = '账号或密码错误，请重新输入'; password.value = ''; passwordInput.value?.focus();
    } else error.value = isApiError(e) ? e.message : '登录失败，请稍后重试';
  } finally { submitting.value = false; }
}
onBeforeUnmount(() => { password.value = ''; });
</script>

<template>
  <main class="login-page" :style="loginTokens">
    <header class="login-header">
      <div class="login-brand">
        <span class="login-mark" aria-hidden="true" />
        <div><strong>仁谷科技</strong><span>RenGu Technology</span></div>
      </div>
      <a-segmented
        class="login-theme" size="small" :value="chrome" aria-label="界面配色"
        :options="[{ label: '白色', value: 'light' }, { label: '海军蓝', value: 'navy' }]"
        @change="(value: string | number) => applyChrome(value === 'navy' ? 'navy' : 'light')"
      />
    </header>

    <div class="login-main">
      <section class="login-hero" aria-labelledby="login-product-title">
        <div class="login-hero-copy">
          <h1 id="login-product-title">本体智能平台</h1>
        </div>
        <img class="login-hero-image" :src="loginHero" alt="" aria-hidden="true" fetchpriority="high" decoding="async" draggable="false" />
      </section>

      <section class="login-form-panel" aria-labelledby="login-title">
        <div class="login-form-content">
          <div class="login-heading"><h2 id="login-title">登录</h2></div>
          <form class="login-form" :aria-busy="submitting" @submit.prevent="submit">
            <div class="login-field">
              <label for="login-username">账号</label>
              <input id="login-username" v-model="username" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required :disabled="submitting" :aria-invalid="!!formError" :aria-describedby="formError ? 'login-error' : undefined" @input="clearError" />
            </div>
            <div class="login-field">
              <label for="login-password">密码</label>
              <div class="login-password-wrap">
                <input id="login-password" ref="passwordInput" v-model="password" name="password" :type="passwordVisible ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" required :disabled="submitting" :aria-invalid="!!formError" :aria-describedby="formError ? 'login-error' : undefined" @input="clearError" />
                <button type="button" class="password-toggle" :aria-label="passwordVisible ? '隐藏密码' : '显示密码'" :aria-pressed="passwordVisible" :disabled="submitting" @click="passwordVisible = !passwordVisible"><EyeInvisibleOutlined v-if="passwordVisible" /><EyeOutlined v-else /></button>
              </div>
            </div>
            <div class="login-status" aria-live="polite" aria-atomic="true">
              <p v-if="formError" id="login-error" class="login-error" role="alert">{{ formError }}</p>
              <p v-else-if="expired" class="login-expired">登录已过期，请重新登录</p>
            </div>
            <a-button class="login-submit" type="primary" html-type="submit" :loading="submitting"><span class="login-submit-content"><span>{{ submitting ? '正在登录' : '登录' }}</span><ArrowRightOutlined v-if="!submitting" /></span></a-button>
          </form>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.login-page {
  --login-content-width: min(1920px, calc(100% - clamp(64px, 8vw, 224px)));
  min-height: 100vh; min-height: 100dvh; display: flex; flex-direction: column;
  background: var(--g3); color: var(--g10); font-family: var(--login-font);
}
.login-header { width: var(--login-content-width); margin: 0 auto; padding: 32px 0; display: flex; align-items: center; justify-content: space-between; gap: 24px; flex: none; }
.login-brand { display: flex; align-items: center; gap: 12px; }
.login-mark { display: block; width: 34px; height: 40px; flex: none; background: var(--mark); -webkit-mask: url('../assets/rengu-mark.png') center / contain no-repeat; mask: url('../assets/rengu-mark.png') center / contain no-repeat; }
.login-brand strong { display: block; font-size: 21px; font-weight: var(--fw-strong); line-height: 1.4; letter-spacing: 2px; }
.login-brand span:not(.login-mark) { display: block; margin-top: 2px; color: var(--g9); font-size: 11px; letter-spacing: .3px; line-height: 1.5; }
.login-theme { flex: none; }
.login-main { width: var(--login-content-width); margin: 0 auto; padding: 0 0 64px; flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 480px; gap: clamp(80px, 6vw, 160px); align-items: center; }
.login-hero { position: relative; min-width: 0; width: 100%; aspect-ratio: 4 / 3; }
.login-hero-copy { position: absolute; top: 14%; left: 6%; z-index: 1; }
.login-hero-copy h1 { margin: 0; color: var(--g10); font-size: clamp(34px, 1.8vw, 44px); font-weight: var(--fw-strong); line-height: 1.4; letter-spacing: 3px; }
.login-hero-image { position: absolute; inset: 0; display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; -webkit-mask-image: linear-gradient(90deg, transparent, #000 6%, #000 94%, transparent), linear-gradient(180deg, transparent, #000 5%, #000 94%, transparent); -webkit-mask-composite: source-in; mask-image: linear-gradient(90deg, transparent, #000 6%, #000 94%, transparent), linear-gradient(180deg, transparent, #000 5%, #000 94%, transparent); mask-composite: intersect; }
.login-form-panel { position: relative; width: 100%; min-width: 0; padding: 40px 44px 44px; background: transparent; border: var(--border); border-radius: 8px; }
.login-form-content { width: 100%; }
.login-heading h2 { margin: 0 0 36px; color: var(--g10); font-size: 28px; line-height: 1.5; font-weight: var(--fw-strong); letter-spacing: 1px; }
.login-field + .login-field { margin-top: 24px; }
.login-field label { display: block; margin-bottom: 10px; font-size: 14px; font-weight: var(--fw-strong); line-height: 1.5; }
.login-field input { display: block; width: 100%; height: 54px; padding: 0 16px; color: var(--g10); background: var(--login-control-bg); border: 1px solid var(--login-control-border); border-radius: var(--radius); font: inherit; font-size: 15px; outline: none; transition: border-color 150ms, box-shadow 150ms; }
.login-field input::placeholder { color: var(--login-placeholder); }
.login-field input:hover { border-color: var(--login-control-hover); }
.login-field input:focus { border-color: var(--login-control-focus); box-shadow: 0 0 0 var(--login-control-outline-width) var(--login-control-outline); }
.login-field input[aria-invalid="true"] { border-color: var(--login-error-border); }
.login-field input[aria-invalid="true"]:focus { box-shadow: 0 0 0 var(--login-control-outline-width) var(--login-error-outline); }
.login-password-wrap { position: relative; }.login-password-wrap input { padding-right: 54px; }
.password-toggle { position: absolute; top: 5px; right: 4px; display: grid; place-items: center; width: 44px; height: 44px; padding: 0; color: var(--g9); background: transparent; border: 0; border-radius: var(--radius); font-size: 18px; cursor: pointer; }
.password-toggle:hover { color: var(--login-control-hover); background: var(--brand-bg); }
.password-toggle:focus-visible { outline: 2px solid var(--login-control-focus); outline-offset: 2px; }
.login-status { min-height: 48px; padding: 12px 0 8px; font-size: 13px; line-height: 20px; }.login-status p { margin: 0; }
.login-error { color: var(--login-error); }.login-expired { color: var(--g9); }
.login-submit { display: flex; align-items: center; justify-content: center; width: 100%; height: 54px; padding: 0 24px; font-size: 15px; font-weight: var(--fw-strong); }
.login-submit-content { display: inline-flex; align-items: center; gap: 14px; }
.login-field input:disabled { color: var(--login-disabled-text); background: var(--login-disabled-bg); border-color: var(--login-control-border); cursor: wait; }
.password-toggle:disabled, .password-toggle:disabled:hover { color: var(--login-disabled-text); background: transparent; cursor: wait; }
@media (max-width: 1280px) { .login-main { grid-template-columns: minmax(0, 1fr) 420px; gap: 40px; }.login-form-panel { padding: 36px; }.login-hero-copy h1 { font-size: 32px; } }
@media (max-width: 960px) { .login-page { --login-content-width: min(calc(100% - 48px), 600px); }.login-header { padding: 24px 0; }.login-main { grid-template-columns: minmax(0, 1fr); gap: 0; padding: 16px 0 40px; align-content: center; }.login-hero { display: none; }.login-form-panel { width: min(100%, 480px); justify-self: center; } }
@media (max-width: 600px) { .login-page { --login-content-width: calc(100% - 40px); }.login-header { padding: 22px 0; gap: 12px; }.login-brand { gap: 8px; }.login-mark { width: 26px; height: 32px; }.login-brand strong { font-size: 17px; letter-spacing: 1px; }.login-brand span:not(.login-mark) { font-size: 9px; }.login-main { padding: 16px 0 32px; }.login-form-panel { padding: 32px 28px 36px; }.login-heading h2 { font-size: 27px; } }
@media (prefers-reduced-motion: reduce) { .login-page *, .login-page *::before, .login-page *::after { transition: none !important; animation: none !important; } }
</style>
