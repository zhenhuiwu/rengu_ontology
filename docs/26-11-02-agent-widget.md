# 独立智能问数组件集成（2026-11-02）

## 嵌入宿主页

将 `agent-widget.js`、独立 `agent.html` 及其构建资源部署到问数服务的前端地址。宿主页在 `body` 可用后加载 SDK：

```html
<script src="https://agent.example.com/agent-widget.js"></script>
<script>
  const agent = window.RenguAgent.mount({
    baseUrl: 'https://agent.example.com/',
    ontologyId: 4,
    theme: 'navy',
    title: '智能问数'
  });
  // agent.open();
  // agent.close();
  // agent.destroy();
</script>
```

`baseUrl` 是部署目录，支持 HTTP(S)，不可含 URL 凭据、查询参数或片段；目录末尾的 `/` 会自动补齐。例如 `/tools/` 对应 `/tools/agent.html`。`ontologyId` 必须为正安全整数。`theme` 为 `light`（默认）或 `navy`，兼容将 `dark` 规范为 `navy`。可选 `width` 默认 600，接受 320–960 的整数像素；`zIndex` 默认 1000，可按宿主页层级设置非负整数。标题作为纯文本写入。

子目录部署时，例如 `baseUrl: 'https://host/rengu/'`，静态资源构建基址由部署方设为 `--base=/rengu/`。独立页使用同源目录相对 API：`/rengu/agent.html` 对应 `/rengu/api/agent/v1`；反向代理需移除 `/rengu` 前缀并映射至后端 `/api/agent/v1`。不支持通过任意 `apiBase` 或外部凭据改写 API 目标。本轮只说明该部署合同，未执行构建或修改代理。

SDK 在右下角创建开关和非模态右侧面板。样式位于 Shadow DOM，不改宿主样式、路由、滚动或普通按键行为；小屏宽度限制为视口减 24px，`100vh` 为不支持动态视口单位的浏览器保留回退。只有面板内部激活时 Escape 才关闭，关闭后焦点回到开关。iframe 内部 Escape 经下面的关闭消息处理。

第一次 `open()` 才加载 iframe；关闭会保留当前实例，重新打开不会新建 iframe。相同规范化配置重复 `mount()` 返回同一实例；不同配置会抛错，需先 `destroy()` 再挂载，避免不同本体复用会话。`destroy()` 移除宿主、消息监听与定时器，重复调用安全；已销毁实例不会再打开。SDK 文件重复加载也不重复初始化。

## 独立页面与公开访问边界

iframe 地址为 `new URL('agent.html', baseUrl)`，查询参数仅含 `ontology`、`theme` 和 `embed=1`。SDK 不读取 localStorage/sessionStorage 或平台登录凭据，也不向 iframe 传递 key、token、宿主历史或业务数据。

独立入口使用新增 `/api/agent/v1` 公开问答接口。服务端必须显式配置允许公开的本体 ID；默认 allowlist 为空。客户端没有登录、签发密钥或绕过旧权限的步骤；**列入公开范围的数据可被匿名访问**，应由部署管理员确定公开范围。该入口只提供问答、澄清与图表，不发布动作执行或管理写接口。原工作台、布局和平台权限链不被 SDK 修改。

宿主页与问数服务均需通过 HTTP(S) 部署；HTTPS 宿主应使用 HTTPS 问数地址。宿主页 CSP 需允许 SDK 脚本、组件样式及对应 `frame-src`，问数服务的 `frame-ancestors`/代理嵌入策略需允许该宿主。SDK 不修改这些策略。iframe 使用 `strict-origin` referrer policy，只传宿主 origin；若宿主额外禁止 referrer，iframe 无法向父页发送下面的消息，但宿主标题栏关闭仍可用。

## 消息与真实状态

iframe 可向 `document.referrer` 解析出的精确 HTTP(S) 父 origin 发送：

```js
window.parent.postMessage({ type: 'rengu-agent:close' }, parentOrigin);
window.parent.postMessage({ type: 'rengu-agent:ready' }, parentOrigin);
```

SDK 同时验证 `event.origin === iframeURL.origin` 和 `event.source === iframe.contentWindow`。只识别关闭和就绪消息，不接受消息切换本体、导航或读取会话。`ready` 表示独立界面已挂载，不代表公共配置或模型连接成功；接口失败时由 iframe 内部显示真实错误。未收到界面就绪消息时显示载入状态，20 秒后显示“界面尚未就绪”与重试，不伪造成功。

## 独立演示与检查

通过前端服务打开 `/agent-widget-demo.html?ontology=4&theme=navy`，默认本体 4、浅色主题。可通过 `baseUrl` 参数指向另一个 HTTP(S) 部署目录。演示页的设备表是明确标注的静态展示，不读取或修改原设备。默认不会自动打开组件或请求模型；手动打开后可核对载入、关闭、焦点与布局。

离线检查：`node frontend/scripts/check-agent-widget.mjs`。在最小内存 DOM 中运行真实 SDK，覆盖参数拒绝、纯文本标题、延迟加载、幂等挂载、严格消息源、不可重绑本体、面板 Escape/焦点及卸载边界；不访问网络、存储、数据库或模型。真实公开 allowlist、问答/图表与 1920/2560 布局需主会话浏览器验证，离线检查不替代这些证据。未安装依赖、打包或运行后台测试。
