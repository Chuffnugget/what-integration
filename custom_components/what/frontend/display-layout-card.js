/* WHAT display layout card. No dependencies, remote assets, or screen capture. */
const copy = value => JSON.parse(JSON.stringify(value));
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fields = ["id", "group", "x", "y", "width", "height", "rotation", "refresh"];
export const layoutOf = monitors => monitors.filter(m => m.enabled).map(m => Object.fromEntries(fields.filter(k => m[k] !== undefined).map(k => [k, m[k]])));
export const signature = monitors => JSON.stringify(monitors.map(m => [m.id, m.connected, m.enabled, ...fields.slice(1).map(k => m[k])]).sort((a,b) => a[0].localeCompare(b[0])));
// Match the Windows backend's refresh tolerance; disabled-mode telemetry is not an arrangement change.
export function sameArrangement(a,b) {
  const sorted=rows=>[...rows].sort((x,y)=>x.id.localeCompare(y.id));a=sorted(a);b=sorted(b);
  return a.length===b.length && a.every((m,i)=>{const n=b[i];
    if(m.id!==n.id || !!m.enabled!==!!n.enabled || !!m.connected!==!!n.connected)return false;
    return !m.enabled || (fields.filter(k=>k!=="refresh").every(k=>m[k]===n[k]) && Math.abs((m.refresh||0)-(n.refresh||0))<=0.05);
  });
}
export function normalise(monitors, primary) {
  const rows = layoutOf(monitors), origin = rows.find(m => m.id === primary);
  if (!origin) throw Error("Choose an active primary display.");
  const ox=origin.x,oy=origin.y;
  for (const row of rows) { row.x -= ox; row.y -= oy; }
  return rows;
}
export function validate(rows) {
  if (!rows.length || rows.length > 32) throw Error("Keep at least one display enabled.");
  const groups = new Map(), ids = new Set();
  for (const row of rows) {
    if (!/^[a-f0-9]{24}$/.test(row.id) || !/^[a-f0-9]{24}$/.test(row.group) || ids.has(row.id)) throw Error("Invalid display identity.");
    ids.add(row.id);
    if (![row.x,row.y,row.width,row.height,row.rotation].every(Number.isInteger) || Math.abs(row.x)>65536 || Math.abs(row.y)>65536 || row.width<1 || row.width>32768 || row.height<1 || row.height>32768 || row.rotation<1 || row.rotation>4) throw Error("Display geometry is outside supported bounds.");
    if (row.refresh !== undefined && (!Number.isFinite(row.refresh) || row.refresh<0 || row.refresh>10000)) throw Error("Invalid refresh rate.");
    const other=groups.get(row.group);
    if (other && ["x","y","width","height"].some(k=>row[k]!==other[k])) throw Error("Duplicated displays must share their position and size.");
    groups.set(row.group,row);
  }
  const distinct=[...groups.values()];
  if (distinct.filter(m=>m.x===0 && m.y===0).length!==1) throw Error("Choose one primary display.");
  for (let i=0;i<distinct.length;i++) for (let j=i+1;j<distinct.length;j++) {
    const a=distinct[i],b=distinct[j];
    if(a.x<b.x+b.width && b.x<a.x+a.width && a.y<b.y+b.height && b.y<a.y+a.height) throw Error("Screens overlap. Move them apart or explicitly choose Duplicate.");
  }
  return rows;
}
export function rotateGroup(monitors,id,steps) {
  const selected=monitors.find(m=>m.id===id);if(!selected)return;
  for(const m of monitors.filter(m=>m.enabled && m.group===selected.group)) {
    m.rotation=1+((m.rotation-1+steps)%4+4)%4;
    if(Math.abs(steps)%2)[m.width,m.height]=[m.height,m.width];
  }
}
export function snapPosition(monitors,id,x,y,threshold) {
  const m=monitors.find(row=>row.id===id);let bestX=threshold,bestY=threshold;
  const initialX=x,initialY=y;
  for(const other of monitors.filter(row=>row.enabled && row.group!==m.group)) {
    for(const candidate of [other.x,other.x+other.width,other.x-m.width,other.x+other.width-m.width])if(Math.abs(candidate-initialX)<bestX){bestX=Math.abs(candidate-initialX);x=candidate;}
    for(const candidate of [other.y,other.y+other.height,other.y-m.height,other.y+other.height-m.height])if(Math.abs(candidate-initialY)<bestY){bestY=Math.abs(candidate-initialY);y=candidate;}
  }
  return {x:Math.round(x),y:Math.round(y)};
}
class WhatDisplayLayoutCard extends HTMLElement {
  constructor(){super();this.attachShadow({mode:"open"});this._draft=[];this._selected="";this._dirty=false;this._pending=false;this._message="";this._conflict=false;}
  setConfig(config){if(!config.entity?.startsWith("sensor."))throw Error("Select the WHAT Display arrangement sensor.");if(this._config?.entity!==config.entity){this._draft=[];this._base="";this._dirty=false;this._conflict=false;}this._config={title:"Displays",...config};this.render();}
  getCardSize(){return 8;}
  static getStubConfig(hass){return {entity:Object.keys(hass.states).find(id=>id.startsWith("sensor.") && Array.isArray(hass.states[id].attributes.monitors) && "layout_editor" in hass.states[id].attributes) || "sensor.your_pc_display_arrangement"};}
  static getConfigElement(){return document.createElement("what-display-layout-editor");}
  set hass(hass){
    this._hass=hass;const state=hass.states[this._config?.entity];this._state=state;
    const monitors=state?.attributes.monitors || [],next=signature(monitors),changed=!sameArrangement(monitors,this._incomingMonitors||[]) || JSON.stringify(monitors.map(m=>[m.id,m.name,m.modes]))!==this._metadata;
    this._incomingMonitors=copy(monitors);this._metadata=JSON.stringify(monitors.map(m=>[m.id,m.name,m.modes]));
    this._incoming=next;
    if(!this._base || (!this._dirty && !this._drag && !this._pending && changed))this.reload(false);
    else if(changed && this._dirty && !this._pending)this._conflict=!sameArrangement(monitors,this._baseMonitors);
    if(this._pending && this._requestFinished && !state?.attributes.busy && !state?.attributes.pending && (this.requestedVisible() || !sameArrangement(monitors,this._baseMonitors)))this.finishReadback();
    const status=JSON.stringify([state?.state,state?.attributes.control_enabled,state?.attributes.busy,state?.attributes.pending,state?.attributes.layout_editor]);
    if(!this._drag){
      if(changed)this.render();
      else if(status!==this._status)this.updateStatus();
      this._status=status;
    }
  }
  connectedCallback(){this._observer=new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(Math.abs(width-(this._observedWidth||0))<1)return;this._observedWidth=width;if(!this._drag)this.render();});this._observer.observe(this);this.render();}
  disconnectedCallback(){this._observer?.disconnect();clearTimeout(this._readbackTimer);this._drag=null;}
  get editable(){const a=this._state?.attributes;return !!a && !["unavailable","unknown"].includes(this._state.state) && a.control_enabled===true && a.layout_editor===true && !a.pending && !this._pending;}
  get writable(){const a=this._state?.attributes;return !!a && !["unavailable","unknown"].includes(this._state.state) && a.control_enabled===true && a.layout_editor===true && !a.busy && !a.pending && !this._pending;}
  reload(draw=true){this._draft=copy(this._state?.attributes.monitors || []);this._baseMonitors=copy(this._draft);this._expected=layoutOf(this._draft);this._base=signature(this._draft);this._dirty=false;this._conflict=false;this._primary=this._draft.find(m=>m.enabled && m.primary)?.id || this._draft.find(m=>m.enabled)?.id;this._selected=this._draft.some(m=>m.id===this._selected)?this._selected:this._draft[0]?.id;this._message="";if(draw)this.render();}
  edit(action){if(!this.editable)return;action();this._dirty=true;this._message="";this.render();}
  group(id){const selected=this._draft.find(m=>m.id===id);return this._draft.filter(m=>m.enabled && m.group===selected?.group);}
  enable(id,x,y){const m=this._draft.find(m=>m.id===id);if(!m)return;m.enabled=true;m.group=m.id;m.width=m.width||1920;m.height=m.height||1080;m.rotation=m.rotation||1;m.x=Math.round(x??Math.max(0,...this._draft.filter(n=>n.enabled && n!==m).map(n=>n.x+n.width)));m.y=Math.round(y??0);if(!this._primary)this._primary=id;}
  disable(id){if(this._draft.filter(m=>m.enabled).length<=1){this._message="Keep at least one display enabled.";return;}const m=this._draft.find(m=>m.id===id);m.enabled=false;if(this._primary===id)this._primary=this._draft.find(n=>n.enabled)?.id;}
  viewport(){const active=this._draft.filter(m=>m.enabled);const width=Math.max(200,this.getBoundingClientRect().width-(this.getBoundingClientRect().width<=420?28:40)),height=300;const left=Math.min(0,...active.map(m=>m.x)),top=Math.min(0,...active.map(m=>m.y));const right=Math.max(1920,...active.map(m=>m.x+m.width)),bottom=Math.max(1080,...active.map(m=>m.y+m.height));const scale=Math.min((width-64)/(right-left),(height-64)/(bottom-top),.16);return {width,height,scale,x:(width-(right-left)*scale)/2-left*scale,y:(height-(bottom-top)*scale)/2-top*scale};}
  async apply(){
    if(!this.writable || this._conflict || !this._dirty)return;
    let rows;try{rows=validate(normalise(this._draft,this._primary));}catch(error){this._message=error.message;this.render();return;}
    this._requestedRows=rows;this._pending=true;this._requestFinished=false;this._message="Applying layout…";this.render();
    try{
      await this._hass.callService("what","apply_display_layout",{entity_id:this._config.entity,layout:rows,expected:this._expected});
      this._requestFinished=true;
      if(this.requestedVisible() || !sameArrangement(this._state?.attributes.monitors||[],this._baseMonitors))this.finishReadback();
      else {
        this._message="Waiting for Windows to report the applied layout…";
        this._readbackTimer=setTimeout(()=>{this._pending=false;this._conflict=true;this._message="Windows has not reported a new arrangement yet. Your draft is kept. Reload to check before applying again.";this.render();},10000);
      }
    }catch(error){this._pending=false;this._message=error.message || "Outcome unknown. Reload the preview before retrying.";this._conflict=true;}
    this.render();
  }
  updateStatus(){
    const apply=this.shadowRoot.querySelector('[data-action="apply"]');if(!apply){this.render();return;}
    for(const el of this.shadowRoot.querySelectorAll('[data-action]:not([data-action="reload"]):not([data-action="apply"]),[data-place],[data-move],[data-rotate],[data-field]:not([data-field="selected"]):not([data-field="relative"])'))el.disabled=!this.editable || (el.dataset.action==='primary' && this._selected===this._primary);
    apply.disabled=!this.writable||!this._dirty||this._conflict;
    apply.title=this.applyReason();this.shadowRoot.querySelector('.footer .message').textContent=this._message||this.applyReason();
  }
  requestedVisible(){
    const actual=layoutOf(this._state?.attributes.monitors||[]),wanted=this._requestedRows||[];
    return actual.length===wanted.length && wanted.every(row=>{const current=actual.find(m=>m.id===row.id);return current && fields.filter(k=>k!=="refresh").every(k=>current[k]===row[k]) && (!row.refresh || Math.abs(row.refresh-(current.refresh||0))<=0.05);});
  }
  finishReadback(){clearTimeout(this._readbackTimer);this._pending=false;this.reload(false);this._message="Layout applied. Showing the arrangement reported by Windows.";}
  applyReason(){
    if(this._pending)return this._message||"Applying layout…";
    if(this._conflict)return "Windows changed while you were editing. Reload before applying.";
    if(!this.editable)return this._state?.attributes.pending?"Resolve display recovery in WHAT on the PC first.":"Enable Displays and Home Assistant display control in WHAT; the PC must be connected.";
    if(this._state?.attributes.busy)return "Windows is checking or changing displays. Your draft is kept; Apply will become available shortly.";
    if(!this._dirty)return "No changes to apply.";
    try{validate(normalise(this._draft,this._primary));}catch(error){return error.message;}
    return "Ready to apply your arrangement.";
  }
  place(side){const m=this._draft.find(n=>n.id===this._selected),other=this._draft.find(n=>n.id===this._relative);
    if(!m||!other||!other.enabled||m.group===other.group)return;
    const x=side==='left'?other.x-m.width:side==='right'?other.x+other.width:other.x;
    const y=side==='above'?other.y-m.height:side==='below'?other.y+other.height:other.y;
    this.move(m.id,x-m.x,y-m.y);
  }
  render(){
    if(!this._config || this._drag)return;
    const v=this.viewport();this._view=v;const selected=this._draft.find(m=>m.id===this._selected);const inactive=this._draft.filter(m=>!m.enabled);
    const groups=[...new Set(this._draft.filter(m=>m.enabled).map(m=>m.group))].map(group=>this._draft.filter(m=>m.enabled && m.group===group));
    const neighbours=this._draft.filter(m=>m.enabled && m.group!==selected?.group);
    if(!neighbours.some(m=>m.id===this._relative))this._relative=neighbours[0]?.id;
    const disabled=this.editable?"":"disabled";const currentMode=selected?`${selected.width}x${selected.height}`:"";
    const modes=selected?.modes || [],rotated=selected && [2,4].includes(selected.rotation);
    const resolutions=[...new Set([currentMode,...modes.map(m=>`${rotated?m.height:m.width}x${rotated?m.width:m.height}`)])].filter(Boolean);
    const refreshes=[...new Set([selected?.refresh || 0,...modes.filter(m=>(rotated?m.height:m.width)===selected?.width && (rotated?m.width:m.height)===selected?.height).map(m=>m.refresh)])].sort((a,b)=>a-b);
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;color:var(--primary-text-color,#e8edf5);font:14px var(--paper-font-body1_-_font-family,system-ui)}*{box-sizing:border-box}ha-card{display:block;border-radius:16px;background:var(--ha-card-background,var(--card-background-color,#182230));overflow:hidden}.wrap{padding:20px}header{display:flex;align-items:center;justify-content:space-between;gap:10px}h2{font-size:21px;margin:0}p{color:var(--secondary-text-color,#9daec1);line-height:1.5;margin:8px 0 14px}.board{height:300px;position:relative;overflow:hidden;border:1px solid var(--divider-color,#344458);border-radius:12px;background:radial-gradient(circle,var(--divider-color,#344458) 1px,transparent 1px) 0 0/18px 18px;touch-action:pan-y}.screen{position:absolute;border:2px solid var(--primary-color,#59c7ef);border-radius:8px;background:color-mix(in srgb,var(--primary-color,#59c7ef) 16%,var(--card-background-color,#182230));display:flex;align-items:center;justify-content:center;flex-direction:column;padding:8px;cursor:grab;touch-action:none;user-select:none;min-width:28px;min-height:28px}.screen.selected{z-index:2;outline:3px solid color-mix(in srgb,var(--primary-color,#59c7ef) 35%,transparent);outline-offset:4px}.screen{container-type:inline-size;gap:4px}.screen strong{font-size:clamp(12px,18cqw,26px);line-height:1}.screen .monitor-name{font-size:clamp(10px,10cqw,15px);line-height:1.15;text-align:center;max-width:100%;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.screen .resolution{font-size:clamp(8px,8cqw,11px)}.screen small{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}.guide{position:absolute;pointer-events:none;border-color:var(--primary-color,#59c7ef);opacity:.65;z-index:3}.guide.vertical{top:0;bottom:0;border-left:1px dashed}.guide.horizontal{left:0;right:0;border-top:1px dashed}.rotate{position:absolute;right:-15px;top:-17px;border-radius:50%;width:32px;height:32px;padding:0;touch-action:none}.inactive{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.inactive button{touch-action:none}.details{margin-top:18px;padding-top:14px;border-top:1px solid var(--divider-color,#344458)}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}label{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--secondary-text-color,#9daec1)}input,select,button{font:inherit;color:var(--primary-text-color,#e8edf5);background:var(--secondary-background-color,#243446);border:1px solid var(--divider-color,#43536a);border-radius:8px;padding:9px;min-height:38px}select,input{width:100%;min-width:0}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}button:focus-visible,.screen:focus-visible{outline:2px solid var(--primary-color,#59c7ef);outline-offset:3px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.primary{background:var(--primary-color,#59c7ef);color:var(--text-primary-color,#10212c);font-weight:600}.message{padding:10px;border-radius:8px;background:var(--secondary-background-color,#243446);margin-top:12px;line-height:1.5}.note{font-size:12px}.footer{margin-top:16px;padding-top:14px;border-top:1px solid var(--divider-color,#344458);min-height:110px}.footer .message{background:none;margin:0;padding:0;min-height:44px}.placement{margin-top:14px;padding:12px;border-radius:8px;background:var(--secondary-background-color,#243446)}.chip{display:inline-block;font-size:11px;margin-left:5px}.empty{padding:30px;text-align:center}.wide{grid-column:1/-1}@media(max-width:420px){.wrap{padding:14px}.fields{gap:8px}.fields{grid-template-columns:1fr}.screen small{font-size:9px}}
    </style><ha-card><div class="wrap"><header><h2>${esc(this._config.title)}</h2><button data-action="reload" ${this._pending?"disabled":""}>${this._dirty?"Discard changes":"Reload"}</button></header>
      <p>Drag screens into place. Drag ↻ to rotate in 90° steps. Nothing changes on the PC until you apply.</p>
      <div class="board" aria-label="Monitor arrangement">${groups.map(group=>{const m=group.find(n=>n.id===this._selected)||group[0],numbers=group.map(n=>this._draft.indexOf(n)+1).join(" + ");return `<div class="screen ${group.some(n=>n.id===this._selected)?"selected":""}" role="button" tabindex="0" aria-label="Display ${esc(numbers)}: ${esc(m.name)}. Arrow keys move; R rotates." data-id="${m.id}" style="left:${v.x+m.x*v.scale}px;top:${v.y+m.y*v.scale}px;width:${m.width*v.scale}px;height:${m.height*v.scale}px"><strong>${numbers}</strong><span class="monitor-name" title="${esc(group.map(n=>n.name||"Display").join(" + "))}">${esc(group.map(n=>n.name||"Display").join(" + "))}</span><small class="resolution">${m.width} × ${m.height}</small>${group.some(n=>n.id===this._primary)?"<small>Primary</small>":""}<button class="rotate" data-rotate="${m.id}" aria-label="Rotate display ${esc(numbers)}" ${disabled}>↻</button></div>`;}).join("")}${!groups.length?'<p class="empty">No active display information</p>':""}</div>
      ${inactive.length?`<p class="note">Inactive displays - drag onto the desktop, or select and enable.</p><div class="inactive">${inactive.map(m=>`<button data-inactive="${m.id}">${this._draft.indexOf(m)+1} · ${esc(m.name)}</button>`).join("")}</div>`:""}
      ${selected?`<div class="details"><label>Selected display<select data-field="selected">${this._draft.map((m,i)=>`<option value="${m.id}" ${m.id===selected.id?"selected":""}>${i+1} · ${esc(m.name)}${m.enabled?"":" (inactive)"}</option>`).join("")}</select></label>
        <div class="actions">${!selected.enabled?`<button data-action="enable" ${disabled}>Enable / Extend desktop</button>`:`<button data-action="primary" ${disabled} ${selected.id===this._primary?"disabled":""}>${selected.id===this._primary?"Primary display":"Make primary"}</button><button data-action="rotate" ${disabled}>Rotate 90°</button><button data-action="disable" ${disabled}>Disable</button>${this.group(selected.id).length>1?`<button data-action="extend" ${disabled}>Stop duplicating</button>`:""}`}</div>
        ${selected.enabled?`${neighbours.length?`<div class="placement"><label>Place selected display next to<select data-field="relative">${neighbours.map(m=>`<option value="${m.id}" ${m.id===this._relative?"selected":""}>${esc(m.name)}</option>`).join("")}</select></label><div class="actions">${["left","right","above","below"].map(side=>`<button data-place="${side}" ${disabled}>${side[0].toUpperCase()+side.slice(1)}</button>`).join("")}</div></div>`:""}<div class="fields" style="margin-top:14px"><label>Resolution<select data-field="resolution" ${disabled}>${resolutions.map(value=>`<option value="${value}" ${value===currentMode?"selected":""}>${value.replace("x"," × ")}</option>`).join("")}</select></label><label>Refresh rate<select data-field="refresh" ${disabled}>${refreshes.map(value=>`<option value="${value}" ${value===(selected.refresh||0)?"selected":""}>${value?value+" Hz":"Driver default"}</option>`).join("")}</select></label><label>Orientation<select data-field="rotation" ${disabled}>${["Landscape","Portrait (90°)","Landscape flipped","Portrait (270°)"].map((label,i)=>`<option value="${i+1}" ${i+1===selected.rotation?"selected":""}>${label}</option>`).join("")}</select></label><label>Duplicate another display<select data-field="duplicate" ${disabled}><option value="">Choose source…</option>${this._draft.filter(m=>m.enabled && m.id!==selected.id).map(m=>`<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select></label></div><div class="actions" aria-label="Nudge display"><button data-move="-100,0" aria-label="Move left" ${disabled}>←</button><button data-move="0,-100" aria-label="Move up" ${disabled}>↑</button><button data-move="0,100" aria-label="Move down" ${disabled}>↓</button><button data-move="100,0" aria-label="Move right" ${disabled}>→</button></div>`:""}</div>`:""}
      <div class="footer"><div class="message" role="status" aria-live="polite">${esc(this._message||this.applyReason())}</div>
      <div class="actions"><button class="primary" data-action="apply" title="${esc(this.applyReason())}" ${!this.writable||!this._dirty||this._conflict?"disabled":""}>${this._pending?"Applying…":"Apply layout"}</button><span class="note">${this._dirty?"Unsaved arrangement":"Current Windows arrangement"}</span></div></div>
    </div></ha-card>`;
    this.shadowRoot.querySelectorAll('[data-action]').forEach(el=>el.onclick=()=>this.action(el.dataset.action));
    this.shadowRoot.querySelectorAll('[data-field]').forEach(el=>el.onchange=()=>this.field(el.dataset.field,el.value));
    this.shadowRoot.querySelectorAll('[data-place]').forEach(el=>el.onclick=()=>this.edit(()=>this.place(el.dataset.place)));
    this.shadowRoot.querySelectorAll('[data-move]').forEach(el=>el.onclick=()=>{const [x,y]=el.dataset.move.split(',').map(Number);this.edit(()=>this.move(this._selected,x,y));});
    this.shadowRoot.querySelectorAll('.screen,[data-inactive]').forEach(el=>el.onpointerdown=e=>this.pointerDown(e,el));
    this.shadowRoot.querySelectorAll('.screen').forEach(el=>el.onkeydown=e=>{const step=e.shiftKey?100:10,delta={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]}[e.key];this._selected=el.dataset.id;if(delta){e.preventDefault();this.edit(()=>this.move(this._selected,...delta));}else if(e.key.toLowerCase()==='r'){e.preventDefault();this.edit(()=>rotateGroup(this._draft,this._selected,1));}});
    this.shadowRoot.querySelectorAll('[data-rotate]').forEach(el=>el.onclick=e=>{e.stopPropagation();this._selected=el.dataset.rotate;this.edit(()=>rotateGroup(this._draft,this._selected,1));});
  }
  move(id,x,y){for(const m of this.group(id)){m.x+=x;m.y+=y;}}
  action(action){if(action==='reload'){this.reload();return;}if(action==='apply'){this.apply();return;}this.edit(()=>{const m=this._draft.find(n=>n.id===this._selected);if(!m)return;if(action==='enable')this.enable(m.id);if(action==='disable')this.disable(m.id);if(action==='primary')this._primary=m.id;if(action==='rotate')rotateGroup(this._draft,m.id,1);if(action==='extend'){m.enabled=false;this.enable(m.id);}});}
  field(field,value){if(field==='relative'){this._relative=value;return;}if(field==='selected'){this._selected=value;this.render();return;}this.edit(()=>{const m=this._draft.find(n=>n.id===this._selected);if(field==='rotation')rotateGroup(this._draft,m.id,Number(value)-m.rotation);if(field==='resolution'){const [w,h]=value.split('x').map(Number);for(const n of this.group(m.id)){n.width=w;n.height=h;n.refresh=0;}}if(field==='refresh')m.refresh=Number(value);if(field==='duplicate'){const source=this._draft.find(n=>n.id===value);if(source){m.group=source.group;m.x=source.x;m.y=source.y;m.width=source.width;m.height=source.height;m.enabled=true;if(this._primary===m.id)this._primary=source.id;}}});}
  pointerDown(event,element){
    if(event.button!==0)return;const id=element.dataset.id||element.dataset.inactive;this._selected=id;
    if(!this.editable){this.render();return;}
    const before=copy(this._draft),m=this._draft.find(n=>n.id===id),board=this.shadowRoot.querySelector('.board'),bounds=board.getBoundingClientRect(),v=this._view;
    this._drag={before,id,x:event.clientX,y:event.clientY,moved:false,wasInactive:!m.enabled,rotate:!!event.target.closest('[data-rotate]'),baseRotation:m.rotation,angle:Math.atan2(event.clientY-(bounds.top+v.y+(m.y+m.height/2)*v.scale),event.clientX-(bounds.left+v.x+(m.x+m.width/2)*v.scale)),bounds,v};
    element.setPointerCapture(event.pointerId);
    element.onpointermove=e=>{
      const d=this._drag;if(!d)return;const dx=e.clientX-d.x,dy=e.clientY-d.y;if(!d.moved && Math.hypot(dx,dy)<5)return;d.moved=true;
      this._draft=copy(d.before);const target=this._draft.find(n=>n.id===id);
      if(d.rotate){const angle=Math.atan2(e.clientY-(bounds.top+v.y+(target.y+target.height/2)*v.scale),e.clientX-(bounds.left+v.x+(target.x+target.width/2)*v.scale));rotateGroup(this._draft,id,Math.round((angle-d.angle)/(Math.PI/2)));}
      else {if(d.wasInactive)this.enable(id,(e.clientX-bounds.left-v.x)/v.scale-target.width/2,(e.clientY-bounds.top-v.y)/v.scale-target.height/2);const position=d.wasInactive?{x:target.x,y:target.y}:snapPosition(this._draft,id,target.x+dx/v.scale,target.y+dy/v.scale,12/v.scale);const rawX=target.x+dx/v.scale,rawY=target.y+dy/v.scale;board.querySelectorAll('.guide').forEach(el=>el.remove());
        if(!d.wasInactive)for(const [axis,snapped,raw] of [['x',position.x,rawX],['y',position.y,rawY]])if(Math.abs(snapped-raw)>.1){const guide=document.createElement('div');guide.className='guide '+(axis==='x'?'vertical':'horizontal');guide.style[axis==='x'?'left':'top']=((axis==='x'?v.x:v.y)+snapped*v.scale)+'px';board.append(guide);}
        const offsetX=position.x-target.x,offsetY=position.y-target.y;this.move(id,offsetX,offsetY);}
      const tile=this.shadowRoot.querySelector(`.screen[data-id="${id}"]`);if(tile){const current=this._draft.find(n=>n.id===id);Object.assign(tile.style,{left:(v.x+current.x*v.scale)+'px',top:(v.y+current.y*v.scale)+'px',width:current.width*v.scale+'px',height:current.height*v.scale+'px'});}else if(d.wasInactive){let ghost=this.shadowRoot.querySelector('.ghost');if(!ghost){ghost=document.createElement('div');ghost.className='screen ghost';ghost.textContent=String(this._draft.indexOf(target)+1);board.append(ghost);}Object.assign(ghost.style,{left:(v.x+target.x*v.scale)+'px',top:(v.y+target.y*v.scale)+'px',width:target.width*v.scale+'px',height:target.height*v.scale+'px'});}
    };
    const finish=(e,cancel)=>{const d=this._drag;if(!d)return;const inside=e.clientX>=bounds.left && e.clientX<=bounds.right && e.clientY>=bounds.top && e.clientY<=bounds.bottom;if(cancel || (d.wasInactive && !inside))this._draft=d.before;else if(d.moved){this._dirty=true;}else if(d.rotate){rotateGroup(this._draft,id,1);this._dirty=true;}this._drag=null;this._conflict=this._conflict || !sameArrangement(this._state?.attributes.monitors||[],this._baseMonitors);element.onpointermove=null;this.render();};
    element.onpointerup=e=>finish(e,false);element.onpointercancel=e=>finish(e,true);
  }
}
class WhatDisplayLayoutEditor extends HTMLElement {
  setConfig(config){this._config=config;this.render();}
  set hass(hass){this._hass=hass;this.render();}
  render(){if(!this._config||!this._hass)return;if(!this.shadowRoot)this.attachShadow({mode:'open'});const entities=Object.values(this._hass.states).filter(s=>s.entity_id.startsWith('sensor.') && Array.isArray(s.attributes.monitors));this.shadowRoot.innerHTML=`<label>Display arrangement sensor <select>${entities.map(s=>`<option value="${esc(s.entity_id)}" ${s.entity_id===this._config.entity?'selected':''}>${esc(s.attributes.friendly_name||s.entity_id)}</option>`).join('')}</select></label>`;this.shadowRoot.querySelector('select').onchange=e=>{this._config={...this._config,entity:e.target.value};this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:this._config},bubbles:true,composed:true}));};}
}
if(!customElements.get('what-display-layout-card'))customElements.define('what-display-layout-card',WhatDisplayLayoutCard);
if(!customElements.get('what-display-layout-editor'))customElements.define('what-display-layout-editor',WhatDisplayLayoutEditor);
window.customCards=window.customCards||[];if(!window.customCards.some(card=>card.type==='what-display-layout-card'))window.customCards.push({type:'what-display-layout-card',name:'WHAT display layout',description:'Arrange, rotate and enable Windows monitors visually.',preview:true});
