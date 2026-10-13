import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

test('comic painting fills pale gaps without exposing later objects or changing completed characters',async()=>{
 const archive=path.resolve(process.env.BOOMEARTH_TEST_ARCHIVE||`.internal/video-runtime-tests/${Date.now()}`);
 fs.mkdirSync(archive,{recursive:true});
 const browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,userDataDir:path.join(archive,'browser-profile'),args:['--no-sandbox']});
 try{
  const results=[];
  for(const repaired of [false,true]){
   const page=await browser.newPage();await page.setContent('<section id="scene-01"><img id="scene-01--draw-source"><canvas id="scene-01--draw-canvas"></canvas><i id="scene-01--pencil-cursor"></i></section>');
   await page.evaluate(()=>{window.installSpongeRegionDraw=()=>{};window.installSpongeInfoCards=()=>{};});
   await page.addScriptTag({path:path.resolve('video-content-template/assets/sponge-component-draw.js')});
   if(repaired)await page.addScriptTag({path:path.resolve('video-content-template/assets/vivid-comic-component-draw.js')});
   const result=await page.evaluate(async()=>{
    const source=document.getElementById('scene-01--draw-source'),native=document.createElement('canvas');native.width=80;native.height=60;const ctx=native.getContext('2d');
    ctx.fillStyle='#fff';ctx.fillRect(0,0,80,60);ctx.fillStyle='#ffbc18';ctx.fillRect(10,8,24,44);ctx.fillStyle='#185dcc';ctx.fillRect(48,15,20,32);ctx.fillStyle='rgb(239,244,250)';ctx.fillRect(63,21,4,20);
    source.src=native.toDataURL();await source.decode();
    const plan={parts:[{id:'human',kind:'character',weight:4,polygons:[[[.125,.133333],[.425,.133333],[.425,.866667]],[[.125,.133333],[.425,.866667],[.125,.866667]]]},{id:'prop',kind:'object',weight:2,polygons:[[[.6,.25],[.7875,.25],[.7875,.783333],[.6,.783333]]]}]};
    document.getElementById('scene-01').dataset.drawingPlan=JSON.stringify(plan);
    let target,update;const tl={fromTo(state,before,after){target=state;update=after.onUpdate;}};
    window.installSpongeRegionDraw(tl,[{id:'scene-01',start:0,end:12}],[],new Set(['scene-01']));
    const canvas=document.getElementById('scene-01--draw-canvas'),out=canvas.getContext('2d');
    const composite=()=>{const dst=out.getImageData(0,0,80,60).data;return Array.from(dst,(_,p)=>p%4===3?255:Math.round(dst[p]*dst[p-p%4+3]/255+255*(1-dst[p-p%4+3]/255)));};
    const seek=elapsed=>{target.elapsed=elapsed;update();return composite();};
    const qc=window.__spongePaintingQC['scene-01'];const blank=seek(0),body=seek(qc.schedule[0].end+.000001),final=seek(qc.duration-1e-9),rewound=seek(qc.schedule[0].end+.000001),src=ctx.getImageData(0,0,80,60).data;
    let missing=0,bodyMissing=0,lateProp=0,changedBody=0;
    for(let y=0;y<60;y++)for(let x=0;x<80;x++){
     const p=(y*80+x)*4;
     if([0,1,2].some(k=>Math.abs(final[p+k]-src[p+k])>3))missing++;
     if(x>=10&&x<34&&y>=8&&y<52){if([0,1,2].some(k=>Math.abs(body[p+k]-src[p+k])>3))bodyMissing++;if([0,1,2].some(k=>body[p+k]!==final[p+k]))changedBody++;}
     if(x>=48&&x<68&&y>=15&&y<47&&Math.min(body[p],body[p+1],body[p+2])<252)lateProp++;
    }
    return {missing,bodyMissing,lateProp,changedBody,blankIsWhite:blank.every(v=>v===255),rewindMatches:body.every((v,p)=>v===rewound[p]),qc};
   });
   results.push({repaired,...result});await page.close();
  }
  fs.writeFileSync(path.join(archive,'pixel-ownership-regression.json'),JSON.stringify(results,null,2));
  assert.ok(results[0].missing>0,'original renderer must reproduce omitted source pixels');
  assert.equal(results[1].missing,0,'complete pale prop fill must match original pixels');
  assert.equal(results[1].bodyMissing,0,'triangulated character must be whole when its component completes');
  assert.equal(results[1].lateProp,0,'future prop must remain hidden');
  assert.equal(results[1].changedBody,0,'later prop must not overwrite the completed character');
  assert.ok(results[1].blankIsWhite&&results[1].rewindMatches);
 }finally{await browser.close();}
});

