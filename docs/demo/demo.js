/* A backend for the real ANY-PRESENCE page, so the website can run the actual app.
   Searches and game info are real (App Store and VNDB directly, Steam and GOG through the proxy below).
   There is no Discord here: Start only fills in the pretend profile card. Everything else is kept in memory. */
(function(){
const Q=new URLSearchParams(location.search);
const now=()=>Math.floor(Date.now()/1000), sleep=ms=>new Promise(r=>setTimeout(r,ms));
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const _b={}, enc=s=>_b[s]||(_b[s]=URL.createObjectURL(new Blob([s],{type:"image/svg+xml"}))); // short blob: links read better in the icon box than long data: ones
const PAL=[["#3a2f5c","#d9a441"],["#16424a","#e0766a"],["#4a2a3c","#8fd0b0"],["#1f3a5f","#f0c36a"],["#2b4a2e","#e3b16a"],["#4a3320","#7fb0e0"],["#3b2a6b","#e87fa8"],["#2c2f3a","#6fd1c0"]];
const PROXY="https://any-presence.kagitreble.workers.dev/";   // Cloudflare Worker: browsers can't call Steam or GOG directly
const DAILY=500; // Worker calls per visitor per day; after that Steam and GOG are skipped and everything else keeps working
const day=()=>new Date().toISOString().slice(0,10);
function used(add){ try{ const k="ap-demo-calls", v=JSON.parse(localStorage.getItem(k)||"{}"), n=v.day===day()?v.n:0; if(add!==undefined) localStorage.setItem(k,JSON.stringify({day:day(),n:add===true?DAILY:n+add})); return n }catch(e){ return 0 } }
const jget=async(u,viaProxy)=>{ if(viaProxy){ if(used()>=DAILY) throw new Error("limit"); used(1) }
  const r=await fetch(viaProxy?PROXY+"?u="+encodeURIComponent(u):u); if(r.status===429) used(true); if(!r.ok) throw new Error("HTTP "+r.status); return r.json() };
const q=o=>new URLSearchParams(o).toString();
const tok=t=>String(t||"").toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu," ").trim().split(" ").filter(x=>x.length>1||/\d/.test(x));
const hits=(title,words)=>{ const t=tok(title).join(" "); return words.every(w=>t.includes(w)) };
const plain=(h,limit=3500)=>{ let t=String(h||"").replace(/<(script|style)[\s\S]*?<\/\1>/gi,"").replace(/<br\s*\/?>|<\/p>|<\/li>|<\/h\d>|<\/div>/gi,"\n").replace(/<li[^>]*>/gi,"• ");
  t=new DOMParser().parseFromString(t,"text/html").body.textContent||""; t=t.replace(/[ \t\r\f\v ]+/g," ").replace(/ ?\n ?/g,"\n").replace(/\n{3,}/g,"\n\n").trim();
  return t.length>limit?t.slice(0,limit).trimEnd()+"…":t };
const firstPara=(t,n=320)=>{ const p=String(t||"").split("\n\n")[0].replace(/\n/g," ").trim(); return p.length<=n?p:p.slice(0,n).replace(/\s\S*$/,"")+"…" };
const rows=(...p)=>p.filter(x=>x[1]); const join=(a,n=6)=>(a||[]).slice(0,n).filter(Boolean).join(", ");
const probe=urls=>new Promise(done=>{ let i=0; const next=()=>{ if(i>=urls.length) return done(null); const im=new Image(), u=urls[i++], t=setTimeout(next,3500); im.onload=()=>{clearTimeout(t);done(u)}; im.onerror=()=>{clearTimeout(t);next()}; im.src=u }; next() });
const SC="https://cdn.cloudflare.steamstatic.com/steam/apps/";
const adultCache={};
async function steamAdult(id){ if(id in adultCache) return adultCache[id];
  try{ const d=await jget(`https://store.steampowered.com/api/appdetails?${q({appids:id,filters:"content_descriptors"})}`,true); const e=d[id]||{};
    if(!e.success) return adultCache[id]=true; const ids=(e.data&&e.data.content_descriptors&&e.data.content_descriptors.ids)||[]; return adultCache[id]=ids.some(x=>x===3||x===4) }
  catch(e){ return true } }
async function searchSteam(term){
  const items=((await jget(`https://store.steampowered.com/api/storesearch/?${q({term,cc:"us",l:"english"})}`,true)).items||[]).slice(0,6);
  let hidden=0, shown=items;
  if(!cfg.nsfw){ const flags=await Promise.all(items.map(i=>steamAdult(i.id))); shown=items.filter((_,n)=>!flags[n]); hidden=items.length-shown.length }
  const out=await Promise.all(shown.map(async i=>({title:i.name,icon:await probe([`${SC}${i.id}/library_600x900.jpg`,`${SC}${i.id}/header.jpg`])||i.tiny_image,thumb:i.tiny_image,source:"Steam",url:`https://store.steampowered.com/app/${i.id}/`,ref:{s:"steam",id:String(i.id)}})));
  return {out,hidden} }
async function searchGog(term){
  const u="https://catalog.gog.com/v1/catalog?"+q({query:"like:"+term,limit:10,order:"desc:score",productType:"in:game,pack",countryCode:"US",locale:"en-US",currencyCode:"USD"});
  const w=tok(term), out=[];
  for(const p of (await jget(u,true)).products||[]){ if(!hits(p.title,w)) continue;
    out.push({title:p.title,icon:p.coverVertical||p.coverHorizontal,thumb:p.coverHorizontal||p.coverVertical,source:"GOG",url:p.storeLink||(p.slug?"https://www.gog.com/game/"+p.slug:null),ref:p.id?{s:"gog",id:String(p.id),name:p.title}:null}); if(out.length===6) break }
  return {out,hidden:0} }
const appName=n=>{ const h=String(n||"").trim().split(/\s[-–—]\s/)[0].trim(); return h.length>=3?h:String(n||"?").trim() };
async function searchApp(term){
  const w=tok(term), out=[];
  for(const it of (await jget("https://itunes.apple.com/search?"+q({term,entity:"software",country:"us",limit:15}))).results||[]){
    if(!(it.genres||[]).includes("Games")||!it.trackId) continue; const title=appName(it.trackName); if(!hits(it.trackName,w)&&!hits(title,w)) continue;
    out.push({title,icon:it.artworkUrl512||it.artworkUrl100,thumb:it.artworkUrl100||it.artworkUrl512,source:"App Store",url:(it.trackViewUrl||"").split("?")[0]||null,ref:{s:"appstore",id:String(it.trackId),name:title}}); if(out.length===6) break }
  return {out,hidden:0} }
const vndb=async body=>{ const r=await fetch("https://api.vndb.org/kana/vn",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); if(!r.ok) throw new Error("HTTP "+r.status); return r.json() };
async function searchVndb(term){
  const out=[]; let hidden=0;
  for(const v of (await vndb({filters:["search","=",term],fields:"id, title, image.url, image.thumbnail, image.sexual, image.violence",results:6})).results||[]){ const im=v.image||{};
    if(!cfg.nsfw&&((im.sexual||0)>0.5||(im.violence||0)>0.5)){ hidden++; continue }
    out.push({title:v.title||"?",icon:im.url,thumb:im.thumbnail||im.url,source:"VNDB",url:v.id?"https://vndb.org/"+v.id:null,ref:v.id?{s:"vndb",id:v.id}:null}) }
  return {out,hidden} }
const SRC=[["steam","Steam",searchSteam],["gog","GOG",searchGog],["appstore","App Store",searchApp],["vndb","VNDB",searchVndb]];

async function infoSteam(ref,title){
  const id=ref.id, page=`https://store.steampowered.com/app/${id}/`;
  const e=(await jget(`https://store.steampowered.com/api/appdetails?${q({appids:id,cc:"us",l:"english"})}`,true))[id]||{}, d=e.data;
  if(!e.success||!d||typeof d!=="object") return null;
  const adult=((d.content_descriptors||{}).ids||[]).some(x=>x===3||x===4), media=[];
  if(cfg.nsfw||!adult){
    (d.movies||[]).slice(0,6).forEach(m=>media.push({type:"video",name:m.name||"Trailer",thumb:m.thumbnail,src:(m.mp4||{}).max||(m.webm||{}).max,hls:m.hls_h264,page}));
    (d.screenshots||[]).slice(0,20).forEach(s=>{ if(s.path_full) media.push({type:"image",thumb:s.path_thumbnail||s.path_full,url:s.path_full}) }) }
  const pl=d.platforms||{};
  return {source:"Steam",title:d.name||title,url:page,cover:d.header_image,tagline:plain(d.short_description,400),about:plain(d.about_the_game,3500),reviews:[],
    scores:(d.metacritic||{}).score?[{name:"Metacritic",value:String(d.metacritic.score),url:null}]:[],
    details:rows(["Genres",join((d.genres||[]).map(g=>g.description))],["Developer",join(d.developers)],["Publisher",join(d.publishers)],["Release date",(d.release_date||{}).date],["Platforms",join([["windows","Windows"],["mac","macOS"],["linux","Linux"]].filter(x=>pl[x[0]]).map(x=>x[1]))]),
    tags:(d.categories||[]).slice(0,8).map(c=>c.description).filter(Boolean),media,adult,hidden:!!(adult&&!cfg.nsfw&&((d.movies||[]).length||(d.screenshots||[]).length)),ref} }
async function infoGog(ref,title){
  const name=ref.name||title, shot=(t,f)=>String(t).replace("{formatter}",f);
  const prods=(await jget("https://catalog.gog.com/v1/catalog?"+q({query:"like:"+name,limit:10,order:"desc:score",productType:"in:game,pack",countryCode:"US",locale:"en-US",currencyCode:"USD"}),true)).products||[];
  const p=prods.find(x=>String(x.id)===ref.id||x.slug===ref.id)||prods.find(x=>tok(x.title).join(" ")===tok(name).join(" "))||prods[0];
  const gid=p&&p.id?String(p.id):(/^\d+$/.test(ref.id)?ref.id:null); let extra={};
  if(gid){ try{ extra=await jget(`https://api.gog.com/products/${gid}?${q({expand:"description,screenshots,videos",locale:"en-US"})}`,true) }catch(e){} }
  if(!p&&!Object.keys(extra).length) return null; const P=p||{}, desc=extra.description||{}, lead=plain(desc.lead,1200), full=plain(desc.full,3500), media=[];
  (extra.videos||[]).slice(0,4).forEach(v=>{ if(String(v.video_url||"").startsWith("https://")&&v.thumbnail_url) media.push({type:"video",name:"Trailer",thumb:v.thumbnail_url,src:null,hls:null,page:v.video_url}) });
  let shots=(P.screenshots||[]).filter(t=>String(t).includes("{formatter}")); if(!shots.length) shots=(extra.screenshots||[]).map(s=>s.formatter_template_url).filter(t=>String(t).includes("{formatter}"));
  shots.slice(0,20).forEach(t=>media.push({type:"image",thumb:shot(t,"product_card_v2_mobile_slider_639"),url:shot(t,"ggvgl_2x")}));
  const os=[["windows","Windows"],["osx","macOS"],["linux","Linux"]].filter(x=>(P.operatingSystems||[]).includes(x[0])).map(x=>x[1]);
  return {source:"GOG",title:P.title||extra.title||title,url:P.storeLink||(P.slug?"https://www.gog.com/game/"+P.slug:null)||((extra.links||{}).product_card),cover:P.coverHorizontal||P.coverVertical,
    tagline:firstPara(lead),about:full&&full!==lead?(lead+"\n\n"+full).trim():(lead||full),reviews:[],scores:[],
    details:rows(["Genres",join((P.genres||[]).map(g=>g.name))],["Developer",join(P.developers)],["Publisher",join(P.publishers)],["Release date",String(P.releaseDate||(extra.release_date||"").slice(0,10)).replace(/\./g,"-")],["Platforms",join(os)]),
    tags:(P.features||[]).slice(0,8).map(f=>f.name).filter(Boolean),media,adult:false,hidden:false,ref} }
async function infoApp(ref,title){
  const it=((await jget("https://itunes.apple.com/lookup?"+q({id:ref.id,country:"us"}))).results||[])[0]; if(!it) return null;
  const about=plain(it.description,3500);
  return {source:"App Store",title:appName(it.trackName)||title,url:(it.trackViewUrl||"").split("?")[0]||null,cover:it.artworkUrl512,tagline:firstPara(about),about,reviews:[],
    scores:it.averageUserRating?[{name:"App Store rating",value:it.averageUserRating.toFixed(1)+" / 5",url:null}]:[],
    details:rows(["Genres",join(it.genres)],["Developer",it.artistName],["Release date",(it.releaseDate||"").slice(0,10)],["Price",it.formattedPrice]),tags:(it.genres||[]).slice(0,8),
    media:(it.screenshotUrls||[]).slice(0,12).map(u=>({type:"image",thumb:u,url:u})),adult:false,hidden:false,ref} }
const bb=t=>String(t||"").replace(/\[url=[^\]]*\]([\s\S]*?)\[\/url\]/gi,"$1").replace(/\[\/?(?:b|i|u|spoiler|quote|raw|code|url)[^\]]*\]/gi,"").trim();
async function infoVndb(ref,title){
  const v=((await vndb({filters:["id","=",ref.id],fields:"title, description, image.url, image.sexual, image.violence, screenshots.url, screenshots.thumbnail, screenshots.sexual, screenshots.violence, released, developers.name, rating",results:1})).results||[])[0]; if(!v) return null;
  const about=plain(bb(v.description),3500), im=v.image||{}, adult=(im.sexual||0)>0.5||(im.violence||0)>0.5;
  const shots=(v.screenshots||[]).filter(s=>cfg.nsfw||((s.sexual||0)<=0.5&&(s.violence||0)<=0.5)), hid=(v.screenshots||[]).length>shots.length;
  return {source:"VNDB",title:v.title||title,url:"https://vndb.org/"+v.id,cover:im.url,tagline:firstPara(about),about,reviews:[],
    scores:v.rating?[{name:"VNDB rating",value:(v.rating/10).toFixed(1)+" / 10",url:null}]:[],details:rows(["Released",v.released],["Developer",join((v.developers||[]).map(d=>d.name))]),tags:[],
    media:shots.slice(0,12).map(s=>({type:"image",thumb:s.thumbnail||s.url,url:s.url})),adult,hidden:hid,ref} }
