import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import puppeteer from 'puppeteer-core';
import sharp from 'sharp';
const [projectArg,outArg]=process.argv.slice(2);
if(!projectArg||!outArg)throw Error('Usage: node automation/scripts/qc_comic_semantic_process.mjs <project> <new-output-directory>');
const project=path.resolve(projectArg),out=path.resolve(outArg);
if(fs.existsSync(path.join(out,'process-qc.json')))throw Error('no clobber');
fs.mkdirSync(out,{recursive:true});
const server=http.createServer((req,res)=>{
 const rel=decodeURIComponent(req.url.split('?')[0]);const f=path.resolve(project,'.'+(rel==='/'?'/index.html':rel));
 if(!f.startsWith(project+path.sep)||!fs.existsSync(f)||!fs.statSync(f).isFile()){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',path.extname(f)==='.html'?'text/html; charset=utf-8':path.extname(f)==='.js'?'text/javascript':'application/octet-stream');res.end(fs.readFileSync(f));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
try{
 browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,userDataDir:path.join(out,'browser-profile'),args:['--no-sandbox','--disable-gpu']});
 const page=await browser.newPage();await page.setViewport({width:1920,height:1080});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'networkidle0',timeout:120000});
 const runtimeUrl=await page.evaluate(()=>[...document.scripts].map(s=>s.src).find(src=>new URL(src,location.href).pathname.endsWith('/vivid-comic-component-draw.js')));
 if(!runtimeUrl)throw Error('The page has no vivid-comic-component-draw.js runtime.');
 const runtimeAddress=new URL(runtimeUrl),runtimeFile=path.resolve(project,'.'+decodeURIComponent(runtimeAddress.pathname));
 if(runtimeAddress.origin!==new URL(page.url()).origin||!runtimeFile.startsWith(project+path.sep))throw Error('Runtime must be a local project resource.');
 await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));});
 const ids=await page.evaluate(()=>Object.keys(window.__spongePaintingQC||{}));const results=[];
 for(const id of ids){
  const result=await page.evaluate(id=>{
   const qc=window.__spongePaintingQC[id],semantic=window.__vividComicSemanticParts?.[id];
   if(!semantic)throw Error('missing semantic partition '+id);
   const srcImage=document.getElementById(id+'--draw-source'),canvas=document.getElementById(id+'--draw-canvas');
   const native=document.createElement('canvas');native.width=srcImage.naturalWidth;native.height=srcImage.naturalHeight;const nctx=native.getContext('2d',{willReadFrequently:true});nctx.fillStyle='#fff';nctx.fillRect(0,0,native.width,native.height);nctx.drawImage(srcImage,0,0);
   const src=nctx.getImageData(0,0,native.width,native.height).data,ctx=canvas.getContext('2d',{willReadFrequently:true});
   const raw=Uint8Array.from(atob(semantic.ownership_rle),c=>c.charCodeAt(0)),view=new DataView(raw.buffer),owners=new Uint8Array(native.width*native.height);let position=0;
   for(let a=0;a<raw.length;a+=5){const count=view.getUint32(a,true),owner=raw[a+4];owners.fill(owner,position,position+count);position+=count;}
   const pixels=[];for(let p=0;p<owners.length;p++)if(src[p*4+3]>127&&Math.min(src[p*4],src[p*4+1],src[p*4+2])<252)pixels.push(p);
   const tl=window.__timelines['boomearth-content-production'];const seek=t=>{tl.totalTime(t,false);window.__vividComicPaintingDebug[id].repaint();return ctx.getImageData(0,0,canvas.width,canvas.height).data;};
   const frames=[],checks=[];let previous=null;
   for(let i=0;i<qc.schedule.length;i++){
    const part=qc.schedule[i];
    for(const f of [0,.1,.25,.5,.75,.9,.99,.9999999,1]){
     const t=qc.start+part.start+(part.end-part.start)*f+(f===1?.000001:0),dst=seek(t);
     let missingCompleted=0,leakedFuture=0,changedRevealed=0,currentDrawn=0,currentTotal=0,partialPixel=0,unowned=0;
     for(const p of pixels){
      const a=p*4,owner=owners[p],actual=[0,1,2].map(k=>Math.round(dst[a+k]*dst[a+3]/255+255*(1-dst[a+3]/255)));
      const drawn=Math.min(...actual)<252,matches=actual.every((c,k)=>Math.abs(c-src[a+k])<=3);
      if(!owner){unowned++;continue;}
      if((owner<i+1||(f===1&&owner===i+1))&&!matches)missingCompleted++;
      if((owner>i+1||(f===0&&owner===i+1))&&drawn)leakedFuture++;
      if(owner===i+1){currentTotal++;if(drawn)currentDrawn++;}
      if(drawn&&!matches)partialPixel++;
      if(previous&&previous[a+3]===255&&Math.min(previous[a],previous[a+1],previous[a+2])<252&&[0,1,2,3].some(k=>previous[a+k]!==dst[a+k]))changedRevealed++;
     }
     checks.push({id:part.id,partIndex:i,fraction:f,time:t,missingCompleted,leakedFuture,changedRevealed,currentDrawn,currentTotal,partialPixel,unowned});
     if([.25,.75,1].includes(f))frames.push({id:part.id,partIndex:i,fraction:f,time:t,png:canvas.toDataURL()});
     previous=dst;
    }
   }
   const last=previous,randomTimes=[qc.start+qc.duration*.3,qc.start+qc.duration*.8,qc.start+qc.duration*.3];
   const first=seek(randomTimes[0]);seek(randomTimes[1]);const rewind=seek(randomTimes[2]);let seekMismatch=0;
   for(let a=0;a<first.length;a++)if(first[a]!==rewind[a])seekMismatch++;
   const blank=seek(qc.start-.00001);let initialInk=0;for(const p of pixels)if(blank[p*4+3]&&Math.min(blank[p*4],blank[p*4+1],blank[p*4+2])<252)initialInk++;
   return {id,checks,frames,schedule:qc.schedule,drawingStart:qc.start,drawingEnd:qc.start+qc.duration,seekMismatch,initialInk,pixelCount:pixels.length};
  },id);
  const tiles=[];
  for(const frame of result.frames){
   const base=`${id}-part-${String(frame.partIndex+1).padStart(2,'0')}-${Math.round(frame.fraction*100)}`,bytes=Buffer.from(frame.png.split(',')[1],'base64');
   fs.writeFileSync(path.join(out,base+'.png'),bytes);frame.file=base+'.png';delete frame.png;
   const tile=await sharp(bytes).flatten({background:'#fff'}).resize(480,270,{fit:'contain',background:'#fff'}).toBuffer();
   const title=`${id} / ${frame.id} / ${Math.round(frame.fraction*100)}%`;
   const label=Buffer.from(`<svg width="480" height="24"><rect width="480" height="24" fill="#eef1f5"/><text x="8" y="17" font-size="14" font-family="Arial">${title}</text></svg>`);
   const idx=tiles.length/2,left=(idx%3)*480,top=Math.floor(idx/3)*294;
   tiles.push({input:label,left,top},{input:tile,left,top:top+24});
  }
  await sharp({create:{width:1440,height:result.schedule.length*294,channels:3,background:'#fff'}}).composite(tiles).png().toFile(path.join(out,id+'-process-sheet.png'));
  result.failures=result.checks.filter(c=>c.missingCompleted||c.leakedFuture||c.changedRevealed||c.partialPixel||c.unowned);
  result.endSnaps=result.schedule.map((p,i)=>{const samples=result.checks.filter(c=>c.partIndex===i);const before=samples.find(c=>c.fraction===.9999999),end=samples.find(c=>c.fraction===1);return {id:p.id,pixels:end.currentDrawn-before.currentDrawn};});
  results.push(result);console.log(id,JSON.stringify({parts:result.schedule.length,failures:result.failures.length,initialInk:result.initialInk,seekMismatch:result.seekMismatch,endSnaps:result.endSnaps}));
 }
 const failures=results.filter(s=>s.failures.length||s.initialInk||s.seekMismatch||s.endSnaps.some(e=>e.pixels>8));
 const sha=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
 const report={status:errors.length||failures.length||ids.length===0?'FAIL':'PASS',scope:'each semantic part at nine progress points; completed pixels stable, no future reveal, no endpoint fill, deterministic rewind',html_sha256:sha(path.join(project,'index.html')),runtime_sha256:sha(runtimeFile),errors,sceneCount:ids.length,partCount:results.reduce((a,s)=>a+s.schedule.length,0),checkCount:results.reduce((a,s)=>a+s.checks.length,0),failures:failures.map(s=>s.id),scenes:results};
 fs.writeFileSync(path.join(out,'process-qc.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,scenes:report.sceneCount,parts:report.partCount,checks:report.checkCount,errors,failures:report.failures}));
 if(report.status!=='PASS')process.exitCode=1;
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
