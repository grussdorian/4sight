var graph = {nodes:{}, edges:[]};
var nodePositions = {};
var svgEl;
var viewX=0, viewY=0, viewW=1200, viewH=800, viewScale=1;
var draggingNode=null, dragOffX=0, dragOffY=0;
var selectedNode=null;
var portDrag=null, portLine=null;
var creatingKind=null;
var pendingEdge=null;
var pendingEdgeWeight="medium";
var NODE_W=150, NODE_H=52, PORT_R=6;

var COLORS={critical:"#dc2626",high:"#ea580c",medium:"#ca8a04",low:"#16a34a",none:"#9ca3af",
  edge:"#3b82f6",bg:"#f8f9fa",text:"#1f2937",textDim:"#6b7280",cardBg:"#ffffff",cardStroke:"#d1d5db"};

function sevColor(s){return COLORS[s]||COLORS.none;}

function init(){
  svgEl=document.getElementById("svg-canvas");
  svgEl.addEventListener("wheel",onWheel,{passive:false});
  svgEl.addEventListener("mousedown",onMouseDown);
  svgEl.addEventListener("mousemove",onMouseMove);
  svgEl.addEventListener("mouseup",onMouseUp);
  svgEl.addEventListener("contextmenu",onContextMenu);
  window.addEventListener("resize",resizeSVG);
  resizeSVG();
  loadGraph();
}

function resizeSVG(){
  var rect=svgEl.parentElement.getBoundingClientRect();
  viewW=Math.max(rect.width,600); viewH=Math.max(rect.height,400);
  updateViewBox();
}
function updateViewBox(){
  svgEl.setAttribute("viewBox",viewX+" "+viewY+" "+viewW+" "+viewH);
  svgEl.setAttribute("width","100%"); svgEl.setAttribute("height","100%");
  svgEl.style.width="100%"; svgEl.style.height="100%"; svgEl.style.display="block";
}

function toSVG(mx,my){
  var pt=svgEl.createSVGPoint(); pt.x=mx; pt.y=my;
  var ctm=svgEl.getScreenCTM();
  if(!ctm) return null;
  var svgp=pt.matrixTransform(ctm.inverse());
  return {x:svgp.x, y:svgp.y};
}

function onWheel(e){
  e.preventDefault();
  if(e.ctrlKey||e.metaKey){
    var p=toSVG(e.clientX,e.clientY); if(!p) return;
    var ds=e.deltaY>0?0.9:1.1;
    var newScale=Math.max(0.3,Math.min(3,viewScale*ds));
    var cx=viewX+viewW/2, cy=viewY+viewH/2;
    viewW=viewW*viewScale/newScale; viewH=viewH*viewScale/newScale;
    viewScale=newScale;
    viewX=cx-viewW/2; viewY=cy-viewH/2;
    updateViewBox();
  }
}

async function loadGraph(){
  var r=await fetch("/builder/graph"); var d=await r.json();
  d.nodes.forEach(function(n){graph.nodes[n.id]=n;});
  graph.edges=d.edges.map(function(e){return {src:e.src, dst:e.dst, type:e.type, weight:e.weight||"medium"};});
  if(Object.keys(nodePositions).length===0){
    layoutGraph();
  }
  render();
}

