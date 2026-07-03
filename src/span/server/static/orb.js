/* SPAN centrale orb — geïnspireerd op dcyoung r3f-audio-visualizer (MIT).
   Vanilla three.js (r128 global), GPU-doelmachine: meerdere VORMEN + echte
   UnrealBloom-gloed, audio/state-reactief op SPAN.state + SPAN.micLevel.
   Vervangt de klassieke arc-reactor (fx.js) als SPAN._orbActive=true.
   Live instelbaar via SPAN.applyOrbConfig() (instellingen -> Orb). */
"use strict";
(() => {
  const SPAN = (window.SPAN = window.SPAN || {});
  if (SPAN._nebula) return;  // NEBULA-weergave actief -> klassieke orb slaapt
  const wrap = document.getElementById("reactor-wrap");
  const classic = document.getElementById("reactor");
  if (!wrap || !classic) return;
  const hasTHREE = typeof THREE !== "undefined";

  const PALETTES = {
    span:["#10204f","#1f7fae","#38e1ff","#bdf3ff","#ffffff","#ffe2a6","#ff9d5c"],
    cyaan:["#031b27","#0a4f6b","#38e1ff","#cffaff","#ffffff"],
    ijs:["#06243a","#0e5a8a","#39b6ff","#a7e8ff","#ffffff"],
    vuur:["#1a0500","#7a1500","#ff5a1e","#ffb000","#ffe98a","#ffffff"],
    paars:["#1a0936","#5a1e9a","#a06bff","#e08aff","#ffd6ff","#ffffff"],
    regenboog:["#ff004c","#ff9d00","#fff200","#22e36b","#19b6ff","#7a4bff","#ff2bd6"],
    cooltowarm:["#3b4cc0","#7b9ff9","#c0d4f5","#f2cbb7","#ee8468","#b40426"],
    zonsondergang:["#0d1b3e","#3b2f63","#9a3b8f","#ff6b6b","#ffb347","#ffe9a8"],
    natuur:["#04231a","#0a5a3c","#33b06a","#a7e8b0","#f0ffe0"],
    goud:["#1a1200","#5a3b00","#c8920f","#ffd27d","#fff2c8","#ffffff"],
    zee:["#00121f","#053a5a","#0a93b8","#4fd1c5","#c9fff0"],
    magma:["#000004","#3b0f70","#8c2981","#de4968","#fe9f6d","#fcfdbf"],
  };
  // GPU-doelmachine -> ruime defaults + bloom aan
  const DEFAULTS = { style:"orb", shape:"bol", cubes:1200, pulse:1.0, rotation:1.0,
                     cubeSize:0.05, radius:2.0, palette:"span", smooth:0.25, bloom:1.6 };

  function load(){ try { return Object.assign({},DEFAULTS,JSON.parse(localStorage.getItem("span_orb")||"{}")); }
    catch(e){ return Object.assign({},DEFAULTS); } }
  let cfg = load();
  SPAN._orbActive = false;

  let renderer, scene, cam, mesh, geo, mat, cv, composer, bloomPass, pts = [];
  const NB = 48; let bars = null;
  const m4 = hasTHREE ? new THREE.Matrix4() : null;
  let tint=null, tintAmt=0, flare=0, rot=0.12, t=0, raf=0;

  const hex = h => [parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)];
  function paletteColor(stops,x){ const R=stops.map(hex); x=Math.max(0,Math.min(1,x));
    const f=x*(R.length-1),i=Math.floor(f),k=f-i,a=R[i],b=R[Math.min(i+1,R.length-1)];
    return new THREE.Color((a[0]+(b[0]-a[0])*k)/255,(a[1]+(b[1]-a[1])*k)/255,(a[2]+(b[2]-a[2])*k)/255); }

  function buildCanvas(){
    cv=document.createElement("canvas"); cv.id="orb-canvas";
    cv.style.cssText="position:absolute;top:2px;left:50%;transform:translateX(-50%);"+
      "width:190px;height:190px;pointer-events:none;filter:drop-shadow(0 0 14px rgba(56,225,255,.55)) drop-shadow(0 0 36px rgba(56,225,255,.30))";
    wrap.insertBefore(cv, classic.nextSibling);
    renderer=new THREE.WebGLRenderer({canvas:cv,alpha:true,antialias:true});
    renderer.setPixelRatio(1); renderer.setSize(300,300,false);
    renderer.setClearColor(0x000000, 0);   // transparante achtergrond (geen vierkant)
    scene=new THREE.Scene();
    cam=new THREE.PerspectiveCamera(50,1,0.1,100);
    tint=new THREE.Color(0x38e1ff);
    // bloom-keten (alleen als de addons geladen zijn)
    try {
      if (THREE.EffectComposer && THREE.UnrealBloomPass) {
        composer=new THREE.EffectComposer(renderer);
        // RenderPass mét clearAlpha 0, anders tekent de composer een
        // ondoorzichtig (grijs) vierkant achter de orb i.p.v. transparant
        const rp=new THREE.RenderPass(scene,cam);
        rp.clearAlpha=0;
        composer.addPass(rp);
        bloomPass=new THREE.UnrealBloomPass(new THREE.Vector2(300,300), cfg.bloom, 0.9, 0.0);
        composer.addPass(bloomPass);
      }
    } catch(e){ composer=null; }
  }

  // --- vorm-generatoren: geven base-positie + verplaatsings-richting + band ---
  function makePoints(shape, N, R){
    const out=[]; const GA=Math.PI*(1+Math.sqrt(5));
    const rnd=(i)=> { const s=Math.sin(i*12.9898)*43758.5453; return s-Math.floor(s); };
    if (shape==="ring"){
      for(let i=0;i<N;i++){ const th=i/N*Math.PI*2, c=Math.cos(th), s=Math.sin(th);
        out.push({b:[c*R,s*R,0], n:[c,s,0], u:th/(Math.PI*2), seed:0.55+0.45*rnd(i), ph:i*0.5}); }
    } else if (shape==="golfvlak"){
      const side=Math.max(2,Math.round(Math.sqrt(N))); const span=R*2.6;
      for(let gx=0;gx<side;gx++) for(let gy=0;gy<side;gy++){
        const x=(gx/(side-1)-0.5)*span, z=(gy/(side-1)-0.5)*span;
        const d=Math.hypot(x,z)/(span*0.71);
        out.push({b:[x,0,z], n:[0,1,0], u:Math.min(1,d), seed:1, ph:(gx+gy)*0.4}); }
    } else if (shape==="helix"){
      for(let i=0;i<N;i++){ const tt=i/N, y=(tt-0.5)*R*3.0; const strand=i%2;
        const ang=tt*Math.PI*2*5 + strand*Math.PI; const rad=R*0.55;
        const c=Math.cos(ang), s=Math.sin(ang);
        out.push({b:[c*rad,y,s*rad], n:[c,0,s], u:tt, seed:1, ph:i*0.6}); }
    } else if (shape==="rooster"){
      const side=Math.max(2,Math.round(Math.cbrt(N))); const span=R*1.8;
      for(let x=0;x<side;x++) for(let y=0;y<side;y++) for(let z=0;z<side;z++){
        const px=(x/(side-1)-0.5)*span, py=(y/(side-1)-0.5)*span, pz=(z/(side-1)-0.5)*span;
        const L=Math.hypot(px,py,pz)||1;
        out.push({b:[px,py,pz], n:[px/L,py/L,pz/L], u:Math.min(1,L/(span*0.87)), seed:1, ph:(x+y+z)*0.5}); }
    } else if (shape==="spiraal"){
      const arms=3;
      for(let i=0;i<N;i++){ const tt=i/N, rad=R*Math.sqrt(tt)*1.5;
        const ang=tt*Math.PI*2*6 + (i%arms)*(Math.PI*2/arms);
        const c=Math.cos(ang), s=Math.sin(ang);
        out.push({b:[c*rad,(rnd(i)-0.5)*0.3,s*rad], n:[0,1,0], u:Math.min(1,rad/(R*1.5)), seed:0.7+0.3*rnd(i), ph:i*0.3}); }
    } else { // bol (default)
      for(let i=0;i<N;i++){ const k=i+0.5, phi=Math.acos(1-2*k/N), th=(GA*k)%(Math.PI*2);
        const ux=Math.cos(th)*Math.sin(phi), uy=Math.sin(th)*Math.sin(phi), uz=Math.cos(phi);
        out.push({b:[ux*R,uy*R,uz*R], n:[ux,uy,uz], u:phi/Math.PI, seed:1, ph:i*0.2}); }
    }
    return out.slice(0,N);
  }

  function buildMesh(){
    if(mesh){ scene.remove(mesh); geo.dispose(); mat.dispose(); }
    const N=Math.max(120,Math.min(2500,cfg.cubes|0)); const R=cfg.radius;
    cam.position.z=R*3.1;
    geo=new THREE.BoxGeometry(cfg.cubeSize,cfg.cubeSize,cfg.cubeSize);
    mat=new THREE.MeshBasicMaterial({toneMapped:false, transparent:true,
        blending:THREE.AdditiveBlending, depthWrite:false});
    mesh=new THREE.InstancedMesh(geo,mat,N); scene.add(mesh);
    pts=makePoints(cfg.shape,N,R);
    const stops=PALETTES[cfg.palette]||PALETTES.span;
    for(let i=0;i<pts.length;i++){ pts[i].band=Math.min(NB-1,(pts[i].u*NB)|0);
      mesh.setColorAt(i, paletteColor(stops, i/pts.length).multiplyScalar(2.2)); }  // lichter/beter zichtbaar
    // ongebruikte instances ver weg parkeren
    for(let i=pts.length;i<N;i++){ m4.makeTranslation(9999,9999,9999); mesh.setMatrixAt(i,m4); }
    mesh.instanceColor.needsUpdate=true;
    bars=new Float32Array(NB);
  }

  function updateBars(){
    const lvl=SPAN.micLevel||0, st=SPAN.state, sm=cfg.smooth||0.25;
    for(let b=0;b<NB;b++){ let target;
      if(st==="speaking") target=lvl*(0.45+0.55*Math.sin(t*6+b*0.7))*(0.6+0.4*Math.sin(t*1.3+b));
      else if(st==="listening") target=lvl*0.85*(0.5+0.5*Math.sin(t*3+b*0.5));
      else if(st==="busy") target=0.20*(0.5+0.5*Math.sin(t*5+b*1.1));
      else target=0.05*(0.5+0.5*Math.sin(t*0.8+b*0.4));
      bars[b]+=(Math.max(0,target)-bars[b])*sm; }
  }

  function frame(){
    if(!SPAN._orbActive){ raf=0; return; }
    t+=0.016; updateBars();
    const st=SPAN.state;
    const rotT=(st==="busy"?0.9:st==="speaking"?0.45:st==="listening"?0.28:0.12)*cfg.rotation;
    rot+=(rotT-rot)*0.05;
    const pulse=(0.5*cfg.pulse + flare*0.6); flare*=0.93;
    const R=cfg.radius;
    for(let i=0;i<pts.length;i++){ const p=pts[i];
      const disp=bars[p.band]*(0.7+0.3*Math.sin(p.ph+t))*p.seed;
      const d=disp*pulse*R*0.5;
      m4.makeTranslation(p.b[0]+p.n[0]*d, p.b[1]+p.n[1]*d, p.b[2]+p.n[2]*d);
      mesh.setMatrixAt(i,m4); }
    mesh.instanceMatrix.needsUpdate=true;
    mesh.rotation.y+=0.002+rot*0.004; mesh.rotation.x=0.22;
    tintAmt*=0.95; mat.color.setRGB(1,1,1).lerp(tint, tintAmt*0.75);
    // altijd direct renderen (transparant) — de bloom-composer tekende een
    // ondoorzichtig grijs vierkant achter de orb. Gloed komt nu van additieve
    // blending op de kubussen + de CSS-drop-shadow op het canvas.
    renderer.render(scene,cam);
    raf=requestAnimationFrame(frame);
  }
  function start(){ if(!raf) raf=requestAnimationFrame(frame); }

  function activate(on){
    SPAN._orbActive=!!on && hasTHREE;
    if(cv) cv.style.display=SPAN._orbActive?"block":"none";
    classic.style.visibility=SPAN._orbActive?"hidden":"visible";
    if(SPAN._orbActive) start();
  }

  SPAN.applyOrbConfig=(partial)=>{
    cfg=Object.assign({},cfg,partial||{});
    try{ localStorage.setItem("span_orb",JSON.stringify(cfg)); }catch(e){}
    if(!hasTHREE) return;
    if(!renderer) buildCanvas();
    const rebuild=!mesh || partial && (partial.cubes!==undefined||partial.palette!==undefined
      ||partial.cubeSize!==undefined||partial.radius!==undefined||partial.shape!==undefined);
    if(rebuild) buildMesh();
    activate(cfg.style==="orb");
  };
  SPAN.orbConfig=()=>Object.assign({},cfg);

  const _glitch=SPAN.glitch,_ok=SPAN.reactorOk,_flare=SPAN.flare;
  SPAN.glitch=function(){_glitch&&_glitch.apply(this,arguments); if(tint){tint.set(0xff5a5a);tintAmt=1;}};
  SPAN.reactorOk=function(){_ok&&_ok.apply(this,arguments); if(tint){tint.set(0x7dffb4);tintAmt=1;}};
  SPAN.flare=function(){_flare&&_flare.apply(this,arguments); flare=1;};

  if(hasTHREE && cfg.style==="orb") SPAN.applyOrbConfig({});
})();
