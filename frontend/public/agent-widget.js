/* Rengu Agent host SDK. No dependencies, platform credentials or storage access. */
(function (window, document) {
  'use strict';
  if (window.RenguAgent) return;
  var current = null;

  function mount(options) {
    options = options || {};
    var base = new URL(options.baseUrl);
    if (!/^https?:$/.test(base.protocol) || base.username || base.password || /^https?:\/\/[^/]*@/i.test(String(options.baseUrl)) || /[?#\\\s]/.test(String(options.baseUrl))) {
      throw new TypeError('baseUrl must be an HTTP(S) URL without credentials, query or fragment');
    }
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    var ontology = String(options.ontologyId);
    if (!/^[1-9]\d*$/.test(ontology) || !Number.isSafeInteger(Number(ontology))) throw new TypeError('ontologyId must be a positive safe integer');
    var theme = options.theme === 'dark' ? 'navy' : options.theme || 'light';
    if (theme !== 'light' && theme !== 'navy') throw new TypeError('theme must be light or navy');
    var title = options.title === undefined ? '智能问数' : options.title;
    if (typeof title !== 'string' || !title.trim() || title.length > 100) throw new TypeError('title must be a nonempty string up to 100 characters');
    var width = options.width === undefined ? 600 : options.width;
    var zIndex = options.zIndex === undefined ? 1000 : options.zIndex;
    if (!Number.isInteger(width) || width < 320 || width > 960) throw new TypeError('width must be an integer from 320 to 960');
    if (!Number.isInteger(zIndex) || zIndex < 0 || zIndex > 2147483646) throw new TypeError('zIndex must be a nonnegative integer');
    if (!document.body) throw new Error('Mount RenguAgent after the document body is available');
    var signature = JSON.stringify([base.href, ontology, theme, title, width, zIndex]);
    if (current) {
      if (current.signature === signature) return current.api;
      throw new Error('A widget is already mounted; destroy it before changing its configuration');
    }
    var url = new URL('agent.html', base);
    url.searchParams.set('ontology', ontology);
    url.searchParams.set('theme', theme);
    url.searchParams.set('embed', '1');
    var host = document.createElement('div');
    host.setAttribute('data-rengu-agent-widget', '');
    host.style.position = 'fixed';
    host.style.right = '24px';
    host.style.bottom = '24px';
    host.style.zIndex = String(zIndex);
    var shadow = host.attachShadow({ mode: 'open' });
    var style = document.createElement('style');
    style.textContent = `
      :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color-scheme: light; --agent-bg: #fff; --agent-text: #262626; --agent-muted: #595959; --agent-border: #e5e6e8; --agent-brand: #1f5fd1; }
      :host([data-theme="navy"]) { color-scheme: dark; --agent-bg: #212d4b; --agent-text: #f4f7fc; --agent-muted: #b7c4da; --agent-border: #3a4c70; --agent-brand: #3b82f6; }
      * { box-sizing: border-box; }
      button { font: inherit; font-size: 14px; line-height: 22px; cursor: pointer; }
      button:focus-visible { outline: 2px solid var(--agent-brand); outline-offset: 3px; }
      .launcher { display: block; min-height: 44px; padding: 10px 20px; border: 0; border-radius: 6px; background: var(--agent-brand); color: #fff; font-weight: 600; box-shadow: 0 4px 16px rgba(0,0,0,.16); }
      .launcher:hover { filter: brightness(1.08); }
      .panel { position: absolute; right: 0; bottom: 60px; width: var(--agent-width); max-width: calc(100vw - 48px); height: 900px; max-height: calc(100vh - 112px); max-height: calc(100dvh - 112px); display: flex; flex-direction: column; overflow: hidden; border: 1px solid var(--agent-border); border-radius: 8px; background: var(--agent-bg); color: var(--agent-text); box-shadow: 0 12px 40px rgba(0,0,0,.2); }
      .panel[hidden], .status[hidden] { display: none; }
      .heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 8px 12px 8px 16px; min-height: 44px; flex: none; border-bottom: 1px solid var(--agent-border); }
      .title { font-size: 14px; line-height: 22px; font-weight: 600; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
      .close { flex: none; width: 32px; height: 32px; padding: 0; border: 0; border-radius: 4px; background: transparent; color: var(--agent-muted); font-size: 22px; }
      .close:hover { background: rgba(128,128,128,.12); color: var(--agent-text); }
      .status { display: flex; align-items: center; gap: 12px; padding: 6px 16px; flex: none; font-size: 12px; line-height: 20px; color: var(--agent-muted); border-bottom: 1px solid var(--agent-border); }
      .status button { margin-left: auto; border: 0; padding: 0; background: transparent; color: var(--agent-brand); font-size: 12px; }
      iframe { display: block; width: 100%; flex: 1; min-height: 0; border: 0; background: var(--agent-bg); }
      @media (max-width: 640px) { .panel { right: -12px; max-width: calc(100vw - 24px); max-height: calc(100vh - 100px); max-height: calc(100dvh - 100px); } }
    `;
    host.setAttribute('data-theme', theme);
    host.style.setProperty('--agent-width', width + 'px');
    var launcher = document.createElement('button');
    launcher.type = 'button';
    launcher.className = 'launcher';
    launcher.textContent = title;
    launcher.setAttribute('aria-label', '打开' + title);
    launcher.setAttribute('aria-expanded', 'false');
    launcher.setAttribute('aria-controls', 'rengu-agent-panel');
    launcher.setAttribute('aria-haspopup', 'dialog');
    var panel = document.createElement('section');
    panel.id = 'rengu-agent-panel';
    panel.className = 'panel';
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-label', title);
    var heading = document.createElement('div');
    heading.className = 'heading';
    var label = document.createElement('span');
    label.className = 'title';
    label.textContent = title;
    var dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'close';
    dismiss.textContent = '×';
    dismiss.setAttribute('aria-label', '关闭' + title);
    heading.append(label, dismiss);
    var status = document.createElement('div');
    status.className = 'status';
    status.setAttribute('role', 'status');
    var statusText = document.createElement('span');
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = '重试';
    retry.hidden = true;
    status.append(statusText, retry);
    var frame = document.createElement('iframe');
    frame.title = title;
    frame.referrerPolicy = 'strict-origin';
    panel.append(heading, status, frame);
    shadow.append(style, panel, launcher);
    var opened = false, destroyed = false, started = false, ready = false, timer;
    function clearTimer() { if (timer !== undefined) window.clearTimeout(timer); timer = undefined; }
    function load() {
      if (destroyed) return;
      clearTimer();
      ready = false;
      status.hidden = false;
      retry.hidden = true;
      statusText.textContent = '正在载入问数界面…';
      frame.src = url.href;
      timer = window.setTimeout(function () {
        if (destroyed || ready) return;
        statusText.textContent = '界面尚未就绪，请检查服务或重试';
        retry.hidden = false;
      }, 20000);
    }
    function open() {
      if (destroyed) return;
      opened = true;
      panel.hidden = false;
      launcher.setAttribute('aria-expanded', 'true');
      launcher.setAttribute('aria-label', '关闭' + title);
      if (!started) { started = true; load(); }
      dismiss.focus();
    }
    function close() {
      if (destroyed || !opened) return;
      opened = false;
      panel.hidden = true;
      launcher.setAttribute('aria-expanded', 'false');
      launcher.setAttribute('aria-label', '打开' + title);
      launcher.focus();
    }
    function receive(event) {
      if (destroyed || event.origin !== url.origin || event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object') return;
      if (event.data.type === 'rengu-agent:close') close();
      if (event.data.type === 'rengu-agent:ready') {
        ready = true;
        clearTimer();
        status.hidden = true;
      }
    }
    launcher.addEventListener('click', function () { if (opened) close(); else open(); });
    dismiss.addEventListener('click', close);
    retry.addEventListener('click', load);
    host.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && opened && panel.contains(shadow.activeElement)) { event.preventDefault(); close(); }
    });
    frame.addEventListener('error', function () {
      if (destroyed || !started || ready) return;
      clearTimer();
      statusText.textContent = '问数界面未能载入，请重试';
      retry.hidden = false;
    });
    window.addEventListener('message', receive);
    var api = Object.freeze({ open: open, close: close, destroy: function () {
      if (destroyed) return;
      var focused = document.activeElement === host;
      close();
      destroyed = true;
      clearTimer();
      window.removeEventListener('message', receive);
      host.remove();
      if (focused && document.body.focus) document.body.focus();
      if (current && current.api === api) current = null;
    } });
    current = { signature: signature, api: api };
    document.body.append(host);
    return api;
  }
  window.RenguAgent = Object.freeze({ mount: mount });
})(window, document);