// --- Topological layout ---
function layoutGraph(){
  var allNids=Object.keys(graph.nodes);
  if(allNids.length===0) return;
  var layers={};
  allNids.forEach(function(nid){
    var hasOutgoing=(graph.edges||[]).some(function(e){return e.src===nid;});
    if(!hasOutgoing) layers[nid]=0;
  });
  if(Object.keys(layers).length===0) layers[allNids[0]]=0;
  var changed=true;
  while(changed){ changed=false;
    allNids.forEach(function(nid){
      if(layers[nid]!==undefined) return;
      var maxSrc=-1, allDone=true;
      (graph.edges||[]).forEach(function(e){
        if(e.src===nid && layers[e.dst]!==undefined){
          maxSrc=Math.max(maxSrc,layers[e.dst]);
        }else if(e.src===nid && layers[e.dst]===undefined){
          allDone=false;
        }
      });
      if(allDone&&maxSrc>=0){layers[nid]=maxSrc+1;changed=true;}
    });
  }
  allNids.forEach(function(nid){if(layers[nid]===undefined) layers[nid]=0;});
  var layerGroups={};
  allNids.forEach(function(nid){
    var l=layers[nid];
    if(!layerGroups[l]) layerGroups[l]=[];
    layerGroups[l].push(nid);
  });
  var keys=Object.keys(layerGroups).map(Number).sort(function(a,b){return a-b;});
  var spacing=200, rowH=120, startY=80;
  keys.forEach(function(l){
    var nodes=layerGroups[l];
    var totalW=nodes.length*spacing;
    var startX=Math.max(60,(600-totalW)/2);
    nodes.forEach(function(nid,i){
      nodePositions[nid]={x:startX+i*spacing, y:startY+l*rowH};
    });
  });
  var maxX=0,maxY=0;
  allNids.forEach(function(nid){
    var p=nodePositions[nid]; if(!p) return;
    maxX=Math.max(maxX,p.x+NODE_W); maxY=Math.max(maxY,p.y+NODE_H);
  });
  viewW=Math.max(maxX+120,600); viewH=Math.max(maxY+120,400);
  viewX=-60; viewY=-20;
  updateViewBox();
}

// --- Event handlers ---
function getNodeFromEvent(e){var el=e.target.closest('[data-node]');return el?el.getAttribute("data-node"):null;}

function onMouseDown(e){
  var p=toSVG(e.clientX,e.clientY); if(!p) return;
  if(e.target.classList.contains("node-port")){
    portDrag={from:e.target.getAttribute("data-node"),x:p.x,y:p.y};
    portLine=document.createElementNS("http://www.w3.org/2000/svg","line");
    portLine.setAttribute("x1",p.x);portLine.setAttribute("y1",p.y);
    portLine.setAttribute("x2",p.x);portLine.setAttribute("y2",p.y);
    portLine.setAttribute("stroke","#1d4ed8");portLine.setAttribute("stroke-width","2");
    portLine.setAttribute("stroke-dasharray","4 2");
    svgEl.appendChild(portLine); svgEl.classList.add("dragging"); return;
  }
  var nid=getNodeFromEvent(e);
  if(nid&&nodePositions[nid]){
    draggingNode=nid; var np=nodePositions[nid];
    dragOffX=p.x-np.x; dragOffY=p.y-np.y;
    selectNode(nid); svgEl.classList.add("dragging"); return;
  }
  selectNode(null);
}

function onMouseMove(e){
  var p=toSVG(e.clientX,e.clientY); if(!p) return;
  if(draggingNode){
    nodePositions[draggingNode]={x:p.x-dragOffX,y:p.y-dragOffY}; render();
  }else if(portDrag&&portLine){
    portLine.setAttribute("x2",p.x);portLine.setAttribute("y2",p.y);
    var snap=findSnapNode(e.clientX,e.clientY);
    if(snap&&snap!==portDrag.from){
      var np=nodePositions[snap]; if(!np) return;
      portLine.setAttribute("x2",np.x+NODE_W/2);portLine.setAttribute("y2",np.y+NODE_H/2);
    }
  }
}

function onMouseUp(e){
  if(draggingNode){draggingNode=null;svgEl.classList.remove("dragging");}
  if(portDrag){
    var snap=findSnapNode(e.clientX,e.clientY);
    if(snap&&snap!==portDrag.from){
      // The node you drag FROM is always the edge source. For a leaf (data
      // source) that means data flows leaf -> consumer; for a task it means
      // task -> target. Same direction either way.
      addEdge(portDrag.from, snap);
    }
    if(portLine){portLine.remove();portLine=null;}
    portDrag=null;svgEl.classList.remove("dragging");render();
  }
}

