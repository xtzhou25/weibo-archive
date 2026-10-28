'use strict';
const $=id=>document.getElementById(id);
let state=null,busy=false,version='',token='',lastSample=null,speed=0;
const phases={idle:'等待开始',starting:'正在启动',running:'正在备份',paused:'已暂停',stopped:'已停止',complete:'已完成',partial:'部分未完成',error:'需要处理'};
const authPhases={signed_out:'未登录',unverified:'验证会话',opening:'正在加载',waiting_scan:'等待扫码',confirm_on_phone:'请在手机确认',verifying:'验证账号',ready:'已登录',expired:'二维码失效',error:'登录异常'};
const videoLimit=$('video-max-mb'),videoLimitKey='weiboArchive.videoMaxMb';
try{const saved=localStorage.getItem(videoLimitKey);if(saved!==null&&/^\d{1,7}$/.test(saved)&&Number(saved)<=1048576)videoLimit.value=saved;}catch(e){}
for(const event of ['input','change'])videoLimit.addEventListener(event,()=>{if(videoLimit.value!==''&&videoLimit.checkValidity()){try{localStorage.setItem(videoLimitKey,videoLimit.value);}catch(e){}}});
function bytes(value){let n=Number(value)||0,i=0;while(n>=1024&&i<4){n/=1024;i++;}return `${i?n.toFixed(1):n} ${['B','KB','MB','GB','TB'][i]}`;}
function error(message){$('error').textContent=message;$('error').hidden=!message;}
function render(){
  const a=state?.auth,b=state?.backup,active=Boolean(state?.active);
  document.querySelectorAll('button').forEach(button=>button.disabled=busy);
  for(const id of ['login','window','refresh','logout'])$(id).disabled=busy||active;
  $('start').disabled=busy||active||!a?.ready;
  $('comments-only').disabled=busy||active||!a?.ready||!state?.features?.comments;
  $('failed-only').disabled=busy||active||!a?.ready||!state?.features?.failed_media_retry;
  $('visibility-only').disabled=busy||active||!a?.ready||!state?.features?.visibility;
  if(document.querySelector('[name=comments]').checked&&!state?.features?.comments)$('start').disabled=true;
  if(state&&!state.features?.video_size_limit){$('start').disabled=true;$('comments-only').disabled=true;}
  $('pause').disabled=busy||!active||b?.phase==='paused';
  $('resume').disabled=busy||!active||b?.phase!=='paused';
  $('stop').disabled=busy||!active;
  if(!state)return;
  $('auth-badge').textContent=authPhases[a.phase]||a.phase;
  $('auth-message').textContent=(a.browser?`${a.browser} · `:'')+(a.uid?`账号 ${a.name||a.uid} · `:'')+a.message;
  $('start-hint').textContent=a.ready?(active?'备份任务已启动，请在下方查看进度。':'账号已验证。中断任务会续传，已完成任务会增量更新。'):'登录确认前不能开始备份；请等待上方显示“已登录”。';
  $('phase').textContent=phases[b.phase]||b.phase;
  $('message').textContent=b.message;
  $('posts').textContent=b.posts;$('media').textContent=b.media;$('bytes').textContent=bytes(b.bytes);$('comments').textContent=b.comments||0;
  const now=Date.now();
  if(lastSample&&now>lastSample.time){const delta=Number(b.bytes)-lastSample.bytes;speed=delta>=0&&b.phase==='running'?delta/((now-lastSample.time)/1000):0;}
  lastSample={time:now,bytes:Number(b.bytes)||0};
  const retry=b.retry_until?Math.max(0,Math.ceil(b.retry_until-now/1000)):b.retry_in;
  const file=b.file_total?`当前文件 ${Math.min(100,Math.round(100*b.file_downloaded/b.file_total))}% (${bytes(b.file_downloaded)} / ${bytes(b.file_total)})`:'';
  $('detail').textContent=[b.retry_total?`失败媒体 ${b.retry_done||0}/${b.retry_total}，恢复 ${b.retry_recovered||0}`:'',b.visibility_stage==='fetching'?`核验可见范围 ${b.visibility_done||0}/${b.visibility_total||0}`:b.comment_stage==='fetching'?'正在抓取评论及回复':b.page?`第 ${b.page} 页`:'',active?`${bytes(speed)}/s`:'',b.incomplete?`${b.incomplete} 项媒体未完成`:'',b.skipped?`${b.skipped} 个视频超出大小上限，已跳过`:'',b.comment_jobs_pending?`${b.comment_jobs_pending} 个评论队列待完成`:'',b.comment_parent_unresolved?`${b.comment_parent_unresolved} 条回复的父评论标识缺失`:'',retry?`退避约 ${retry} 秒`:'',file,b.current_file||''].filter(Boolean).join(' · ');
  $('qr-panel').hidden=!a.qr_ready;
  if(a.qr_ready&&version!==String(a.qr_version)){version=String(a.qr_version);$('qr').src='/api/qr?v='+encodeURIComponent(version);}
  if(!a.qr_ready){version='';$('qr').removeAttribute('src');}
  const events=(b.events||[]).slice().reverse().map(text=>{const item=document.createElement('li');item.textContent=text;return item;});$('events').replaceChildren(...events);
}
async function refresh(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw new Error();state=await response.json();token=state.csrf;render();return true;}catch(e){state=null;render();$('message').textContent='本地服务未连接，请确认启动窗口仍在运行。';return false;}}
async function command(action,data={}){if(busy)return;busy=true;error('');render();try{if((!token||!state)&&!await refresh())throw new Error('无法连接本地服务');const response=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error||'操作失败');await refresh();}catch(e){error(e.message);}finally{busy=false;render();}}
$('login').onclick=()=>command('login',{persist:$('persist').checked,mode:'qr'});
$('refresh').onclick=$('login').onclick;
$('window').onclick=()=>command('login',{persist:$('persist').checked,mode:'window'});
$('logout').onclick=()=>{if(confirm('清除本工具的登录会话？备份文件会保留。'))command('logout');};
for(const action of ['pause','resume','stop','export'])$(action).onclick=()=>command(action);
function startBackup(commentsOnly=false,visibilityOnly=false,failedOnly=false){if(!state?.auth.ready){error('请先完成扫码和账号验证');return;}if(!$('settings').reportValidity())return;const form=new FormData($('settings'));if(!state.features?.video_size_limit){error('视频大小限制正在更新，请等待服务更新后刷新页面');return;}if(visibilityOnly&&!state.features?.visibility){error('可见范围功能正在更新，请等待服务更新后刷新页面');return;}if(!visibilityOnly&&!failedOnly&&(commentsOnly||form.has('comments'))&&!state.features?.comments){error('评论功能正在更新，请等待服务更新后刷新页面');return;}try{localStorage.setItem(videoLimitKey,form.get('video_max_mb'));}catch(e){}command('start',{text:form.has('text'),images:form.has('images'),video:form.has('video'),video_max_mb:form.get('video_max_mb'),comments:!visibilityOnly&&!failedOnly&&(commentsOnly||form.has('comments')),failed_only:failedOnly,comments_only:commentsOnly,visibility_only:visibilityOnly,start:form.get('start'),end:form.get('end')});}
$('settings').onsubmit=event=>{event.preventDefault();startBackup();};
$('comments-only').onclick=()=>startBackup(true);
$('failed-only').onclick=()=>startBackup(false,false,true);
$('visibility-only').onclick=()=>startBackup(false,true);
render();refresh();setInterval(()=>{if(!busy)refresh();},1000);
