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
var panning=null;
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
  window.addEventListener("mouseup",function(){if(panning){panning=null;svgEl.classList.remove("panning");}});
  window.addEventListener("resize",resizeSVG);
  initPanelResize();
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

function initPanelResize(){
  var resizer=document.getElementById("panel-resizer");
  var panel=document.getElementById("node-panel");
  if(!resizer||!panel) return;
  var active=false;
  resizer.addEventListener("mousedown",function(e){active=true;e.preventDefault();document.body.style.cursor="col-resize";});
  window.addEventListener("mousemove",function(e){
    if(!active) return;
    var w=Math.max(240,Math.min(760,window.innerWidth-e.clientX));
    panel.style.width=w+"px";
    resizeSVG();
  });
  window.addEventListener("mouseup",function(){if(active){active=false;document.body.style.cursor="";}});
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
  var p=toSVG(e.clientX,e.clientY); if(!p) return;
  var ds=e.deltaY>0?0.9:1.1;
  var newScale=Math.max(0.3,Math.min(3,viewScale*ds));
  if(newScale===viewScale) return;
  var k=viewScale/newScale;            // keep the point under the cursor fixed
  viewW=viewW*k; viewH=viewH*k;
  viewX=p.x-(p.x-viewX)*k; viewY=p.y-(p.y-viewY)*k;
  viewScale=newScale;
  updateViewBox();
}

var ROOT_ID=null;
async function loadGraph(){
  var r=await fetch("/builder/graph"); var d=await r.json();
  d.nodes.forEach(function(n){graph.nodes[n.id]=n;});
  graph.edges=d.edges.map(function(e){return {src:e.src, dst:e.dst, type:e.type, weight:e.weight||"medium"};});
  if(Object.keys(nodePositions).length===0){
    layoutGraph();
  }
  try{ ROOT_ID=(await (await fetch("/root")).json()).node_id; }catch(e){}
  renderLeftBar();
  render();
}

// ===== Tabs =====
function switchTab(name){
  document.querySelectorAll(".tab").forEach(function(t){t.classList.toggle("active", t.getAttribute("data-tab")===name);});
  document.querySelectorAll(".tab-pane").forEach(function(p){p.classList.remove("active");});
  document.getElementById("tab-"+name).classList.add("active");
  var isBuilder=(name==="builder");
  document.getElementById("btn-task").style.display=isBuilder?"":"none";
  document.getElementById("btn-leaf").style.display=isBuilder?"":"none";
  if(name==="builder"){ resizeSVG(); render(); }
  else{ openReportAtRoot(); }
}

var SEVC={low:"#16a34a",medium:"#ca8a04",high:"#ea580c",critical:"#dc2626",none:"#94a3b8"};

// ===== Left bar =====
function renderLeftBar(){
  var nodes=Object.values(graph.nodes);
  var counts={low:0,medium:0,high:0,critical:0};
  nodes.forEach(function(n){ if(n.severity&&counts[n.severity]!=null) counts[n.severity]++; });
  document.getElementById("risk-dist").innerHTML=["low","medium","high","critical"].map(function(s){
    return "<div class='risk-cell'><div class='n' style='color:"+SEVC[s]+"'>"+counts[s]+"</div><div class='l'>"+s+"</div></div>";
  }).join("");
  fillLbList("lb-program", nodes.filter(function(n){return n.id===ROOT_ID;}));
  fillLbList("lb-tasks", nodes.filter(function(n){return n.kind==="task"&&n.id!==ROOT_ID;}));
  fillLbList("lb-data", nodes.filter(function(n){return n.kind==="leaf";}));
}
function fillLbList(elId, items){
  var el=document.getElementById(elId); if(!el) return;
  el.innerHTML=items.map(function(n){
    var c=SEVC[n.severity||"none"];
    return "<div class='lb-item"+(selectedNode===n.id?" sel":"")+"' onclick=\"lbClick('"+n.id+"')\">"+
      "<span class='lb-dot' style='background:"+c+"'></span><span class='t'>"+esc(n.title||n.id)+"</span></div>";
  }).join("")||"<div class='hint' style='padding:4px 8px;'>none</div>";
}
function lbClick(nid){ switchTab('builder'); selectNode(nid); renderLeftBar(); }