var contextNodeId=null;
function onContextMenu(e){
  e.preventDefault(); var nid=getNodeFromEvent(e); if(!nid) return;
  contextNodeId=nid;
  var menu=document.createElement("div");
  menu.style.cssText="position:fixed;background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:4px 0;z-index:300;min-width:200px;box-shadow:0 4px 12px rgba(0,0,0,0.15);";
  menu.style.left=e.clientX+"px";menu.style.top=e.clientY+"px";
  menu.innerHTML=
    '<div style="padding:8px 16px;cursor:pointer;font-size:13px;">'+nid+'</div>'+
    '<div style="padding:8px 16px;cursor:pointer;font-size:13px;color:#3b82f6;">Add edge from...</div>';
  menu.children[1].onclick=function(){
    var target=prompt("Create edge FROM node (ID):");
    if(target&&graph.nodes[target]&&target!==nid) addEdge(target,nid);
    menu.remove();
  };
  document.body.appendChild(menu);
  setTimeout(function(){menu.remove();},5000);
  document.addEventListener("click",function rm(){menu.remove();document.removeEventListener("click",rm);},{once:true});
}

function findSnapNode(mx,my){
  var p=toSVG(mx,my); if(!p) return null;
  var best=null,bestDist=60;
  Object.keys(nodePositions).forEach(function(nid){
    var np=nodePositions[nid];
    var dx=p.x-(np.x+NODE_W/2),dy=p.y-(np.y+NODE_H/2);
    var dist=Math.sqrt(dx*dx+dy*dy);
    if(dist<bestDist){bestDist=dist;best=nid;}
  });
  return best;
}

// --- Edge helpers ---
function neighborsOf(nid){
  var nb={};
  (graph.edges||[]).forEach(function(e){
    if(e.src===nid) nb[e.dst]=true;
    if(e.dst===nid) nb[e.src]=true;
  });
  return Object.keys(nb);
}

function addEdge(fromId,toId){
  var exists=(graph.edges||[]).some(function(e){return e.src===fromId&&e.dst===toId;});
  if(exists) return;
  pendingEdge={from:fromId, to:toId};
  pendingEdgeWeight="medium";
  showWeightPicker(fromId, toId);
}

function showWeightPicker(fromId, toId){
  removeWeightPicker();
  var fromTitle=(graph.nodes[fromId]&&graph.nodes[fromId].title)||fromId;
  var toTitle=(graph.nodes[toId]&&graph.nodes[toId].title)||toId;
  var div=document.createElement("div");
  div.className="weight-picker";
  div.id="weight-picker-popup";
  var weights=["critical","high","medium","low"];
  var html='<div style="padding:8px 16px;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb;">'+
    esc(fromTitle)+' &rarr; '+esc(toTitle)+'</div>';
  weights.forEach(function(w){
    html+='<div class="w-row'+(w===pendingEdgeWeight?" selected":"")+
      '" onclick="selectWeight(\''+w+'\')">'+
      w.charAt(0).toUpperCase()+w.slice(1)+(w==="medium"?" (default)":"")+
      '</div>';
  });
  html+='<div class="w-actions">'+
    '<button onclick="cancelWeightPicker()">Cancel</button>'+
    '<button class="confirm" onclick="confirmWeightPicker()">Confirm</button>'+
    '</div>';
  div.innerHTML=html;
  div.style.left=Math.min(window.innerWidth-200, Math.max(100, window.innerWidth/2-90))+"px";
  div.style.top=Math.min(window.innerHeight-160, Math.max(100, window.innerHeight/2-80))+"px";
  document.body.appendChild(div);
  document.addEventListener("click",function rm(e){
    if(!e.target.closest("#weight-picker-popup")){
      cancelWeightPicker();
      document.removeEventListener("click",rm);
    }
  });
}

function selectWeight(w){
  pendingEdgeWeight=w;
  var rows=document.querySelectorAll("#weight-picker-popup .w-row");
  rows.forEach(function(r){ r.classList.toggle("selected", r.textContent.trim().toLowerCase().startsWith(w)); });
}