const INFO={steam:infoSteam,gog:infoGog,appstore:infoApp,vndb:infoVndb};
function refFromUrl(u){ u=String(u||"").trim(); let m;
  if(m=u.match(/store\.steampowered\.com\/app\/(\d+)/)) return {s:"steam",id:m[1]};
  if(m=u.match(/apps\.apple\.com\/.*\/id(\d+)/)) return {s:"appstore",id:m[1]};
  if(m=u.match(/vndb\.org\/(v\d+)/)) return {s:"vndb",id:m[1]};
  return null }

const PROGS=[
 {name:"eldenring.exe",exe:"C:/Games/ELDEN RING/eldenring.exe",title:"ELDEN RING™"},
 {name:"DarkSoulsIII.exe",exe:"C:/Games/DS3/DarkSoulsIII.exe",title:"DARK SOULS III"},
 {name:"StardewValley.exe",exe:"C:/Games/Stardew Valley/StardewValley.exe",title:"Stardew Valley"},
 {name:"METAMORPHOSIS.exe",exe:"C:/Games/METAMORPHOSIS/METAMORPHOSIS.exe",title:"METAMORPHOSIS"},
 {name:"Spotify.exe",exe:"C:/Apps/Spotify.exe",title:"Spotify"},
 {name:"Code.exe",exe:"C:/Apps/Code.exe",title:"index.html - Visual Studio Code"}];