// ===== Report tab =====
var reportPath=[];
async function openReportAtRoot(){
  if(!ROOT_ID){ try{ ROOT_ID=(await (await fetch("/root")).json()).node_id; }catch(e){} }
  reportPath=ROOT_ID?[ROOT_ID]:[];
  renderReport();
}
function reportNavigate(nid){
  var idx=reportPath.indexOf(nid);
  if(idx>=0) reportPath=reportPath.slice(0,idx+1); else reportPath.push(nid);
  renderReport();
}
async function renderReport(){
  var card=document.getElementById("report-card");
  if(!reportPath.length){ card.innerHTML="<div class='rpt'><div class='rpt-empty'>No root found. Build a graph and run assessment.</div></div>"; return; }
  var nid=reportPath[reportPath.length-1];
  document.getElementById("report-breadcrumb").innerHTML=reportPath.map(function(id,i){
    var last=(i===reportPath.length-1);
    var title=(graph.nodes[id]&&graph.nodes[id].title)||id;
    return (i>0?"<span class='sep'>&rsaquo;</span>":"")+
      "<span class='crumb"+(last?" cur":"")+"'"+(last?"":" onclick=\"reportNavigate('"+id+"')\"")+">"+esc(title)+"</span>";
  }).join("");
  card.innerHTML="<div class='rpt'><div class='rpt-empty'>Loading...</div></div>";
  var d=await (await fetch("/builder/nodes/"+nid)).json();
  if(graph.nodes[nid]) graph.nodes[nid].severity=d.severity;
  var sev=d.severity||"none";
  var ctx="";
  if(!d.query){ try{ ctx=(await (await fetch("/node/"+nid+"/context")).json()).summary; }catch(e){} }
  var drivers=(d.inputs||[]).map(function(id){
    var n=graph.nodes[id]||{}; var s=n.severity||"none";
    return "<div class='driver' onclick=\"reportNavigate('"+id+"')\">"+
      "<span class='dleft' style='background:"+SEVC[s]+"'></span>"+
      "<span class='dtitle'>"+esc(n.title||id)+"</span>"+
      "<span class='dsev' style='color:"+SEVC[s]+"'>"+s+"</span><span class='arrow'>&rsaquo;</span></div>";
  }).join("");
  var stats="";
  var rv=d.raw_values||{}, rvk=Object.keys(rv);
  if(rvk.length) stats+="<div class='rpt-stat'><div class='k'>Current value</div><div class='v'>"+rvk.map(function(k){return esc(k)+" = <b>"+esc(String(rv[k]))+"</b>";}).join("<br>")+"</div></div>";
  if(d.field_rules&&d.field_rules.length) stats+="<div class='rpt-stat'><div class='k'>Threshold</div><div class='v'>"+d.field_rules.map(function(fr){
    return esc(fr.field)+" "+esc(fr.operator||"")+" "+esc(String(fr.expected))+" &rarr; "+esc(fr.severity_on_breach);
  }).join("<br>")+"</div></div>";
  card.innerHTML="<div class='rpt'>"+
    "<div class='rpt-head'><span class='rpt-title'>"+esc(d.title||nid)+"</span>"+
      "<span class='badge kind'>"+esc(d.kind||"")+"</span>"+
      "<span class='sev-pill' style='background:"+SEVC[sev]+"'>"+sev+"</span></div>"+
    (d.description?"<div class='rpt-row'><div class='k'>Description</div><div class='v'>"+esc(d.description)+"</div></div>":"")+
    (ctx?"<div class='rpt-row'><div class='k'>Context (vector + LLM)</div><div class='v'>"+esc(ctx)+"</div></div>":"")+
    (stats?"<div class='rpt-row'><div class='rpt-grid'>"+stats+"</div></div>":"")+
    "<div class='rpt-row'><div class='k'>Drivers (what feeds this node)</div>"+
      (drivers||"<div class='rpt-empty'>No upstream drivers (leaf data source).</div>")+"</div></div>";
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
  // Empty canvas: start panning (drag the view both axes).
  selectNode(null);
  panning={sx:e.clientX, sy:e.clientY, vx:viewX, vy:viewY};
  svgEl.classList.add("panning");
}