function confirmWeightPicker(){
  if(!pendingEdge) return;
  var fromId=pendingEdge.from, toId=pendingEdge.to;
  graph.edges.push({src:fromId, dst:toId, type:"dependency", weight:pendingEdgeWeight});
  fetch("/builder/edges",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({src:fromId, dst:toId, type:"dependency", weight:pendingEdgeWeight})});
  removeWeightPicker();
  pendingEdge=null;
  layoutGraph(); render();
}

function cancelWeightPicker(){
  removeWeightPicker();
  pendingEdge=null;
}

function removeWeightPicker(){
  var el=document.getElementById("weight-picker-popup");
  if(el) el.remove();
}

// --- Render ---
function render(){
  var highlight={};
  if(selectedNode){
    highlight[selectedNode]=true;
    neighborsOf(selectedNode).forEach(function(n){highlight[n]=true;});
  }
  var anySelected=selectedNode!=null;

  var html='';
  var drawn={};
  (graph.edges||[]).forEach(function(e){
    var key=e.src+"-"+e.dst; if(drawn[key]) return; drawn[key]=true;
    var from=nodePositions[e.src],to=nodePositions[e.dst];
    if(!from||!to) return;
    var active=anySelected?(highlight[e.src]&&highlight[e.dst]):true;
    var weight=e.weight||"medium";
    var widths={critical:3, high:2, medium:1.5, low:1};
    var opacities={critical:1, high:0.8, medium:0.6, low:0.35};
    var w=widths[weight]||1.5;
    var op=active?opacities[weight]||0.6:0.15;
    var dash=weight==="low"?"4 3":"none";
    var strokeCol=active?COLORS.edge:"#d1d5db";
    var x1=from.x+NODE_W/2,y1=from.y+NODE_H;
    var x2=to.x+NODE_W/2,y2=to.y;
    var dy=Math.max(Math.abs(y2-y1)/3,20);
    var d="M"+x1+" "+y1+" C"+x1+" "+(y1-dy)+" "+x2+" "+(y2+dy)+" "+x2+" "+y2;
    html+='<path d="'+d+'" fill="none" stroke="'+strokeCol+'" stroke-width="'+w+'" stroke-dasharray="'+dash+'" opacity="'+op+'"/>';
    if(active&&w>=1.5){
      var ang=Math.atan2(y2-y1,x2-x1),s=Math.max(6,w*3);
      var mx=(x1+x2)/2,my=(y1+y2)/2;
      var pts=(mx-s*Math.cos(ang-0.5))+","+(my-s*Math.sin(ang-0.5))+" "+(mx-s*Math.cos(ang+0.5))+","+(my-s*Math.sin(ang+0.5))+" "+mx+","+my;
      html+='<polygon points="'+pts+'" fill="'+strokeCol+'"/>';
      var labels={critical:"CRIT", high:"HIGH", medium:"MED", low:"LOW"};
      var lbl=labels[weight]||"";
      html+='<text x="'+(mx+8)+'" y="'+(my-6)+'" class="edge-weight-label" opacity="'+op+'">'+lbl+'</text>';
    }
  });

  Object.keys(graph.nodes).forEach(function(nid){
    var n=graph.nodes[nid],pos=nodePositions[nid];
    if(!n||!pos) return;
    var inHighlight=!anySelected||highlight[nid];
    var col=inHighlight?sevColor(n.severity):"#9ca3af";
    var opacity=inHighlight?1:0.3;
    html+='<g data-node="'+nid+'" transform="translate('+pos.x+','+pos.y+')" opacity="'+opacity+'" style="cursor:pointer;">';
    if(inHighlight) html+='<rect x="2" y="3" width="'+NODE_W+'" height="'+NODE_H+'" rx="8" fill="#00000010"/>';
    html+='<rect class="node-rect" x="0" y="0" width="'+NODE_W+'" height="'+NODE_H+'" rx="8" fill="'+COLORS.cardBg+'" stroke="'+(nid===selectedNode?col:inHighlight?COLORS.cardStroke:"#e5e7eb")+'" stroke-width="'+(nid===selectedNode?2:1)+'"/>';
    html+='<rect x="0" y="6" width="3" height="'+(NODE_H-12)+'" rx="1.5" fill="'+col+'"/>';
    var title=(n.title||nid);
    var maxChars=Math.floor((NODE_W-50)/7);
    if(title.length>maxChars) title=title.slice(0,maxChars-2)+"…";
    html+='<text x="28" y="21" fill="'+(inHighlight?COLORS.text:"#9ca3af")+'" font-size="12" font-family="system-ui" font-weight="600" style="user-select:none;pointer-events:none;">'+esc(title)+'</text>';
    var badge=(n.kind||"task");
    if(n.severity){
      html+='<text x="28" y="38" fill="'+COLORS.textDim+'" font-size="10" font-family="system-ui" style="user-select:none;pointer-events:none;">'+badge+'</text>';
      html+='<text x="'+(NODE_W-10)+'" y="38" fill="'+col+'" font-size="10" font-weight="bold" text-anchor="end" font-family="system-ui" style="user-select:none;pointer-events:none;">'+n.severity.toUpperCase()+'</text>';
    }else{
      html+='<text x="28" y="38" fill="'+COLORS.textDim+'" font-size="10" font-family="system-ui" style="user-select:none;pointer-events:none;">'+badge+'</text>';
    }
    if(inHighlight){
      html+='<circle class="node-port" data-node="'+nid+'" cx="'+NODE_W+'" cy="'+NODE_H/2+'" r="'+PORT_R+'" fill="'+col+'" stroke="'+col+'" stroke-width="1.5"/>';
      html+='<circle cx="0" cy="'+NODE_H/2+'" r="'+PORT_R+'" fill="none" stroke="'+COLORS.cardStroke+'" stroke-width="1"/>';
    }
    html+='</g>';
  });

  svgEl.innerHTML=html;
}

