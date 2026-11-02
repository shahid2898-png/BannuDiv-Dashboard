/* Bannu Division Dashboard — offline cache.
   Page and data files: always try the network first (so figures are never stale),
   fall back to the last saved copy when there is no signal.
   Fonts, logos and map/PDF/PPT libraries: served from the saved copy (they never change). */
const V="bannu-v1", STATIC=/fonts\.(googleapis|gstatic)\.com|cdnjs\.cloudflare\.com|\/assets\//;
self.addEventListener("install",e=>self.skipWaiting());
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==V).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",e=>{
  const r=e.request; if(r.method!=="GET") return;
  const u=new URL(r.url);
  if(STATIC.test(r.url)){
    e.respondWith(caches.open(V).then(c=>c.match(r).then(hit=>hit||fetch(r).then(res=>{ if(res.ok||res.type==="opaque") c.put(r,res.clone()); return res; }))));
    return;
  }
  if(u.origin!==location.origin) return;
  if(/tile|arcgisonline/.test(r.url)) return;
  e.respondWith(fetch(r).then(res=>{ if(res.ok){ const cp=res.clone(); caches.open(V).then(c=>c.put(r,cp)); } return res; })
    .catch(()=>caches.open(V).then(c=>c.match(r,{ignoreSearch:true})).then(h=>h||Response.error())));
});
