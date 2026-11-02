/* Whole-page PDF: renders the dashboard page to an image and lays it out on landscape A4 pages.
   Used by the PDF button and the "Whole page as PDF" card menu (window.print() is blocked inside a published artifact). */
async function pagePDF(){
  const root=document.documentElement,body=document.body;
  const cs=getComputedStyle(body);
  let bg=cs.backgroundColor;if(!bg||/rgba\(0, 0, 0, 0\)|transparent/.test(bg))bg="#ffffff";
  const W=Math.max(root.clientWidth,body.scrollWidth),H=Math.max(root.scrollHeight,body.scrollHeight);
  const scale=Math.max(.55,Math.min(1.6,15000/H));
  let unsafe=[];
  const cv=await html2canvas(body,{scale,backgroundColor:bg,useCORS:!0,logging:!1,width:W,windowWidth:W,windowHeight:H,scrollX:0,scrollY:0,
    ignoreElements:e=>e.id==="toast"||(e.classList&&(e.classList.contains("modal-bg")||e.classList.contains("dl-menu")||e.classList.contains("sk"))),
    onclone:d=>{
      d.documentElement.classList.add("motion-off");d.body.classList.add("motion-off");
      d.querySelectorAll(".rv").forEach(n=>n.classList.add("in"));
      const st=d.createElement("style");
      st.textContent=".rail,.scrim,.tbtn,.dl-btn{display:none!important}.shell{grid-template-columns:minmax(0,1fr)!important}.rv{opacity:1!important;transform:none!important;filter:none!important}body:before,body:after{display:none!important}*{animation:none!important;transition:none!important}";
      d.head.appendChild(st);
      d.querySelectorAll("*").forEach(n=>{const p=d.defaultView.getComputedStyle(n);if(p.position==="sticky")n.style.position="static";else if(p.position==="fixed")n.style.display="none"});
      /* record vertical spans of blocks that must not be sliced through (cards, rows, charts, headings, text lines) */
      const top0=d.body.getBoundingClientRect().top,lim=(595-36)*(W/(842-36))*.9;
      d.querySelectorAll("body *").forEach(n=>{
        const r=n.getBoundingClientRect();if(r.height<8||r.width<8||r.height>lim)return;
        const tg=n.tagName;if(tg==="SCRIPT"||tg==="STYLE")return;
        unsafe.push([r.top-top0,r.bottom-top0]);
      });
    }});
    const {jsPDF}=window.jspdf;
  const pw=842,ph=595,m=18,iw=pw-2*m,pageHpx=Math.floor(cv.width*(ph-2*m)/iw);
  /* merge unsafe spans (canvas px), then choose each cut at the latest safe y within one page */
  const sp=unsafe.map(a=>[a[0]*scale,a[1]*scale]).sort((a,b)=>a[0]-b[0]),mg=[];
  for(const a of sp){const l=mg[mg.length-1];if(l&&a[0]<l[1]-1)l[1]=Math.max(l[1],a[1]);else mg.push([a[0],a[1]])}
  const inside=y=>{let lo=0,hi=mg.length-1;while(lo<=hi){const k=(lo+hi)>>1,g=mg[k];if(y<=g[0]+1)hi=k-1;else if(y>=g[1]-1)lo=k+1;else return g}return null};
  const pbg=(()=>{try{const p=cv.getContext('2d').getImageData(2,cv.height-3,1,1).data;return `rgb(${p[0]},${p[1]},${p[2]})`}catch(e){return bg}})();
  const cuts=[0];let pos=0;
  while(cv.height-pos>pageHpx){
    let y=pos+pageHpx,g,n=0;
    while((g=inside(y))&&n++<400)y=g[0];
    if(y<=pos+pageHpx*.35)y=pos+pageHpx; /* unbreakable tall block: hard cut */
    y=Math.floor(y);cuts.push(y);pos=y;
  }
  const blank=(()=>{try{const h=cv.height-pos;if(h>=pageHpx*.2||cuts.length<2)return false;const d=cv.getContext('2d').getImageData(0,pos,cv.width,h).data,b=cv.getContext('2d').getImageData(2,cv.height-3,1,1).data;for(let i=0;i<d.length;i+=4*37)if(Math.abs(d[i]-b[0])+Math.abs(d[i+1]-b[1])+Math.abs(d[i+2]-b[2])>24)return false;return true}catch(e){return false}})();
  if(!blank)cuts.push(cv.height);
  const pdf=new jsPDF({orientation:"landscape",unit:"pt",format:"a4",compress:!0});
  const pages=cuts.length-1;
  for(let i=0;i<pages;i++){
    const y0=cuts[i],sh=cuts[i+1]-y0;
    const c=document.createElement("canvas");c.width=cv.width;c.height=sh;
    const x=c.getContext("2d");x.fillStyle=pbg;x.fillRect(0,0,c.width,c.height);x.drawImage(cv,0,y0,cv.width,sh,0,0,cv.width,sh);
    if(i)pdf.addPage();
    pdf.addImage(c.toDataURL("image/jpeg",.9),"JPEG",m,m,iw,sh*iw/cv.width);
    pdf.setFontSize(7);pdf.setTextColor(120);pdf.text(`Bannu Division Dashboard · page ${i+1} of ${pages}`,pw-m,ph-6,{align:"right"});
  }
  const name=("Bannu Division - "+(document.title||"Dashboard").replace(/[\\/:*?"<>|]+/g," ")+" - "+new Date().toISOString().slice(0,10)+".pdf").replace(/\s+/g," ");
  await saveBlob(pdf.output("blob"),name);
  return pages;
}