function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}

// --- Node creation ---
function addNode(kind){ startCreate(kind); }

function startCreate(kind){
  creatingKind=kind;
  document.getElementById("panel-title").textContent="New "+(kind==="leaf"?"Data Source":"Task");
  document.getElementById("panel-name").value="";
  document.getElementById("panel-desc").value="";
  document.getElementById("panel-kind").value=kind;
  document.getElementById("panel-field-rules").innerHTML="";
  document.getElementById("panel-inbound-signals").innerHTML="";
  document.getElementById("panel-outbound-signal").innerHTML="";
  document.getElementById("btn-delete").style.display="none";
  document.getElementById("panel-relations").style.display="none";
  onKindChange();
}

function onKindChange(){
  var isLeaf=document.getElementById("panel-kind").value==="leaf";
  document.getElementById("panel-leaf-fields").style.display=isLeaf?"block":"none";
  document.getElementById("panel-field-rules-section").style.display=isLeaf?"block":"none";
  document.getElementById("panel-signals").style.display=isLeaf?"none":"block";
}

async function hashId(name){
  var data=new TextEncoder().encode(name+Date.now());
  var hash=await crypto.subtle.digest("SHA-256",data);
  return Array.from(new Uint8Array(hash)).map(function(b){return b.toString(16).padStart(2,"0");}).join("").slice(0,12);
}

async function saveNodePanel(){
  var name=document.getElementById("panel-name").value.trim();
  if(!name){alert("Name required");return;}
  var kind=document.getElementById("panel-kind").value;
  var desc=document.getElementById("panel-desc").value;
  var nid=creatingKind?await hashId(name):selectedNode;
  var body={id:nid,kind:kind,title:name,description:desc};
  if(kind==="leaf"){
    body.adapter_id=document.getElementById("panel-adapter").value||"generic";
    body.query=document.getElementById("panel-query").value||"";
    body.field_rules=collectFieldRules();
  }
  fetch("/builder/nodes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){return r.json();}).then(function(d){
    if(d.deduped) nid=d.id;
    body.id=nid; graph.nodes[nid]=body;
    if(creatingKind){
      selectedNode=nid; creatingKind=null;
      document.getElementById("panel-title").textContent=name+" ("+kind+")";
      document.getElementById("panel-relations").style.display="block";
      document.getElementById("btn-delete").style.display="block";
    }
    layoutGraph(); render();
  });
}

