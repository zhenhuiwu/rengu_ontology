// Real installed Chromium, local fixture only. No app/database writes or browser download.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtempSync,readdirSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {allRoutes,slug} from './shot.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=mkdtempSync(path.join(tmpdir(),'rengu-shot-check-'));
const html=(route)=>`<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:400 14px sans-serif}button,input{font:inherit}main{height:100vh;width:100%;box-sizing:border-box}
.sidebar{position:fixed;right:0;top:0;width:100px;height:100px;overflow:auto}.scroll{overflow:auto;height:100vh}.long{height:1700px}.empty-icon{font-size:99px}.hidden{display:none;font-size:19px}
${route==='/large'? '@media(min-width:2000px){.large{height:1700px}}':''}
</style></head><body>${route==='/ok'?'<nav class="side-nav sidebar"><div style="height:500px">导航独立滚动</div></nav><main class="scroll"><div class="long">允许一个主区滚动</div><div class="empty-icon"></div><p class="hidden">隐藏文字</p></main>':route==='/bad'?'<main style="height:1700px"><p style="font-size:13px;font-weight:500">错误字号</p><p style="font-size:13.98px">不能四舍五入通过</p></main><script>console.error("fixture-console-error")</script>':route==='/clipped'?'<main style="overflow:hidden"><section class="panel long">不能通过隐藏溢出变绿</section></main>':route==='/click'?'<main><button onclick="document.getElementById(&quot;next&quot;).hidden=false">展开</button><button id="next" hidden onclick="document.getElementById(&quot;bad&quot;).hidden=false">显示</button><p id="bad" hidden style="font-size:13px">交互后字号错误</p></main>':route==='/nested'?'<main style="height:300px;overflow:auto"><div style="height:200px;overflow:auto"><div style="height:500px">内层滚动</div></div><div style="height:500px">外层滚动</div></main>':'<main class="large">只在大分辨率溢出</main>'}</body></html>`;
const server=createServer((req,res)=>{if(req.url==='/favicon.ico'){res.writeHead(204).end();return;}res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html(req.url));});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
try{
 const base=`http://127.0.0.1:${server.address().port}`;
 const code=await new Promise((resolve,reject)=>{
  const child=spawn(process.execPath,['scripts/shot.mjs','--base',base,'--out',out,'/ok','/bad','/clipped','/nested','/large','/click#click=展开>显示'],{cwd:root,env:{...process.env,SHOT_SETTLE_MS:'0'},stdio:['ignore','pipe','pipe']});
  let output='';child.stdout.on('data',d=>output+=d);child.stderr.on('data',d=>output+=d);child.on('error',reject);child.on('close',status=>{if(status!==1)console.error(output);resolve(status);});
 });
 assert.equal(code,1,'deliberate fixture failures must set nonzero exit');
 const report=JSON.parse(readFileSync(path.join(out,readdirSync(out).find(n=>n.endsWith('.json'))),'utf8'));
 const results=route=>report.results.filter(r=>r.route===route);
 assert.equal(report.results.length,12);
 assert.ok(results('/ok').every(r=>r.ok),'sidebar/empty icon/hidden text must not count as violations');
 assert.ok(results('/bad').every(r=>!r.ok&&r.layout.documentOverflow.y&&r.fonts.violations.some(v=>v.size===13&&v.weight===500)&&r.consoleErrors.includes('fixture-console-error')));
 assert.ok(results('/bad').every(r=>r.fonts.violations.some(v=>v.size===13.98)),'fractional wrong sizes must not be rounded to a valid token');
 assert.ok(results('/clipped').every(r=>!r.ok&&r.layout.surfaces.some(s=>s.clipped.length)),'hidden clipping is still a real failure');
 assert.ok(results('/nested').every(r=>!r.ok&&r.layout.surfaces.some(s=>s.nested.length)));
 assert.equal(results('/large').find(r=>r.viewport.width===1920).ok,true);assert.equal(results('/large').find(r=>r.viewport.width===2560).ok,false);
 assert.ok(results('/click#click=展开>显示').every(r=>!r.ok&&r.fonts.violations.some(v=>v.text==='交互后字号错误')),'checks run after all click steps');
 for(const result of report.results){const png=readFileSync(result.screenshot);assert.equal(png.readUInt32BE(16),2560);assert.equal(png.readUInt32BE(20),1440);}
 assert.ok(allRoutes().includes('/admin/system/users'));assert.equal(new Set(allRoutes()).size,allRoutes().length);assert.ok(!slug('/a?b=c#click=显示').includes('/'));
 console.log(`Shot checks passed: real Chromium, two viewports, post-click/font/console/overflow/clipping failures. Evidence: ${out}`);
}finally{await new Promise(resolve=>server.close(resolve));}
