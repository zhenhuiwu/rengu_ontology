/** Cross-platform screenshots + real layout/font/console checks. See README. */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {checkLayout,checkFonts} from './shot-checks.mjs';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(import.meta.url);
export function allRoutes(){return [...new Set(['/',...[...fs.readFileSync(path.join(ROOT,'src/nav.ts'),'utf8').matchAll(/path: ['"]([^'"]+)['"]/g)].map(m=>m[1])])];}
export function slug(route){const value=(route.replace(/^\//,'')||'home').replaceAll('/','_').replaceAll('?','__').replaceAll('&','_').replaceAll('=','-').replaceAll(':','-');return value.replace(/[^\p{L}\p{N}_\-.]/gu,'_');}
function playwright(){
  try{return require('playwright');}catch{}
  const roots=[...((process.env.SHOT_NODE_MODULES||'').split(path.delimiter)),...((process.env.NODE_PATH||'').split(path.delimiter)),path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')];
  for(const root of roots.filter(Boolean)){try{return require(path.join(root,'playwright'));}catch{}}
  throw Error('缺少已安装的Playwright。请设置SHOT_NODE_MODULES为包含playwright的node_modules目录；脚本不会下载依赖或浏览器。');
}
function browserPath(chromium){
  if(process.env.SHOT_BROWSER_EXECUTABLE){if(fs.existsSync(process.env.SHOT_BROWSER_EXECUTABLE))return process.env.SHOT_BROWSER_EXECUTABLE;throw Error('SHOT_BROWSER_EXECUTABLE指向的浏览器不存在');}
  const expected=chromium.executablePath();if(fs.existsSync(expected))return expected;
  const cache=process.env.PLAYWRIGHT_BROWSERS_PATH || (process.platform==='win32'?path.join(os.homedir(),'AppData/Local/ms-playwright'):process.platform==='darwin'?path.join(os.homedir(),'Library/Caches/ms-playwright'):path.join(os.homedir(),'.cache/ms-playwright'));
  if(fs.existsSync(cache)){
    const dirs=fs.readdirSync(cache).filter(n=>/^chromium(?:_headless_shell)?-\d+$/.test(n)).sort((a,b)=>Number(b.split('-').at(-1))-Number(a.split('-').at(-1)));
    for(const dir of dirs)for(const suffix of ['chrome-headless-shell-win64/chrome-headless-shell.exe','chrome-headless-shell-linux64/chrome-headless-shell','chrome-headless-shell-mac-arm64/chrome-headless-shell','chrome-headless-shell-mac-x64/chrome-headless-shell','chrome-win64/chrome.exe','chrome-linux64/chrome','chrome-linux/chrome','chrome-mac/Chromium.app/Contents/MacOS/Chromium']){const candidate=path.join(cache,dir,suffix);if(fs.existsSync(candidate))return candidate;}
  }
  throw Error('未找到已安装的Chromium。请设置SHOT_BROWSER_EXECUTABLE；脚本不会下载浏览器。');
}
function outputPath(dir,route,stamp,viewport){
  const base=slug(route),suffix=viewport===1920?'':'__2560x1440';
  const escaped=base.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const old=new RegExp(`^${escaped}${suffix}(?:__\\d{4}_[0-9a-z]+)?\\.png$`);
  for(const name of fs.readdirSync(dir))if(old.test(name))fs.unlinkSync(path.join(dir,name));
  return path.join(dir,`${base}${suffix}__${stamp}.png`);
}
export async function main(args=process.argv.slice(2)){
  let base=process.env.SHOT_BASE||'http://localhost:5280',out=process.env.SHOT_OUTPUT||path.join(ROOT,'shots'),skip=false;const routes=[];
  for(let i=0;i<args.length;i++){
    const arg=args[i];
    if(arg==='--base'||arg==='--out'){const value=args[++i];if(!value)throw Error(`${arg}需要参数`);if(arg==='--base')base=value;else out=value;}
    else if(arg.startsWith('--base='))base=arg.slice(7);
    else if(arg.startsWith('--out='))out=arg.slice(6);
    else if(arg==='--skip-check')skip=true;
    else if(arg==='--all')routes.push(...allRoutes());
    else if(arg.startsWith('--'))throw Error(`未知参数：${arg}`);
    else routes.push(arg.startsWith('/')?arg:`/${arg}`);
  }
  if(!routes.length){console.log('npm run shot -- <路由...> | --all [--base URL] [--out DIR] [--skip-check]');return 2;}
  base=base.replace(/\/$/,'');out=path.resolve(out);fs.mkdirSync(out,{recursive:true});
  let commit='nogit';try{commit=execFileSync('git',['rev-parse','--short','HEAD'],{cwd:ROOT,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();}catch{}
  const now=new Date(),stamp=`${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}_${commit}`;
  const {chromium}=playwright(),executablePath=browserPath(chromium);
  const browser=await chromium.launch({headless:true,executablePath});
  const report={base,browser:await browser.version(),executablePath,skipped:skip,results:[]};
  try{
    for(const route of [...new Set(routes)]){
      const [routePath,clicks='']=route.split('#click=');
      for(const [width,height,scale] of [[1920,1080,4/3],[2560,1440,1]]){
        const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:scale,locale:'zh-CN'}),page=await context.newPage();
        const resources=[];page.on('response',response=>{if(response.status()>=400)resources.push({url:response.url(),status:response.status()});});page.on('requestfailed',request=>resources.push({url:request.url(),error:request.failure()?.errorText}));
        const errors=[],consoleEvents=[];page.on('console',message=>{if(message.type()==='error'||message.type()==='warning'&&message.text().startsWith('[objectRegistry]')){errors.push(message.text());consoleEvents.push({type:message.type(),text:message.text(),location:message.location()});}});page.on('pageerror',error=>errors.push(String(error)));
        const result={route,viewport:{width,height},consoleErrors:errors,consoleEvents,resourceFailures:resources};
        try{
          const response=await page.goto(base+routePath,{waitUntil:'networkidle',timeout:30000});
          if(response&&!response.ok())errors.push(`页面HTTP ${response.status()}`);
          await page.waitForTimeout(Number(process.env.SHOT_SETTLE_MS??800));
          for(const text of clicks.split('>').map(t=>t.trim()).filter(Boolean)){
            try{await page.getByText(text,{exact:true}).filter({visible:true}).first().click({timeout:5000});await page.waitForTimeout(600);}catch(error){errors.push(`点击“${text}”失败：${String(error).split('\n')[0]}`);}
          }
          await page.evaluate(()=>document.fonts.ready.then(()=>undefined));
          if(!skip){result.layout=await page.evaluate(checkLayout);result.fonts=await page.evaluate(checkFonts);}
        }catch(error){errors.push(String(error).split('\n')[0]);}
        result.screenshot=outputPath(out,route,stamp,width);
        try{await page.screenshot({path:result.screenshot});}catch(error){errors.push(`截图失败：${String(error).split('\n')[0]}`);}
        result.ok=errors.length===0&&(skip||result.layout?.ok&&result.fonts?.ok);
        report.results.push(result);console.log(`${result.ok?(skip?'SKIP':'PASS'):'FAIL'} ${width}×${height} ${route} 一屏:${skip?'-':result.layout?.ok?'通过':'失败'} 字号:${skip?'-':result.fonts?.ok?'通过':'失败'} 控制台:${errors.length} ${result.screenshot}`);
        if(!result.ok)console.log(JSON.stringify({layout:result.layout,fonts:result.fonts,errors,consoleEvents,resourceFailures:resources},null,2));
        await context.close();
      }
    }
  }catch(error){report.fatalError=String(error);console.error(report.fatalError);}
  finally{try{await browser.close();}catch(error){report.fatalError??=String(error);}}
  const reportPath=path.join(out,`shot-report__${now.toISOString().replace(/[:.]/g,'-')}_${commit}.json`);fs.writeFileSync(reportPath,JSON.stringify(report,null,2));console.log(`[检查证据] ${reportPath}`);
  return report.fatalError?2:report.results.every(result=>result.ok)?0:1;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().then(code=>{process.exitCode=code;}).catch(error=>{console.error(String(error));process.exitCode=2;});