// --- Field rules ---
function collectFieldRules(){
  var rules=[];
  var rows=document.querySelectorAll(".field-rule-row");
  rows.forEach(function(row){
    var fieldName=row.querySelector(".fr-field").value.trim();
    if(!fieldName) return;
    var kind=row.querySelector(".fr-kind").value;
    var rule={field:fieldName, kind:kind};
    if(kind==="structured"){
      rule.operator=row.querySelector(".fr-op").value;
      rule.expected=parseFloat(row.querySelector(".fr-expected").value)||0;
    }
    rule.severity_on_breach=row.querySelector(".fr-severity").value;
    rules.push(rule);
  });
  return rules;
}

function addFieldRule(){
  var container=document.getElementById("panel-field-rules");
  var row=document.createElement("div");
  row.className="field-rule-row";
  row.innerHTML=
    '<input class="fr-field" type="text" placeholder="field name" style="flex:1;min-width:60px;">'+
    '<select class="fr-kind" onchange="toggleFrKind(this)">'+
      '<option value="structured">structured</option>'+
      '<option value="unstructured">unstructured</option>'+
    '</select>'+
    '<span class="fr-structured-opts">'+
      '<select class="fr-op"><option value="<"><</option><option value=">">></option>'+
      '<option value="<=">&le;</option><option value=">=">&ge;</option>'+
      '<option value="==">=</option></select>'+
      '<input class="fr-expected" type="number" placeholder="val" style="width:50px;">'+
    '</span>'+
    '<select class="fr-severity">'+
      '<option value="low">Low</option>'+
      '<option value="medium" selected>Medium</option>'+
      '<option value="high">High</option>'+
      '<option value="critical">Critical</option>'+
    '</select>'+
    '<button class="fr-delete" onclick="this.closest(\'.field-rule-row\').remove()">&times;</button>';
  container.appendChild(row);
}

function toggleFrKind(sel){
  var opts=sel.parentElement.querySelector(".fr-structured-opts");
  opts.style.display=sel.value==="unstructured"?"none":"";
}

function renderFieldRules(fieldRules){
  var container=document.getElementById("panel-field-rules");
  container.innerHTML="";
  if(!fieldRules||!fieldRules.length) return;
  fieldRules.forEach(function(fr){
    var row=document.createElement("div");
    row.className="field-rule-row";
    var isStructured=fr.kind!=="unstructured";
    row.innerHTML=
      '<input class="fr-field" type="text" value="'+esc(fr.field||"")+'" style="flex:1;min-width:60px;">'+
      '<select class="fr-kind" onchange="toggleFrKind(this)">'+
        '<option value="structured"'+(isStructured?" selected":"")+'>structured</option>'+
        '<option value="unstructured"'+(isStructured?"":" selected")+'>unstructured</option>'+
      '</select>'+
      '<span class="fr-structured-opts" style="display:'+(isStructured?"":"none")+'">'+
        '<select class="fr-op">'+
          '<option value="<"'+(fr.operator==="<"?" selected":"")+'><</option>'+
          '<option value=">"'+(fr.operator===">"?" selected":"")+'>></option>'+
          '<option value="<="'+(fr.operator==="<="?" selected":"")+'>&le;</option>'+
          '<option value=">="'+(fr.operator===">="?" selected":"")+'>&ge;</option>'+
          '<option value="=="'+(fr.operator==="=="?" selected":"")+'>=</option>'+
        '</select>'+
        '<input class="fr-expected" type="number" value="'+(fr.expected||0)+'" style="width:50px;">'+
      '</span>'+
      '<select class="fr-severity">'+
        '<option value="low"'+(fr.severity_on_breach==="low"?" selected":"")+'>Low</option>'+
        '<option value="medium"'+(fr.severity_on_breach==="medium"?" selected":"")+'>Medium</option>'+
        '<option value="high"'+(fr.severity_on_breach==="high"?" selected":"")+'>High</option>'+
        '<option value="critical"'+(fr.severity_on_breach==="critical"?" selected":"")+'>Critical</option>'+
      '</select>'+
      '<button class="fr-delete" onclick="this.closest(\'.field-rule-row\').remove()">&times;</button>';
    container.appendChild(row);
  });
}