const ALLPROGS=PROGS.concat([{name:"svchost.exe",exe:"",title:""},{name:"explorer.exe",exe:"",title:""}]);
const progIcon=n=>{const c=PAL[(n.length*7)%PAL.length];return enc(`<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><rect rx='14' width='64' height='64' fill='${c[0]}'/><circle cx='32' cy='32' r='15' fill='${c[1]}'/></svg>`)};

const SOURCES={steam:"Steam",gog:"GOG",appstore:"App Store",vndb:"VNDB (visual novels)"};
const SCDN="https://cdn.cloudflare.steamstatic.com/steam/apps/";
const mk=(t,proc,hrs,ago,extra,id)=>({key:proc.toLowerCase(),title:t,icon:id?SCDN+id+"/library_600x900.jpg":null,extra:extra||null,buttons:id?[{label:"Get on Steam",url:"https://store.steampowered.com/app/"+id+"/"}]:[],proc,exe:"C:/Games/"+proc,launch:"C:/Games/"+proc,last:now()-ago,played:hrs*3600,ref:id?{s:"steam",id:String(id)}:null});
const cfg={app_id:"1234567890123456789",nsfw:false,show_timer:true,auto_stop:true,imgbb_key:"",version:"demo",check_updates:true,update_later:"",network:"auto",
 theme:Q.get("theme")||"rose",custom_theme:null,
 sources:{steam:true,gog:true,appstore:true,vndb:true},source_labels:SOURCES,
 running:false,started_at:null,auto_detect:true,close_action:"background",startup:true,startup_available:true,tray_available:true,
 apps:[{proc:"Spotify.exe",exe:"C:/Apps/Spotify.exe",title:"Spotify",icon:null,extra:"Listening to music",enabled:true,buttons:[]}],
 recent:[mk("Stardew Valley","StardewValley.exe",64,7200,"Day 12, Spring",413150),mk("DARK SOULS III","DarkSoulsIII.exe",31,90000,"Fighting a boss",374320),mk("ELDEN RING","eldenring.exe",12,260000,null,1245620)]};
