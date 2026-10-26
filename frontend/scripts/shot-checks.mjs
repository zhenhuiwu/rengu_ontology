// Executed inside the browser: no styles or application state are changed.
export function checkLayout() {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1 || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) return false;
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    }
    return true;
  };
  const label = (el) => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${[...el.classList].slice(0, 3).map(c => '.' + c).join('')}`;
  const doc = document.scrollingElement || document.documentElement;
  const documentOverflow = { x: doc.scrollWidth > innerWidth, y: doc.scrollHeight > innerHeight, width: doc.scrollWidth, height: doc.scrollHeight, viewportWidth: innerWidth, viewportHeight: innerHeight };
  const roots = [document.querySelector('main') || document.querySelector('.page-body') || document.body,
    ...document.querySelectorAll('.ant-drawer-body,.ant-modal-body')].filter(visible);
  const surfaces = roots.map(root => {
    const nodes = [root, ...root.querySelectorAll('*')].filter(el => visible(el) && !el.closest('.side-nav,.sn-menu'));
    const scroll = nodes.filter(el => {
      const style = getComputedStyle(el);
      return el.clientWidth > 24 && el.clientHeight > 24 &&
        ((/auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight) || (/auto|scroll/.test(style.overflowX) && el.scrollWidth > el.clientWidth));
    });
    const nested = scroll.filter(el => scroll.some(parent => parent !== el && parent.contains(el))).map(label);
    const clipped = nodes.filter(el => {
      const style = getComputedStyle(el);
      return el.clientWidth >= 100 && el.clientHeight >= 80 && /hidden|clip/.test(style.overflowY) &&
        !(Number(style.webkitLineClamp) > 0) && el.scrollHeight > el.clientHeight;
    }).map(el => ({ element: label(el), height: el.clientHeight, contentHeight: el.scrollHeight }));
    return { root: label(root), scrollRegions: scroll.map(label), nested, clipped, ok: scroll.length <= 1 && !nested.length && !clipped.length };
  });
  return { ok: !documentOverflow.x && !documentOverflow.y && surfaces.every(s => s.ok), documentOverflow, surfaces };
}

export function checkFonts() {
  const sizes = [12, 14, 16, 20, 24], weights = [400, 600], violations = [], seen = new Set();
  let texts = 0;
  function inspect(el, text, rects) {
    if (!text.trim()) return;
    const style = getComputedStyle(el);
    let regions = [...rects].map(r => ({ left: Math.max(0, r.left), top: Math.max(0, r.top), right: Math.min(innerWidth, r.right), bottom: Math.min(innerHeight, r.bottom) }));
    for (let node = el; node; node = node.parentElement) {
      const css = getComputedStyle(node);
      if (css.display === 'none' || css.visibility === 'hidden' || Number(css.opacity) === 0) return;
      if (/hidden|clip|auto|scroll/.test(css.overflowX + css.overflowY)) {
        const box = node.getBoundingClientRect();
        regions = regions.map(r => ({ left: Math.max(r.left, box.left), top: Math.max(r.top, box.top), right: Math.min(r.right, box.right), bottom: Math.min(r.bottom, box.bottom) }));
      }
    }
    if (!regions.some(r => r.right > r.left && r.bottom > r.top)) return;
    texts++;
    const size = Number.parseFloat(style.fontSize), weight = Number(style.fontWeight);
    if (sizes.includes(size) && weights.includes(weight)) return;
    const key = `${size}/${weight}/${text.trim().slice(0, 80)}`;
    if (!seen.has(key)) { seen.add(key); violations.push({ element: el.tagName.toLowerCase(), text: text.trim().slice(0, 80), size, weight }); }
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let text = walker.nextNode(); text; text = walker.nextNode()) {
    const el = text.parentElement;
    if (!el || /SCRIPT|STYLE|NOSCRIPT|OPTION/.test(el.tagName)) continue;
    const range = document.createRange(); range.selectNodeContents(text);
    inspect(el, text.nodeValue || '', range.getClientRects());
  }
  document.querySelectorAll('input,textarea,select').forEach(el => {
    if (el.matches('input[type=hidden],input[type=checkbox],input[type=radio],input[type=file],input[type=range],input[type=color]')) return;
    inspect(el, el.value || el.placeholder || '', [el.getBoundingClientRect()]);
  });
  return { ok: violations.length === 0, texts, allowedSizes: sizes, allowedWeights: weights, violations };
}