// --- Signal display ---
function renderSignals(inbound, outbound){
  var inEl=document.getElementById("panel-inbound-signals");
  inEl.innerHTML="";
  (inbound||[]).forEach(function(s){
    var sevClass="sev-"+(s.severity||"medium");
    inEl.innerHTML+=
      '<div class="signal-item '+sevClass+'">'+
      esc(s.source_node||"")+' &rarr; score '+(s.score||0)+
      ' <span class="sig-weight">('+esc(s.severity||"")+')</span>'+
      '<div style="color:#9ca3af;margin-top:2px;">'+esc(s.cause||"")+'</div>'+
      '</div>';
  });
  if(!inbound||!inbound.length){
    inEl.innerHTML='<span style="opacity:0.4;font-size:11px;">No upstream signals yet</span>';
  }
  var outEl=document.getElementById("panel-outbound-signal");
  if(outbound){
    outEl.innerHTML=
      '<div class="signal-item sev-'+(outbound.severity||"medium")+'">'+
      'score '+outbound.score+' ('+esc(outbound.severity||"")+')'+
      '<div style="color:#9ca3af;margin-top:2px;">'+esc(outbound.cause||"")+'</div>'+
      '</div>';
  }else{
    outEl.innerHTML='<span style="opacity:0.4;font-size:11px;">Not assessed yet</span>';
  }
}

function selectNode(nid){
  if(!nid){ closePanel(); return; }
  selectedNode=nid; creatingKind=null;
  var n=graph.nodes[nid];
  if(!n){ closePanel(); return; }
  document.getElementById("panel-title").textContent=n.title+" ("+n.kind+")";
  document.getElementById("panel-name").value=n.title||"";
  document.getElementById("panel-desc").value=n.description||"";
  document.getElementById("panel-kind").value=n.kind||"task";
  document.getElementById("panel-relations").style.display="block";
  document.getElementById("btn-delete").style.display="block";
  // Inject Problem is only meaningful on a leaf data source.
  var injectBtn=document.getElementById("btn-inject");
  if(injectBtn) injectBtn.disabled=(n.kind!=="leaf");
  onKindChange();
  fetch("/builder/nodes/"+nid).then(function(r){return r.json();}).then(function(d){
    n.description=d.description||n.description||"";
    n.field_rules=d.field_rules||[];
    n.raw_values=d.raw_values||{};
    n.inbound_signals=d.inbound_signals||[];
    n.outbound_signal=d.outbound_signal||null;
    n.query=d.query||"";
    n.adapter_id=d.adapter_id||"";
    document.getElementById("panel-desc").value=n.description||"";
    // Surface the SQL query for data-source (leaf) nodes.
    document.getElementById("panel-adapter").value=d.adapter_id||"";
    document.getElementById("panel-query").value=d.query||"";
    renderFieldRules(d.field_rules||[]);
    renderSignals(d.inbound_signals||[], d.outbound_signal);
    // Direction-correct: "I depend on" = inputs (what flows into this node);
    // "Depends on me" = consumers (what this node flows into).
    var iDepend=(d.inputs||[]).map(function(id){return {id:id};});
    var depOnMe=(d.consumers||[]).map(function(id){return {id:id};});
    var titleFor=function(nid){return (graph.nodes[nid]&&graph.nodes[nid].title)||nid;};
    document.getElementById("panel-dependencies").innerHTML=iDepend.map(function(r){
      return "<div class='rel-item' style='cursor:pointer;' onclick='drillToNode(\""+r.id+"\")'>"+esc(titleFor(r.id))+"</div>";
    }).join("")||"<span style='opacity:0.4;'>none</span>";
    document.getElementById("panel-dependents").innerHTML=depOnMe.map(function(r){
      return "<div class='rel-item' style='cursor:pointer;' onclick='drillToNode(\""+r.id+"\")'>"+esc(titleFor(r.id))+"</div>";
    }).join("")||"<span style='opacity:0.4;'>none</span>";
    loadNodeContext(nid);
  }).catch(function(){});
  render();
}