function onMouseMove(e){
  if(panning){
    var rect=svgEl.getBoundingClientRect();
    viewX=panning.vx-(e.clientX-panning.sx)*(viewW/rect.width);
    viewY=panning.vy-(e.clientY-panning.sy)*(viewH/rect.height);
    updateViewBox();
    return;
  }
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
  if(panning){panning=null;svgEl.classList.remove("panning");}
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

// At most one rule per severity band (LOW/MEDIUM/HIGH/CRITICAL).
var MAX_FIELD_RULES=4;

function fieldRuleRowHTML(fr){
  fr=fr||{};
  var isStructured=fr.kind!=="unstructured";
  function op(v){return fr.operator===v?" selected":"";}
  function sv(v){return fr.severity_on_breach===v?" selected":"";}
  var medDefault=fr.severity_on_breach?sv("medium"):" selected";
  return ''+
    '<input class="fr-field" type="text" placeholder="field name" value="'+esc(fr.field||"")+'">'+
    '<select class="fr-kind" onchange="toggleFrKind(this)">'+
      '<option value="structured"'+(isStructured?" selected":"")+'>structured</option>'+
      '<option value="unstructured"'+(isStructured?"":" selected")+'>unstructured</option>'+
    '</select>'+
    '<span class="fr-structured-opts" style="display:'+(isStructured?"":"none")+'">'+
      '<select class="fr-op">'+
        '<option value="<"'+op("<")+'><</option>'+
        '<option value=">"'+op(">")+'>></option>'+
        '<option value="<="'+op("<=")+'>&le;</option>'+
        '<option value=">="'+op(">=")+'>&ge;</option>'+
        '<option value="=="'+op("==")+'>=</option>'+
      '</select>'+
      '<input class="fr-expected" type="number" placeholder="val" value="'+(fr.expected!=null?fr.expected:"")+'">'+
    '</span>'+
    '<select class="fr-severity">'+
      '<option value="low"'+sv("low")+'>Low</option>'+
      '<option value="medium"'+medDefault+'>Medium</option>'+
      '<option value="high"'+sv("high")+'>High</option>'+
      '<option value="critical"'+sv("critical")+'>Critical</option>'+
    '</select>'+
    '<button class="fr-delete" title="remove" onclick="deleteFieldRule(this)">&times;</button>';
}

function fieldRuleCount(){
  return document.querySelectorAll("#panel-field-rules .field-rule-row").length;
}

function refreshAddFieldRuleBtn(){
  var btn=document.getElementById("btn-add-field-rule");
  if(!btn) return;
  btn.disabled=fieldRuleCount()>=MAX_FIELD_RULES;
  btn.style.opacity=btn.disabled?"0.4":"1";
  btn.style.cursor=btn.disabled?"not-allowed":"pointer";
}

function addFieldRule(){
  if(fieldRuleCount()>=MAX_FIELD_RULES) return;
  var container=document.getElementById("panel-field-rules");
  var row=document.createElement("div");
  row.className="field-rule-row";
  row.innerHTML=fieldRuleRowHTML({});
  container.appendChild(row);
  refreshAddFieldRuleBtn();
}

function deleteFieldRule(btn){
  btn.closest(".field-rule-row").remove();
  refreshAddFieldRuleBtn();
}

function toggleFrKind(sel){
  var opts=sel.parentElement.querySelector(".fr-structured-opts");
  opts.style.display=sel.value==="unstructured"?"none":"";
}

function renderFieldRules(fieldRules){
  var container=document.getElementById("panel-field-rules");
  container.innerHTML="";
  (fieldRules||[]).forEach(function(fr){
    var row=document.createElement("div");
    row.className="field-rule-row";
    row.innerHTML=fieldRuleRowHTML(fr);
    container.appendChild(row);
  });
  refreshAddFieldRuleBtn();
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
    renderReadings(d.raw_values||{});
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
    // Context (vector search + LLM) is for UNSTRUCTURED sources and tasks only.
    // A SQL data source (has a query) is structured -- no vector search.
    var section=document.getElementById("panel-context-section");
    if(d.query){
      section.style.display="none";
    }else{
      section.style.display="block";
      loadNodeContext(nid);
    }
  }).catch(function(){});
  render();
}

