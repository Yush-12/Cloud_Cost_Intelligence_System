const dateLabel=value=>new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'});
document.querySelectorAll('time[datetime]').forEach(el=>{el.title=el.dateTime;el.textContent=dateLabel(el.dateTime)});
const detections=JSON.parse(document.querySelector('#detection-data').textContent);
const selector=document.querySelector('#metric-select');
const labels={outlier:'Outlier detected',within_baseline:'Within baseline',insufficient_data:'More history needed',insufficient_variation:'Baseline is constant'};
const descriptions={outlier:'The evaluated observation differs from the learned baseline. Inspect the evidence before choosing an action.',within_baseline:'The evaluated sample was not flagged. This does not prove that the workload is healthy.',insufficient_data:'More consecutive observations are needed. Missing buckets break the training baseline.',insufficient_variation:'The baseline has fewer than three distinct values. No model score is reported.'};
const svgNS='http://www.w3.org/2000/svg';
function node(tag,attrs,text){const el=document.createElementNS(svgNS,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));if(text!==undefined)el.textContent=text;return el}
function render(){
 const item=detections[Number(selector.value)],svg=document.querySelector('#chart');svg.replaceChildren();
 const width=Math.max(320,Math.round(svg.getBoundingClientRect().width));svg.setAttribute('viewBox','0 0 '+width+' 280');
 let pts=item.points;const range=document.querySelector('#range-select').value;
 if(range!=='all'&&pts.length){const end=Date.parse(pts[pts.length-1].timestamp);pts=pts.filter(p=>Date.parse(p.timestamp)>=end-Number(range)*3600000)}
 const latest=item.points[item.points.length-1];
 document.querySelector('#metric-title').textContent=item.metric+' · latest stored sample';
 document.querySelector('#metric-value').textContent=latest?Number(latest.value).toLocaleString(undefined,{maximumFractionDigits:2})+' '+({Milliseconds:'ms',Count:'count',Percent:'%',Bytes:'bytes'}[latest.unit]||latest.unit):'No observations';
 const badge=document.querySelector('#metric-status');badge.textContent=labels[item.status]||item.status;badge.className='badge '+(item.status==='outlier'?'warn':'neutral');
 document.querySelector('#evidence-heading').textContent=labels[item.status]||item.status;
 document.querySelector('#evidence-description').textContent=descriptions[item.status]||'';
 document.querySelector('#baseline-count').textContent=item.baseline_points+' / '+item.required_baseline_points+' minimum';
 document.querySelector('#decision-score').textContent=item.score===null?'Not evaluated':item.score.toFixed(4);
 document.querySelector('#sample-time').textContent=item.sample_timestamp?dateLabel(item.sample_timestamp):'No sample';
 const tbody=document.querySelector('#chart-table');tbody.replaceChildren();
 pts.forEach(p=>{const tr=document.createElement('tr');[dateLabel(p.timestamp),p.value,p.unit].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});tbody.append(tr)});
 if(!pts.length){svg.append(node('text',{x:width/2,y:140,'text-anchor':'middle'},'No observations in this window'));document.querySelector('#chart-caption').textContent='Missing observations are not zero usage.';return}
 const times=pts.map(p=>Date.parse(p.timestamp)),values=pts.map(p=>p.value),minT=Math.min(...times),maxT=Math.max(...times);
 const upper=Math.max(...values)*1.12||1, lower=Math.min(0,...values);const x=t=>58+(t-minT)/(maxT-minT||1)*(width-82),y=v=>235-(v-lower)/(upper-lower)*213;
 for(let i=0;i<=4;i++){const val=lower+(upper-lower)*i/4,yy=y(val);svg.append(node('line',{x1:58,x2:width-24,y1:yy,y2:yy,stroke:'#e7ecf3'}));svg.append(node('text',{x:48,y:yy+4,'text-anchor':'end'},val.toLocaleString(undefined,{maximumFractionDigits:1})))}
 let segment=[];const flush=()=>{if(segment.length>1)svg.append(node('polyline',{points:segment.join(' '),fill:'none',stroke:'#3a70dc','stroke-width':2,'stroke-linejoin':'round'}));segment=[]};
 pts.forEach((p,i)=>{if(i&&times[i]-times[i-1]!==p.period_seconds*1000)flush();segment.push(x(times[i])+','+y(p.value))});flush();
 pts.forEach((p,i)=>{const flagged=item.status==='outlier'&&p.timestamp===item.sample_timestamp;const dot=node('circle',{cx:x(times[i]),cy:y(p.value),r:flagged?5:3,fill:flagged?'#c67828':'#3a70dc',stroke:'#fff','stroke-width':1});dot.append(node('title',{},dateLabel(p.timestamp)+' · '+p.value+' '+p.unit));svg.append(dot)});
 [minT,...(maxT!==minT?(width<450?[maxT]:[minT+(maxT-minT)/2,maxT]):[])].forEach((t,i,all)=>{svg.append(node('text',{x:x(t),y:265,'text-anchor':i===0?'start':i===all.length-1?'end':'middle'},new Date(t).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})))});
 document.querySelector('#chart-caption').textContent=pts.length+' observed buckets · '+pts[0].unit+' · '+dateLabel(pts[0].timestamp)+' to '+dateLabel(pts[pts.length-1].timestamp);
}
if(selector){selector.addEventListener('change',render);document.querySelector('#range-select').addEventListener('change',render);render()}
const search=document.querySelector('#resource-search');search.addEventListener('input',()=>{let shown=0;document.querySelectorAll('#resource-rows tr').forEach(row=>{row.hidden=!row.textContent.toLowerCase().includes(search.value.toLowerCase());if(!row.hidden)shown++});document.querySelector('#search-empty').hidden=shown!==0});
const links=document.querySelectorAll('nav a');const observer=new IntersectionObserver(entries=>{entries.forEach(entry=>{if(entry.isIntersecting){links.forEach(a=>a.classList.toggle('active',a.hash==='#'+entry.target.id))}})},{rootMargin:'-10% 0px -65% 0px'});document.querySelectorAll('main section[id]').forEach(s=>observer.observe(s));

if(selector){new ResizeObserver(()=>render()).observe(document.querySelector('.chart-wrap'));}
