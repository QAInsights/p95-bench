
import http from 'k6/http';
import {check, sleep} from 'k6';
import {Rate} from 'k6/metrics';
const cfg = {"users": 2, "iterations": 2, "mode": "flow"};
const rows = [{"username": "bench-1", "password": "bench", "quantity": 2}, {"username": "bench-2", "password": "bench", "quantity": 3}, {"username": "bench-3", "password": "bench", "quantity": 4}, {"username": "bench-4", "password": "bench", "quantity": 5}, {"username": "bench-5", "password": "bench", "quantity": 6}, {"username": "bench-6", "password": "bench", "quantity": 7}, {"username": "bench-7", "password": "bench", "quantity": 8}, {"username": "bench-8", "password": "bench", "quantity": 9}, {"username": "bench-9", "password": "bench", "quantity": 10}, {"username": "bench-10", "password": "bench", "quantity": 11}, {"username": "bench-11", "password": "bench", "quantity": 12}, {"username": "bench-12", "password": "bench", "quantity": 13}, {"username": "bench-13", "password": "bench", "quantity": 14}, {"username": "bench-14", "password": "bench", "quantity": 15}, {"username": "bench-15", "password": "bench", "quantity": 16}, {"username": "bench-16", "password": "bench", "quantity": 17}];
const failure = new Rate('business_failure');
export const options = {"scenarios": {"journeys": {"executor": "per-vu-iterations", "vus": 2, "iterations": 2, "maxDuration": "30s"}}, "thresholds": {"checks": ["rate==1"]}};
let csrf = '', cart = '', grant = '';
const base = __ENV.BASE_URL;
function body(r) { try { return r.json(); } catch (_) { return {}; } }
function raw(path, method='GET', data=null, extra={}) {
  const r = http.request(method, base + path, data===null ? null : JSON.stringify(data), {
    headers: Object.assign({'Content-Type':'application/json','X-CSRF-Token':csrf},extra),
    redirects:0, timeout:'3s'});
  const b=body(r); if(b.csrf) csrf=b.csrf;
  if(path==='/v2/work') failure.add(r.status>=400 && !(r.status===401 && b.error==='expired') && r.status!==429);
  return r;
}
function login(){
  let r=raw('/v2/login-form');
  const token=r.html().find('input[name="csrf"]').first().attr('value');
  const row=rows[(__VU-1)%rows.length];
  r=raw('/v2/login','POST',{username:row.username,password:row.password,csrf:token});
  if(r.status===302) {r=raw(r.headers.Location);grant=body(r).grant_id;}
  check(r,{'login':r=>r.status===200});
}
function request(path, method='GET', data=null, extra={}){
  let r=raw(path,method,data,extra);
  if(r.status===401 && ['expired','session'].indexOf(body(r).error)>=0){
    const renewed=raw('/v2/session/renew','POST',{});
    if(renewed.status!==200) login();
    if(cart) raw('/v2/carts/'+cart+'/recover','POST',{});
    r=raw(path,method,data,extra);
  }
  return r;
}
function work(override){
  if(cfg.workload || !csrf) login();
  const path=override||cfg.path||'/v2/work';
  let r=request(path,'POST',{});
  for(let n=0;n<4 && r.status===429;n++){
    sleep(Number(r.headers['Retry-After'])+.01);r=request(path,'POST',{});
  }
  check(r,{'operation or expected throttle':r=>(r.status===200 && body(r).ok===true) || (cfg.completion && r.status===429)});
}
function flow(){
  csrf='';cart='';grant='';
  if(cfg.mode==='ownership' && __VU===2) sleep(.5);
  login();
  if(cfg.mode==='challenge'){
    let r=request('/v2/challenge-form');const form=r.html().find('form');
    const token=form.find('input[name="challenge"]').first().attr('value');
    r=request(form.attr('action'),'POST',{challenge:token});
    r=request(r.headers.Location);grant=body(r).grant_id;
  }
  let page=cfg.mode==='pagination'?'/v2/catalog/page':'/v2/catalog', product;
  for(let n=0;n<3 && page;n++){
    const b=body(request(page));
    product=b.data.products.find(p=>p.variants.some(v=>v.in_stock));
    if(product) break;page=b.next;
  }
  if(!product){check(false,{'eligible product':x=>x});return;}
  const variant=product.variants.find(v=>v.in_stock);
  let r=request('/v2/carts','POST',{product_id:product.id,variant_id:variant.id,grant_id:grant});
  cart=body(r).id;let etag=r.headers.Etag;
  check(r,{'cart':r=>r.status===201});
  if(cfg.mode==='ownership') sleep(__VU===1 ? 1.2 : 1.6);
  if(cfg.mode==='etag'||cfg.mode==='data'){
    const quantity=cfg.mode==='data'?rows[(__VU-1)%rows.length].quantity:(cfg.quantity || 2);
    r=request('/v2/carts/'+cart,'PATCH',{quantity:quantity},{'If-Match':etag});
    if(r.status===412){etag=request('/v2/carts/'+cart).headers.Etag;
      r=request('/v2/carts/'+cart,'PATCH',{quantity:quantity},{'If-Match':etag});}
    etag=r.headers.Etag;check(r,{'update':r=>r.status===200});
  }
  let reservation;
  if(cfg.mode==='reservation'){
    r=request('/v2/reservations','POST',{cart_id:cart});const url=body(r).status_url;
    for(let n=0;n<5;n++){
      sleep(.05);r=request(url);const b=body(r);
      if(b.status==='ready'){reservation=b.reservation_id;break;}
      if(b.status==='failed'){check(false,{'reservation ready':x=>x});return;}
    }
    if(!reservation) return;
  }
  r=request('/v2/orders','POST',{cart_id:cart,reservation_id:reservation},
     {'Idempotency-Key':cart,'If-Match':etag});
  check(r,{'accepted':r=>r.status===201 && body(r).accepted===true});
  if(cfg.mode==='threshold') raw('/v2/health');
}
export default function(){
  if(cfg.workload) {if(cfg.operation==='journey') flow();else work();}
  else if(cfg.mode==='errors'){login();for(let n=0;n<4;n++) work();}
  else flow();
}
export function browse(){work('/v2/browse');}
export function checkout(){flow();}