function renderReadings(rawValues){
  var el=document.getElementById("panel-readings");
  if(!el) return;
  var keys=Object.keys(rawValues||{});
  if(!keys.length){ el.innerHTML="<span style='opacity:0.4;'>no readings yet -- run the query / assessment</span>"; return; }
  el.innerHTML=keys.map(function(k){
    return "<div class='reading-item'><span>"+esc(k)+"</span>"+
      "<input class='reading-input' data-field='"+esc(k)+"' type='number' step='any' value='"+esc(String(rawValues[k]))+"'></div>";
  }).join("")+
  "<button onclick='saveReadings()' style='margin-top:6px;width:100%;padding:5px;font-size:12px;background:#1d4ed8;color:#fff;border:none;border-radius:4px;cursor:pointer;'>Save readings (write to DB)</button>";
}

function saveReadings(){
  if(!selectedNode) return;
  var readings={};
  document.querySelectorAll("#panel-readings .reading-input").forEach(function(inp){
    var v=parseFloat(inp.value); if(!isNaN(v)) readings[inp.getAttribute("data-field")]=v;
  });
  fetch("/node/"+selectedNode+"/readings",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({readings:readings})})
    .then(function(r){return r.json();}).then(function(d){
      if(graph.nodes[selectedNode]) graph.nodes[selectedNode].raw_values=d.raw_values;
      markAssessmentStale();
    });
}

function testQuery(){
  var q=document.getElementById("panel-query").value.trim();
  var out=document.getElementById("query-test-result");
  if(!q){ out.style.color="#dc2626"; out.textContent="Enter a query first."; return; }
  out.style.color="#6b7280"; out.textContent="Running query...";
  fetch("/test-query",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query:q})})
    .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
    .then(function(res){
      if(!res.ok){ out.style.color="#dc2626"; out.textContent="Error: "+(res.d.error||"query failed"); return; }
      var rd=res.d.readings||{}; var keys=Object.keys(rd);
      out.style.color="#15803d";
      out.textContent=keys.length?("Reading: "+keys.map(function(k){return k+" = "+rd[k];}).join(", ")):"Query ran but returned no (field, value) rows.";
    }).catch(function(e){ out.style.color="#dc2626"; out.textContent="Error: "+e; });
}

function markAssessmentStale(){
  var btn=document.getElementById("btn-assess");
  if(btn) btn.style.boxShadow="0 0 0 2px #f59e0b";
  var note=document.getElementById("assess-note"); if(note) note.style.display="inline";
}

function clearAssessmentStale(){
  var btn=document.getElementById("btn-assess");
  if(btn) btn.style.boxShadow="";
  var note=document.getElementById("assess-note"); if(note) note.style.display="none";
}

function showAssessStatus(on){
  var s=document.getElementById("assess-status");
  if(s) s.style.display=on?"flex":"none";
  var btn=document.getElementById("btn-assess");
  if(btn) btn.disabled=on;
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
// Run a full assessment: the server re-reads every node's data source, then
// runs one flattened LLM pass. Shows a processing status while the LLM runs.
async function runAssessment(){
  var orphans=[];
  Object.keys(graph.nodes).forEach(function(nid){
    var hasEdge=(graph.edges||[]).some(function(e){return e.src===nid||e.dst===nid;});
    if(!hasEdge) orphans.push(nid);
  });
  if(orphans.length>0){
    alert("Cannot run assessment. Orphan nodes:\n"+orphans.join(", ")); return;
  }
  showAssessStatus(true);
  try{
    var r=await fetch("/assess",{method:"POST"});
    var d=await r.json();
    (d.nodes||[]).forEach(function(n){ if(graph.nodes[n.id]) graph.nodes[n.id].severity=n.severity; });
    clearAssessmentStale();
    renderLeftBar();
    if(selectedNode) selectNode(selectedNode);   // refresh panel readings/signals
    layoutGraph(); render();
  }catch(e){ alert("Assessment failed: "+e); }
  finally{ showAssessStatus(false); }
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


function resetView(){ nodePositions={}; selectedNode=null; closePanel(); loadGraph(); }