test('semantic brush visits each island serially and paints pale interiors immediately throughout the stroke',async()=>{
 const archive=path.resolve(process.env.BOOMEARTH_TEST_ARCHIVE||`.internal/video-runtime-tests/${Date.now()}-progress`);
 fs.mkdirSync(archive,{recursive:true});
 const browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,userDataDir:path.join(archive,'browser-profile'),args:['--no-sandbox']});
 try{
  const page=await browser.newPage();
  await page.setContent('<section id="scene-01"><img id="scene-01--draw-source"><canvas id="scene-01--draw-canvas"></canvas><i id="scene-01--pencil-cursor"></i></section>');
  await page.evaluate(()=>{window.installSpongeRegionDraw=()=>{};});
  await page.addScriptTag({path:path.resolve('video-content-template/assets/vivid-comic-component-draw.js')});
  const result=await page.evaluate(async()=>{
   const w=320,h=210,native=document.createElement('canvas');native.width=w;native.height=h;
   const ctx=native.getContext('2d');ctx.fillStyle='rgb(253,253,253)';ctx.fillRect(0,0,w,h);
   const owners=new Uint8Array(w*h);owners.fill(1);
   const object=(x,y,width,height,colour,owner)=>{
    ctx.fillStyle='#102035';ctx.fillRect(x,y,width,height);ctx.fillStyle=colour;ctx.fillRect(x+3,y+3,width-6,height-6);
    for(let row=y;row<y+height;row++)owners.fill(owner,row*w+x,row*w+x+width);
   };
   object(18,18,92,176,'rgb(247,249,250)',1);
   object(180,18,98,79,'rgb(248,247,251)',1);
   // A disconnected pale edge lies inside the first object's brush footprint.
   // It must appear when that brush reaches it, not during a later island pass.
   object(113,50,8,25,'rgb(247,249,250)',1);
   object(166,128,138,68,'rgb(247,250,249)',2);
   const src=ctx.getImageData(0,0,w,h).data;
   const runs=[];
   for(let i=0;i<owners.length;){let end=i+1;while(end<owners.length&&owners[end]===owners[i])end++;const n=end-i;runs.push(n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255,owners[i]);i=end;}
   const hash='a'.repeat(64),entry={source_sha256:hash,width:w,height:h,parts:[{id:'character-with-separated-hand',kind:'character',weight:4},{id:'future-object',kind:'object',weight:2}],ownership_rle:btoa(String.fromCharCode(...runs))};
   window.__vividComicSemanticParts={'scene-01':entry};
   const source=document.getElementById('scene-01--draw-source');source.src=native.toDataURL();await source.decode();
   document.getElementById('scene-01').dataset.drawingPlan=JSON.stringify({source_sha256:hash,parts:[{id:'obsolete-polygon',weight:1,polygons:[]}]});
   let state,update;const tl={fromTo(target,before,after){state=target;update=after.onUpdate;}};
   const buildStart=performance.now();window.installSpongeRegionDraw(tl,[{id:'scene-01',start:0,end:12}],[],new Set(['scene-01']));
   const buildMs=performance.now()-buildStart,qc=window.__spongePaintingQC['scene-01'],debug=window.__vividComicPaintingDebug['scene-01'];
   const canvas=document.getElementById('scene-01--draw-canvas'),out=canvas.getContext('2d');
   const seek=elapsed=>{state.elapsed=elapsed;update();return out.getImageData(0,0,w,h).data;};
   const colourMatches=(dst,p)=>[0,1,2].every(k=>dst[p+k]===src[p+k])&&dst[p+3]===src[p+3];
   let previous=seek(0),changedPreviouslyRevealed=0,futureLeaks=0,missingFinished=0,paleAfterBrushMissing=0,partSamples=0;
   const samples=[];let paintMs=0;
   for(let partIndex=0;partIndex<qc.schedule.length;partIndex++){
    const schedule=qc.schedule[partIndex],layer=debug.layers[partIndex];
    for(const progress of [.001,.025,.05,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,.975,.9999,1]){
     const begin=performance.now(),pixels=seek(schedule.start+(schedule.end-schedule.start)*progress);paintMs+=performance.now()-begin;
     for(let p=0;p<w*h;p++){
      const at=p*4;
      if(previous[at+3]&&[0,1,2,3].some(k=>pixels[at+k]!==previous[at+k]))changedPreviouslyRevealed++;
      if(owners[p]>partIndex+1&&pixels[at+3])futureLeaks++;
      if(owners[p]<partIndex+1&&Math.min(src[at],src[at+1],src[at+2])<252&&!colourMatches(pixels,at))missingFinished++;
     }
     // Independent Canvas2D brush rasterization checks the fully swept interior,
     // rather than accepting the runtime's reveal-rank calculation as its oracle.
     for(const group of layer.groups){
      const brush=document.createElement('canvas');brush.width=layer.image.width;brush.height=layer.image.height;
      const b=brush.getContext('2d');b.strokeStyle='#fff';b.lineWidth=layer.width;b.lineCap='round';
      const limit=layer.length*progress;
      for(const stroke of layer.strokes.filter(stroke=>stroke.island===group.id)){
       if(limit<=stroke.start)break;
       const fraction=Math.max(0,Math.min(1,(limit-stroke.start)/(stroke.end-stroke.start)));
       b.beginPath();b.moveTo(...stroke.from);b.lineTo(stroke.from[0]+(stroke.to[0]-stroke.from[0])*fraction,stroke.from[1]);b.stroke();
      }
      const swept=b.getImageData(0,0,brush.width,brush.height).data;
      for(let p=0;p<layer.islands.length;p++)if(layer.sourcePixels[p]&&swept[p*4+3]===255){
       const sourceAt=((Math.floor(p/layer.image.width)+layer.y)*w+p%layer.image.width+layer.x)*4;
       if(Math.min(src[sourceAt],src[sourceAt+1],src[sourceAt+2])>=246&&!colourMatches(pixels,sourceAt))paleAfterBrushMissing++;
      }
     }
     samples.push({part:partIndex,progress});partSamples++;previous=pixels;
    }
   }
   const character=debug.layers[0],firstIsland=character.groups[0],firstSchedule=qc.schedule[0];
   const islandBoundary=seek(firstSchedule.start+(firstSchedule.end-firstSchedule.start)*firstIsland.end/character.length);
   let secondIslandPremature=0,firstIslandIncomplete=0;
   for(let p=0;p<character.islands.length;p++){
    const at=((Math.floor(p/character.image.width)+character.y)*w+p%character.image.width+character.x)*4;
    if(character.islands[p]===character.groups[1].id&&islandBoundary[at+3])secondIslandPremature++;
    if(character.islands[p]===firstIsland.id&&!colourMatches(islandBoundary,at))firstIslandIncomplete++;
   }
   let maximumEndSnap=0;
   for(const schedule of qc.schedule){
    const near=seek(schedule.start+(schedule.end-schedule.start)*.9999),end=seek(schedule.end+1e-9);
    let changed=0;for(let p=0;p<w*h;p++)if([0,1,2,3].some(k=>near[p*4+k]!==end[p*4+k]))changed++;
    maximumEndSnap=Math.max(maximumEndSnap,changed);
   }
   const middleTime=qc.schedule[1].start+(qc.schedule[1].end-qc.schedule[1].start)*.42;
   const middle=seek(middleTime);seek(qc.duration);const rewound=seek(middleTime);
   const final=seek(qc.duration);let maximumFinalDifference=0;
   for(let p=0;p<w*h;p++)for(let k=0;k<3;k++)maximumFinalDifference=Math.max(maximumFinalDifference,Math.abs((final[p*4+3]?final[p*4+k]:255)-src[p*4+k]));
   // The real GSAP tween quantizes state to six decimals. Even its maximum
   // positive rounding error must not expose a new owner at the exact boundary.
   let roundedBoundaryLeaks=0;
   for(let i=0;i<qc.schedule.length;i++){
    const boundary=seek(qc.schedule[i].start+.0000005);
    for(let p=0;p<w*h;p++)if(owners[p]>=i+1&&boundary[p*4+3])roundedBoundaryLeaks++;
   }
   const invalid=[];
   for(const [name,change] of [['hash',e=>e.source_sha256='b'.repeat(64)],['dimensions',e=>e.width++],['truncated-rle',e=>e.ownership_rle=btoa(String.fromCharCode(1,0,0,0,1))],['unknown-owner',e=>e.ownership_rle=btoa(String.fromCharCode((w*h)&255,((w*h)>>>8)&255,((w*h)>>>16)&255,0,3))]]){
    const bad=JSON.parse(JSON.stringify(entry));change(bad);window.__vividComicSemanticParts['scene-01']=bad;
    try{window.installSpongeRegionDraw(tl,[{id:'scene-01',start:0,end:12}],[],new Set(['scene-01']));invalid.push({name,rejected:false});}catch(error){invalid.push({name,rejected:true,message:String(error)});}
   }
   return{changedPreviouslyRevealed,futureLeaks,missingFinished,paleAfterBrushMissing,secondIslandPremature,firstIslandIncomplete,maximumEndSnap,maximumFinalDifference,roundedBoundaryLeaks,rewindMatches:middle.every((v,i)=>v===rewound[i]),partSamples,buildMs,paintMs,invalid,qc};
  });
  fs.writeFileSync(path.join(archive,'semantic-brush-progress-regression.json'),JSON.stringify(result,null,2));
  assert.equal(result.changedPreviouslyRevealed,0,'revealed pixels must never change while later strokes or parts are painted');
  assert.equal(result.futureLeaks,0,'an active brush must not reveal pixels owned by another object');
  assert.equal(result.roundedBoundaryLeaks,0,'GSAP rounding at a part boundary must not expose new ink');
  assert.equal(result.missingFinished,0,'all earlier objects must remain complete');
  assert.equal(result.paleAfterBrushMissing,0,'the swept pale interior must be source-coloured immediately');
  assert.equal(result.secondIslandPremature,0,'a second disconnected island waits for the first island to finish');
  assert.equal(result.firstIslandIncomplete,0,'the first island is complete before the next starts');
  assert.ok(result.maximumEndSnap<=3,`end-of-part colour snap must be absent, got ${result.maximumEndSnap} pixels`);
  assert.ok(result.maximumFinalDifference<=3,'only explicitly excluded near-white paper may differ from the source');
  assert.ok(result.rewindMatches,'seeking backward must reproduce exactly the same pixels');
  assert.ok(result.invalid.every(row=>row.rejected),'mismatched or incomplete semantic ownership must fail closed');
  assert.equal(result.qc.ownership,'explicit-semantic-rle-v2');
  assert.ok(result.qc.excludedNearWhitePixels>0);
 }finally{await browser.close();}
});
