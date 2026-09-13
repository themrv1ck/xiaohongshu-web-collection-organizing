// Offline replay of visible controls. No browser, network, or real account.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let now = 0, serial = 0, collected = input.collected, joinVisible = false;
let panel = false, confirmed = false, security = false, collectBusy = !!input.initialBusy;
const clicks = [], tasks = new Map(), nodes = [];
const ignoredClicks = [];
let collectGateUntil = -1;
const schedule = (fn, delay, repeat = false) => {
  const id = ++serial; tasks.set(id, {fn, at: now + delay, delay, repeat}); return id;
};
const rect = () => ({left:0,top:0,width:100,height:30});
const el = (text, click) => ({innerText:text, textContent:text,
  getBoundingClientRect:rect, scrollIntoView(){}, getAttribute(){return null;},
  dispatchEvent(e){if(e.type==='click')click?.();}});
const own = el('我'); own.getAttribute = () => '/user/profile/' + input.user;
const collect = el('', () => {
  clicks.push(collected ? 'uncollect' : 'collect');
  const was = collected;
  if (collectBusy) return;
  // Mirrors the observed page's 500 ms leading-only click gate. The icon is
  // optimistically updated before its request finishes, without disabled DOM.
  if (now < collectGateUntil) {ignoredClicks.push(was?'uncollect':'collect');return;}
  collectGateUntil = now + (input.platformGateMs || 0);
  if (input.stuck === (was ? 'uncollect' : 'collect')) return;
  if (input.optimisticIcon) collected = !was;
  schedule(() => {
    if (!input.optimisticIcon) collected = !was;
    if (was && input.busyAfterUncollect) {
      collectBusy = true;
      if (!input.busyNeverClears) schedule(() => {collectBusy=false;},700);
    }
    if (was && input.securityAfterUncollect) security = true;
    if (collected && !input.missingJoin) joinVisible = true;
  }, 150);
});
collect.querySelector = () => ({getAttribute:()=>collected?'#collected':'#collect'});
collect.closest = () => collectBusy && input.busyKind !== 'pointer' ? collect : null;
const join = el('加入专辑', () => {clicks.push('join'); panel=true; joinVisible=false;});
const target = el(input.target, () => {clicks.push('board'); confirmed=!input.missingConfirmation;});
const container = {...el(''),scrollTop:0,scrollHeight:500,clientHeight:300};
const innerScroll = {...el(''),scrollTop:0,scrollHeight:700,clientHeight:300};
container.querySelectorAll = () => input.nestedScroll ? [innerScroll] : [];
if(input.nestedScroll)container.scrollHeight=300;
const success = el('已加入' + input.target);
const location = new URL('https://www.xiaohongshu.com/explore/' + input.note);
const document = {
  body:{get innerText(){return security?'安全验证 300031':'';}},
  documentElement:{appendChild(e){nodes.push(e);}},
  createElement(){return {dataset:{},textContent:'',hidden:false};},
  querySelector(s){return s==='#note-page-collect-board-guide'?collect:s==='.board-list-container'&&panel?container:null;},
  querySelectorAll(s){
    if(s==='a[href*="/user/profile/"]')return [own];
    if(s.includes('right-area'))return joinVisible?[join]:[];
    if(s==='.board-list .board-item')return panel&&!input.missingTarget&&(!input.nestedScroll||innerScroll.scrollTop>0)?[target]:[];
    if(s.includes('message-container'))return confirmed?[success]:[];
    return [];
  }
};
const E = function(type, opts){this.type=type;Object.assign(this,opts);};
const sandbox = {URL,document,window:{location,name:'marker'},
  getComputedStyle:e=>({display:'block',visibility:'visible',
    pointerEvents:e===collect&&collectBusy&&input.busyKind==='pointer'?'none':'auto',
    overflowY:e===innerScroll||!input.nestedScroll?'auto':'visible'}),
  MouseEvent:E,PointerEvent:E,FocusEvent:E,
  MutationObserver:class{observe(){}disconnect(){}},
  Date:{now:()=>1000+now+(input.wallClockJump&&now>=100?60000:0)},Math,
  performance:{now:()=>now},
  setInterval:(fn,ms)=>schedule(fn,ms,true),clearInterval:id=>tasks.delete(id),
  setTimeout:(fn,ms)=>schedule(fn,ms),clearTimeout:id=>tasks.delete(id)};
let error = null;
try {
  vm.runInNewContext(input.job,sandbox,{timeout:1000});
  for(let i=0;i<500&&tasks.size;i++){
    const [id,t]=[...tasks].sort((a,b)=>a[1].at-b[1].at)[0];
    if(t.at>15000)break;
    now=t.at;
    if(t.repeat)t.at+=t.delay;else tasks.delete(id);
    t.fn();
    if(nodes.some(n=>JSON.parse(n.textContent||'{}').done))break;
  }
} catch(e){error=e.message;}
process.stdout.write(JSON.stringify({clicks,ignoredClicks,collected,error,
  state:nodes.length?JSON.parse(nodes.at(-1).textContent):null}));