function drillToNode(nid){
  selectNode(nid);
}

function closePanel(){
  creatingKind=null; selectedNode=null;
  document.getElementById("panel-title").textContent="New Node";
  document.getElementById("panel-relations").style.display="none";
  document.getElementById("btn-delete").style.display="none";
  render();
}

function deleteSelectedNode(){
  if(!selectedNode) return;
  if(!confirm("Delete node "+selectedNode+"?")) return;
  fetch("/builder/nodes/"+selectedNode,{method:"DELETE"}).then(function(){
    delete graph.nodes[selectedNode]; delete nodePositions[selectedNode];
    graph.edges=graph.edges.filter(function(e){return e.src!==selectedNode&&e.dst!==selectedNode;});
    closePanel(); layoutGraph(); render();
  });
}

// --- Actions ---
async function runBatchAssess(){
  var orphans=[];
  Object.keys(graph.nodes).forEach(function(nid){
    var hasEdge=(graph.edges||[]).some(function(e){return e.src===nid||e.dst===nid;});
    if(!hasEdge) orphans.push(nid);
  });
  if(orphans.length>0){
    alert("Cannot run assessment. Orphan nodes:\n"+orphans.join(", ")); return;
  }
  var r=await fetch("/builder/batch-assess",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:"full"})});
  var result=await r.json();
  var assessments=result.assessments||result;
  var violations=result.violations||[];
  if(violations.length>0) console.log("Violations:",violations);
  assessments.forEach(function(a){if(graph.nodes[a.node_id]){graph.nodes[a.node_id].severity=a.severity;}});
  layoutGraph(); render();
}

// Lazily fetch + render a node's Chroma-grounded LLM context summary.
async function loadNodeContext(nodeId){
  var el=document.getElementById("panel-context");
  if(!el) return;
  el.textContent="Loading context...";
  try{
    var r=await fetch("/node/"+nodeId+"/context");
    var d=await r.json();
    el.textContent=d.summary||"No context.";
  }catch(e){ el.textContent=""; }
}

function injectProblem(nodeId){
  if(!nodeId) return;
  removeInjectMenu();
  var btn=document.getElementById("btn-inject");
  var menu=document.createElement("div");
  menu.className="weight-picker";
  menu.id="inject-menu";
  var levels=[["low","Reset (healthy)"],["medium","Medium"],["high","High"],["critical","Critical"]];
  menu.innerHTML='<div style="padding:8px 16px;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb;">Inject problem severity</div>'+
    levels.map(function(l){
      return '<div class="w-row" onclick="confirmInject(\''+nodeId+'\',\''+l[0]+'\')">'+esc(l[1])+'</div>';
    }).join("")+
    '<div class="w-actions"><button onclick="removeInjectMenu()">Cancel</button></div>';
  var rect=btn.getBoundingClientRect();
  menu.style.left=rect.left+"px"; menu.style.top=(rect.bottom+4)+"px";
  document.body.appendChild(menu);
}

function removeInjectMenu(){
  var m=document.getElementById("inject-menu"); if(m) m.remove();
}

async function confirmInject(nodeId, severity){
  removeInjectMenu();
  var r=await fetch("/inject/"+nodeId,{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({severity:severity})});
  var d=await r.json();
  await loadGraph();              // refresh severities for the whole graph
  if(selectedNode===nodeId) selectNode(nodeId);  // refresh panel signals/relations
  layoutGraph(); render();
}

function resetView(){ nodePositions={}; selectedNode=null; closePanel(); loadGraph(); }