cfg.gallery=[];
let program=null, lastPayload=null;
const push=(ev,d)=>{ if(window.onBackend) window.onBackend(ev,d) };
const tell=(on,p)=>{ try{ parent.postMessage({type:"presence",on,title:p&&p.title||"",extra:p&&p.extra||"",icon:p&&p.icon||"",buttons:(p&&p.buttons)||[],started:cfg.started_at},"*") }catch(e){} };
function record(p,proc){ // a game that starts goes to the top of My Games
  const key=(proc||p.title).toLowerCase(); let e=cfg.recent.find(r=>r.key===key);
  if(!e){ e={key,played:0}; }
  Object.assign(e,{title:p.title,icon:p.icon||null,extra:p.extra||null,buttons:p.buttons||[],proc:proc||null,exe:p.exe||null,launch:p.exe||p.launch||null,ref:p.ref||e.ref||null,last:now()});
  cfg.recent=[e].concat(cfg.recent.filter(r=>r!==e));
}
const clean=s=>String(s||"").replace(/\.exe$/i,"").replace(/([a-z])([A-Z])/g,"$1 $2");
const API={
 state:async()=>JSON.parse(JSON.stringify(cfg)),
 get_recent:async()=>cfg.recent, running_recent:async()=>[],
 check_update:async()=>({available:false}), install_update:async()=>({error:"This is a demo. Download the real app to try updates."}),
 set_setting:async(k,v)=>{ if(k==="nsfw"){ cfg.nsfw=!!v; return true } if(k.startsWith("source:")) cfg.sources[k.slice(7)]=!!v; else cfg[k]=v; return true },
 set_custom_theme:async(name,colors)=>{ cfg.custom_theme={name:name||"Custom",colors}; cfg.theme="custom"; return {ok:true,custom_theme:cfg.custom_theme} },
 search:async(t)=>{ const on=SRC.filter(x=>cfg.sources[x[0]]!==false);
   const res=await Promise.allSettled(on.map(x=>x[2](t))), results=[], failed=[]; let hidden=0;
   res.forEach((r,i)=>{ if(r.status==="fulfilled"){ results.push(...r.value.out); hidden+=r.value.hidden } else failed.push(on[i][1]) });
   return {results,hidden,failed,relaxed:[]} },
 programs:async(all)=>all?ALLPROGS:PROGS, program_icon:async(exe)=>progIcon(exe||"x"), browse:async()=>null,
 set_program:async(n)=>{ program=n||null; return n?clean(n):"" },
 apply_icon:async(src)=>({url:src}),
 start:async(p)=>{ await sleep(450); lastPayload=p; cfg.running=true; cfg.started_at=now(); record(p,p.proc||program); tell(true,p); setTimeout(()=>push("recent",cfg.recent),50); return {ok:true,started_at:cfg.started_at} },
 update:async(p)=>{ lastPayload=p; tell(true,p); return {ok:true} },
 stop:async()=>{ cfg.running=false; cfg.started_at=null; tell(false); return {ok:true} },
 save_app:async(e)=>{ e.enabled=true; const i=cfg.apps.findIndex(a=>a.proc.toLowerCase()===e.proc.toLowerCase()); let verb="Saved";
   if(i<0) cfg.apps.push(e); else { cfg.apps[i]=e; verb="Updated" } return {verb,apps:cfg.apps} },
 toggle_app:async(p,on)=>{ const a=cfg.apps.find(x=>x.proc===p); if(a) a.enabled=on; return true },
 delete_app:async(p)=>{ cfg.apps=cfg.apps.filter(a=>a.proc!==p); return cfg.apps },
 add_recent:async(p)=>{ const key=(p.proc||p.title).toLowerCase(), had=cfg.recent.some(r=>r.key===key);
   if(had){ const e=cfg.recent.find(r=>r.key===key); Object.assign(e,{title:p.title,extra:p.extra||null,buttons:p.buttons||[],icon:p.icon||e.icon}) }
   else { const e={key,title:p.title,icon:p.icon||null,extra:p.extra||null,buttons:p.buttons||[],proc:p.proc||null,exe:p.exe||null,launch:p.exe||null,ref:p.ref||null,last:0,played:0}; cfg.recent.splice(Math.min(1,cfg.recent.length),0,e) }
   return {added:!had,recent:cfg.recent} },
 remove_recent:async(k)=>{ cfg.recent=cfg.recent.filter(r=>r.key!==k); return cfg.recent },
 play_recent:async(k)=>{ await sleep(400); const e=cfg.recent.find(r=>r.key===k); if(!e) return {error:"That game isn't in the list any more."};
   e.last=now(); cfg.recent=[e].concat(cfg.recent.filter(r=>r!==e)); cfg.running=true; cfg.started_at=now();
   const p={title:e.title,icon:e.icon,extra:e.extra,buttons:e.buttons}; tell(true,p);
   return {ok:true,started_at:cfg.started_at,title:e.title,recent:cfg.recent} },
 set_game_ref:async(key,url)=>{ const ref=refFromUrl(url); if(!ref) return {error:"That link isn't a Steam, App Store or VNDB page."};
   const e=cfg.recent.find(r=>r.key===key); if(!e) return {error:"That game isn't in the list any more."}; e.ref=ref; e.ref_pinned=true; return API.game_info_ref(ref,e.title) },
 game_info:async(key)=>{ const e=cfg.recent.find(r=>r.key===key); return e?API.game_info_ref(e.ref,e.title):{error:"That game isn't in the list any more."} },
 game_info_ref:async(ref,title)=>{
   try{ if(!ref){ const f=await searchSteam(title); ref=f.out[0]&&f.out[0].ref } // no page remembered yet: use the best Steam match
     const fn=ref&&INFO[ref.s]; const d=fn?await fn(ref,title):null; return d||{none:true} }
   catch(e){ return {error:"Couldn't load the info. Try again in a moment."} } },
 set_banner:async()=>({ok:true}), banner_options:async()=>({}), banner_images:async()=>({}),
 open_link:async(u)=>{ try{ window.open(u,"_blank","noopener") }catch(e){} },
 minimize:async()=>{}, close:async()=>{}, remember_upload_choice:async()=>{}, gallery_remove:async()=>[]
};
window.pywebview={api:new Proxy(API,{get:(t,k)=>k in t?t[k]:async()=>({})})};
window.addEventListener("message",e=>{ if(e.source!==parent) return; const d=e.data||{}; if(d.type==="search"){ const q=document.getElementById("q"); if(q&&window.search){ q.value=d.q; window.search() } return } if(d.type==="theme"&&window.applyTheme){ try{ applyTheme(d.id); cfg.theme=d.id }catch(_){} } });
})();
