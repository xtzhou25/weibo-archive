'use strict';
const el = id => document.getElementById(id);
const safePath = path => /^media\/[a-f0-9]{32}\.(jpg|jpeg|png|gif|webp|avif|mp4|mov|webm|m4v)$/.test(path || '');
const node = (tag, text, cls) => {const value=document.createElement(tag);if(text!=null)value.textContent=text;if(cls)value.className=cls;return value;};
function allMedia(post) {return [...(post.media||[]), ...(post.repost?allMedia(post.repost):[])];}
const commentIndex=new Map();
for(const comment of ARCHIVE.comments||[]){if(!commentIndex.has(comment.post_id))commentIndex.set(comment.post_id,[]);commentIndex.get(comment.post_id).push(comment);}
function textOf(post) {return [post.text,post.author?.name,post.repost?textOf(post.repost):'',...(commentIndex.get(post.id)||[]).map(comment=>[comment.text,comment.author?.name].join(' '))].join(' ');}
const legacyLink=el('legacy-snapshot');
if(typeof LEGACY_SNAPSHOT==='string'&&/^reader-v1-\d{8}-\d{6}\.html$/.test(LEGACY_SNAPSHOT)){legacyLink.href=LEGACY_SNAPSHOT;legacyLink.hidden=false;}
const viewer=node('dialog',null,'media-viewer');viewer.id='media-viewer';viewer.setAttribute('aria-label','媒体查看器');
const viewerBar=node('div',null,'viewer-bar'),viewerTitle=node('strong','媒体查看器'),viewerCounter=node('span','','viewer-counter');
const viewerClose=node('button','关闭 ×');viewerClose.type='button';viewerClose.setAttribute('aria-label','关闭媒体查看器');viewerClose.onclick=()=>viewer.close();
viewerBar.append(viewerTitle,viewerCounter,viewerClose);
const viewerStage=node('div',null,'viewer-stage');
const viewerTools=node('div',null,'viewer-tools'),viewerPrev=node('button','← 上一项'),viewerNext=node('button','下一项 →'),viewerZoom=node('button','原尺寸查看'),viewerOriginal=node('a','打开本地原文件');
for(const button of [viewerPrev,viewerNext,viewerZoom])button.type='button';
viewerOriginal.target='_blank';viewerOriginal.rel='noopener noreferrer';
viewerTools.append(viewerPrev,viewerNext,viewerZoom,viewerOriginal);viewer.append(viewerBar,viewerStage,viewerTools);document.body.append(viewer);
let viewerItems=[],viewerIndex=0,viewerOrigin=null,viewerZoomed=false,swipeStart=null;
function isLocalMedia(item){return item?.status==='done'&&safePath(item.path)&&['image','video'].includes(item.kind);}
function stopViewerMedia(){for(const video of viewerStage.querySelectorAll('video')){video.pause();video.removeAttribute('src');video.load();}viewerStage.replaceChildren();}
function missingMedia(item){
  const box=node('div',null,'media-unavailable');
  box.append(node('strong',item.status==='skipped_size'?'视频已按大小上限跳过':'媒体暂不可用'));
  box.append(node('p',item.skip_reason||item.error||'这个文件尚未下载完成，可在后续备份中继续处理。'));
  if(item.status==='skipped_size'&&/^https:\/\//i.test(item.url||'')){const link=node('a','查看原始视频（需要联网）');link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}
  return box;
}
function renderViewer(){
  stopViewerMedia();viewerZoomed=false;viewer.classList.remove('is-zoomed');viewerZoom.textContent='原尺寸查看';
  const item=viewerItems[viewerIndex];viewerCounter.textContent=`${viewerIndex+1} / ${viewerItems.length}`;
  viewerTitle.textContent=item.kind==='video'?'本地视频':item.kind==='image'?'图片查看器':'媒体详情';
  viewerPrev.disabled=viewerIndex===0;viewerNext.disabled=viewerIndex===viewerItems.length-1;
  viewerZoom.hidden=!isLocalMedia(item)||item.kind!=='image';viewerOriginal.hidden=!isLocalMedia(item);
  viewerOriginal.removeAttribute('href');
  if(!isLocalMedia(item)){viewerStage.append(missingMedia(item));return;}
  viewerOriginal.href=item.path;
  const media=node(item.kind==='video'?'video':'img');
  if(item.kind==='video'){
    media.controls=true;media.preload='metadata';media.playsInline=true;
    media.setAttribute('aria-label','本地备份视频');
    media.addEventListener('error',()=>{if(media.isConnected&&viewer.open&&!viewerStage.querySelector('.viewer-error'))viewerStage.append(node('p','浏览器无法播放此文件，可尝试打开本地原文件。','viewer-error'));});
  }else{media.alt=`备份图片，第 ${viewerIndex+1} 张`;media.addEventListener('error',()=>{if(media.isConnected&&viewer.open&&!viewerStage.querySelector('.viewer-error'))viewerStage.append(node('p','图片文件无法读取，请检查媒体是否仍在原目录。','viewer-error'));});}
  media.src=item.path;viewerStage.append(media);viewerStage.scrollTop=0;viewerStage.scrollLeft=0;
}
function openViewer(items,index,origin){viewerItems=items;viewerIndex=index;viewerOrigin=origin;renderViewer();if(!viewer.open)viewer.showModal();document.body.classList.add('viewer-open');viewerClose.focus();}
function moveViewer(delta){if(viewerItems[viewerIndex+delta]){viewerIndex+=delta;renderViewer();}}
viewerPrev.onclick=()=>moveViewer(-1);viewerNext.onclick=()=>moveViewer(1);
viewerZoom.onclick=()=>{viewerZoomed=!viewerZoomed;viewer.classList.toggle('is-zoomed',viewerZoomed);viewerZoom.textContent=viewerZoomed?'适应窗口':'原尺寸查看';};
viewer.addEventListener('close',()=>{stopViewerMedia();document.body.classList.remove('viewer-open');if(viewerOrigin?.isConnected)viewerOrigin.focus();});
viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close();});
viewer.addEventListener('keydown',event=>{if(['INPUT','TEXTAREA','SELECT','VIDEO'].includes(event.target.tagName))return;if(event.key==='ArrowLeft'){event.preventDefault();moveViewer(-1);}if(event.key==='ArrowRight'){event.preventDefault();moveViewer(1);}});
viewerStage.addEventListener('pointerdown',event=>{swipeStart=event.pointerType==='touch'&&!viewerZoomed&&viewerItems[viewerIndex]?.kind==='image'?{x:event.clientX,y:event.clientY}:null;});
viewerStage.addEventListener('pointerup',event=>{if(swipeStart){const dx=event.clientX-swipeStart.x,dy=event.clientY-swipeStart.y;if(Math.abs(dx)>60&&Math.abs(dy)<60)moveViewer(dx<0?1:-1);}swipeStart=null;});
function durationLabel(seconds){if(!Number.isFinite(seconds)||seconds<0)return '';const n=Math.floor(seconds);return `${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;}
function addGallery(parent,items){
  if(!items?.length)return;
  const gallery=node('div',null,'media-grid');gallery.dataset.count=String(Math.min(items.length,9));
  gallery.classList.toggle('single-video',items.length===1&&items[0].kind==='video');
  const visibleItems=items.slice(0,9);
  for(const [index,item]of visibleItems.entries()){
    const tile=node('button',null,'media-tile');tile.type='button';tile.dataset.kind=item.kind;
    tile.setAttribute('aria-label',`${item.kind==='video'?'打开视频':'查看图片'}，第 ${index+1} 项，共 ${items.length} 项`);
    tile.onclick=()=>openViewer(items,index,tile);
    if(isLocalMedia(item)){
      const preview=node(item.kind==='video'?'video':'img');
      if(item.kind==='video'){
        preview.preload='metadata';preview.muted=true;preview.playsInline=true;preview.tabIndex=-1;
        const play=node('span','▶','video-play');play.setAttribute('aria-hidden','true');tile.append(play);
        const badge=node('span','视频','media-badge');tile.append(badge);
        preview.addEventListener('loadedmetadata',()=>{badge.textContent=durationLabel(preview.duration)||'视频';});
      }else{
        preview.loading='lazy';preview.decoding='async';preview.alt=`备份图片 ${index+1}`;
        preview.addEventListener('load',()=>{if(preview.naturalHeight>preview.naturalWidth*2){tile.classList.add('long-image');tile.append(node('span','长图','media-badge'));}else if(/\.gif$/i.test(item.path))tile.append(node('span','GIF','media-badge'));});
      }
      preview.src=item.path;preview.addEventListener('error',()=>{tile.classList.add('preview-error');if(!tile.querySelector('.preview-fallback'))tile.append(node('span',item.kind==='video'?'本地视频 · 点击打开播放器':'图片无法读取','preview-fallback'));});
      tile.prepend(preview);
    }else{
      tile.classList.add('unavailable-tile');tile.append(node('span',item.status==='skipped_size'?'视频超限已跳过':item.kind==='video'?'视频待完成':item.kind==='image'?'图片待完成':'媒体待适配','preview-fallback'));
      tile.title=item.skip_reason||item.error||'媒体未完成';
    }
    if(index===8&&items.length>9){tile.append(node('span',`+${items.length-9}`,'media-overflow'));tile.setAttribute('aria-label',`查看全部 ${items.length} 项媒体，从第 9 项开始`);}
    gallery.append(tile);
  }
  parent.append(gallery);
  if(items.length>9)parent.append(node('p',`共 ${items.length} 项媒体 · 点击缩略图后可前后查看全部`,'media-count-note'));
}
function appendPaged(parent,items,render){let offset=0;const more=node('button','查看更多评论','comment-more');more.type='button';const append=()=>{for(const item of items.slice(offset,offset+25))parent.insertBefore(render(item),more);offset+=25;more.hidden=offset>=items.length;};more.onclick=append;parent.append(more);append();}
function commentsPanel(post){
  const comments=commentIndex.get(post.id)||[],info=post.comment_backup||{state:'not_started'};
  const details=node('details',null,'comments');
  const states={not_started:'尚未备份',pending:'待继续',partial:'部分未完成',error:'需要重试',complete:'已遍历接口返回内容'};
  details.append(node('summary',`评论与回复（${comments.length}） · ${states[info.state]||info.state}`));
  if(info.parent_links_unresolved)details.append(node('p',`${info.parent_links_unresolved} 条回复未返回直接父评论标识，按所属楼中楼分组展示。`,'comment-note'));
  for(const message of info.errors||[])details.append(node('p',message,'comment-note'));
  if(!comments.length){details.append(node('p',info.state==='complete'?'当前接口未返回评论。':'评论尚未下载完成。','comment-note'));return details;}
  const byId=new Map(comments.map(comment=>[comment.id,comment])),children=new Map(),roots=[];
  for(const comment of comments){let parent=comment.parent_id;const trail=new Set([comment.id]);let check=parent,cycle=false;while(check&&byId.has(check)){if(trail.has(check)){cycle=true;break;}trail.add(check);check=byId.get(check).parent_id;}
    if(cycle){roots.push(comment);continue;}
    if(!parent||!byId.has(parent)){parent=comment.root_id!==comment.id&&byId.has(comment.root_id)?comment.root_id:null;}
    if(parent){if(!children.has(parent))children.set(parent,[]);children.get(parent).push(comment);}else roots.push(comment);
  }
  function render(comment,path=new Set(),depth=0){
    if(path.has(comment.id)||depth>64)return node('p','评论关联存在循环或层级过深，原始关联保留在 JSON 中。','comment-note');
    const next=new Set(path);next.add(comment.id);
    const item=node('div',null,'comment');item.id=`comment-${post.id}-${comment.id}`;
    const meta=node('div',null,'meta');meta.append(node('span',comment.author?.name||'未知作者','author'),node('time',(comment.created_at||'').replace('T',' ').slice(0,19)));item.append(meta);
    if(comment.parent_id){const target=byId.get(comment.parent_id);item.append(node('p',target?`回复 ${target.author?.name||'未知作者'} · 评论 ${comment.parent_id}`:`回复评论 ${comment.parent_id}（原评论未由平台返回）`,'comment-reference'));}
    else if(comment.parent_relation==='thread_only')item.append(node('p',(comment.reply_to_author?`回复 @${comment.reply_to_author} · `:'')+'所属一级评论已知，直接父评论标识未由平台返回。','comment-reference'));
    if(comment.reply_original_text)item.append(node('blockquote',comment.reply_original_text,'reply-quote'));
    item.append(node('p',comment.text||'','text'));addGallery(item,comment.media);
    const descendants=children.get(comment.id)||[];
    if(descendants.length){const replies=node('details',null,'replies');replies.append(node('summary',`展开回复（${descendants.length}）`));let loaded=false;replies.addEventListener('toggle',()=>{if(replies.open&&!loaded){loaded=true;const list=node('div');appendPaged(list,descendants,child=>render(child,next,depth+1));replies.append(list);}});item.append(replies);}
    return item;
  }
  let loaded=false;details.addEventListener('toggle',()=>{if(details.open&&!loaded){loaded=true;const list=node('div');appendPaged(list,roots,comment=>render(comment));details.append(list);}});
  return details;
}
function card(post,nested=false) {
  const article=node(nested?'div':'article',null,nested?'repost':'post');
  const meta=node('div',null,'meta');meta.append(node('span',post.author?.name||'未知作者','author'),node('time',post.created_at?post.created_at.replace('T',' ').slice(0,19):'时间未知'));article.append(meta);
  const scope=post.visibility,check=post.visibility_check,failed=check?.state==='error';
  const visibility=node('div',null,'visibility');
  const badge=node('span',`${failed?'上次记录的可见范围':'可见范围'}：${scope?.label||'未知（尚未核验）'}`,'visibility-badge');
  badge.dataset.state=scope?.state||'unknown';
  if(scope?.type!=null)badge.title=`平台可见范围类型：${scope.type}`;
  visibility.append(badge);
  const checked=scope?.checked_at?scope.checked_at.replace('T',' ').slice(0,19):'';
  if(checked)visibility.append(node('span',`核验于 ${checked}`,'visibility-time'));
  if(failed){const warning=node('span','本次核验失败，当前范围待确认','visibility-warning');warning.title=check.error||'';visibility.append(warning);}
  article.append(visibility);
  article.append(node('p',post.text||'','text'));
  addGallery(article,post.media);
  if(post.repost)article.append(card(post.repost,true));
  if(!nested)article.append(commentsPanel(post));return article;
}
let matches=[],shown=0;
function more(){for(const post of matches.slice(shown,shown+50))el('posts').append(card(post));shown+=50;el('more').hidden=shown>=matches.length;}

function validDay(day){return /^\d{4}-\d{2}-\d{2}$/.test(day)&&Number.isFinite(Date.parse(day+'T00:00:00Z'))&&new Date(day+'T00:00:00Z').toISOString().slice(0,10)===day;}
function postDay(post){const day=String(post.created_at||'').slice(0,10);return validDay(day)?day:'';}
const years=new Map(),months=new Map(),days=ARCHIVE.posts.map(postDay).filter(Boolean).sort();
for(const day of days){const year=day.slice(0,4),month=day.slice(0,7);years.set(year,(years.get(year)||0)+1);months.set(month,(months.get(month)||0)+1);}
const earliest=days[0]||'',latest=days.at(-1)||'',monthKeys=[...months.keys()].sort(),unknownDates=ARCHIVE.posts.length-days.length;
const timeStorageKey='weiboArchive.readerTime.'+ARCHIVE.account_uid;
let timeMode='all';
function option(value,text){const item=node('option',text);item.value=value;return item;}
for(const year of [...years.keys()].sort().reverse())el('time-year').append(option(year,`${year} 年 · ${years.get(year)} 条`));
el('archive-extent').textContent=days.length?`档案时间：${earliest} — ${latest} · ${years.size} 个年份，${months.size} 个有内容的月份`:'档案中暂无有效的微博日期。';
for(const id of ['from','to']){el(id).disabled=!days.length;el(id).min=earliest;el(id).max=latest;}
el('time-year').disabled=!years.size;
el('unknown-dates-label').hidden=!unknownDates;
el('unknown-dates-text').textContent=`同时显示时间未知的 ${unknownDates} 条微博`;
function monthOptions(year){el('time-month').replaceChildren(option('','全部月份'));for(const key of monthKeys.filter(key=>key.startsWith(year+'-')).reverse())el('time-month').append(option(key.slice(5),`${Number(key.slice(5))} 月 · ${months.get(key)} 条`));el('time-month').disabled=!year;}
function saveTime(){try{localStorage.setItem(timeStorageKey,JSON.stringify({mode:timeMode,year:el('time-year').value,month:el('time-month').value,start:el('from').value,end:el('to').value,includeUnknown:el('include-unknown').checked}));}catch(e){}}
function rangeError(){const start=el('from').value,end=el('to').value;if(start&&end&&start>end)return '开始日期不能晚于结束日期，请调整范围。';if([el('from'),el('to')].some(input=>!input.validity.valid))return '请选择档案起止日期以内的有效日期。';return '';}
function updateTimeControls(){
  const start=el('from').value,end=el('to').value,limited=Boolean(start||end);
  el('from').max=end||latest;el('to').min=start||earliest;
  const error=rangeError();el('range-error').textContent=error;el('range-error').hidden=!error;
  for(const input of [el('from'),el('to')])input.setAttribute('aria-invalid',String(Boolean(error)));
  document.querySelectorAll('[data-time-preset]').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.timePreset===timeMode));button.disabled=!days.length&&button.dataset.timePreset!=='all';});
  el('month-nav').hidden=!el('time-month').value;
  const index=monthKeys.indexOf(el('time-year').value+'-'+el('time-month').value);
  el('older-month').disabled=index<=0;el('newer-month').disabled=index<0||index>=monthKeys.length-1;
  el('include-unknown').disabled=!limited;
  el('clear-dates').disabled=!limited&&!error;
  let hint=limited?`正在阅读 ${start||earliest} — ${end||latest}（北京时间，包含首尾两天）。`:'显示全部备份时间。';
  if(['7','30','90'].includes(timeMode))hint+=` 最近 ${timeMode} 天从档案最新日期 ${latest} 倒推。`;
  if(unknownDates&&limited)hint+=el('include-unknown').checked?` 另含 ${unknownDates} 条时间未知的微博。`:` ${unknownDates} 条时间未知的微博暂不显示。`;
  el('range-hint').textContent=hint;
  return error;
}
function setRange(start='',end='',mode='all',year='',month=''){
  timeMode=mode;el('from').value=start;el('to').value=end;el('time-year').value=year;monthOptions(year);el('time-month').value=month;filter();saveTime();
}
function chooseMonth(key,mode='month'){const selected=days.filter(day=>day.startsWith(key+'-'));if(selected.length)setRange(selected[0],selected.at(-1),mode,key.slice(0,4),key.slice(5));}
function chooseYear(year){const selected=days.filter(day=>day.startsWith(year+'-'));if(selected.length)setRange(selected[0],selected.at(-1),'year',year);else setRange();}
function preset(mode){
  if(mode==='all'){el('include-unknown').checked=false;setRange();return;}
  if(!latest)return;
  if(mode==='latest-month'){chooseMonth(latest.slice(0,7),mode);return;}
  if(!['7','30','90'].includes(mode))return;
  const start=new Date(Date.parse(latest+'T00:00:00Z')-(Number(mode)-1)*86400000).toISOString().slice(0,10);
  setRange(start<earliest?earliest:start,latest,mode);
}
function filter(){
  const error=updateTimeControls(),query=el('search').value.trim().toLowerCase(),start=el('from').value,end=el('to').value,kind=el('kind').value;
  matches=error?[]:ARCHIVE.posts.filter(post=>{const day=postDay(post),inRange=day?(!start||day>=start)&&(!end||day<=end):(!start&&!end)||el('include-unknown').checked;return inRange&&(!query||textOf(post).toLowerCase().includes(query))&&(!kind||allMedia(post).some(item=>item.kind===kind));});
  el('summary').textContent=`账号 ${ARCHIVE.account_uid} · 共 ${ARCHIVE.posts.length} 条微博 · 当前 ${matches.length} 条`;
  el('empty').hidden=Boolean(matches.length);
  el('empty-message').textContent=error?'请先调整日期，再继续阅读。':'当前筛选条件下没有微博，可以调整时间范围、搜索词或媒体类型。';
  el('empty-clear-dates').hidden=!start&&!end&&!error;
  el('posts').replaceChildren();shown=0;more();
}
for(const id of ['search','kind'])el(id).addEventListener('input',filter);
for(const id of ['from','to'])el(id).addEventListener('input',()=>{timeMode=el('from').value||el('to').value?'custom':'all';el('time-year').value='';monthOptions('');filter();if(!rangeError())saveTime();});
el('include-unknown').addEventListener('change',()=>{filter();saveTime();});
el('time-year').addEventListener('change',()=>chooseYear(el('time-year').value));
el('time-month').addEventListener('change',()=>el('time-month').value?chooseMonth(el('time-year').value+'-'+el('time-month').value):chooseYear(el('time-year').value));
for(const [id,delta] of [['older-month',-1],['newer-month',1]])el(id).onclick=()=>{const index=monthKeys.indexOf(el('time-year').value+'-'+el('time-month').value);if(index>=0&&monthKeys[index+delta])chooseMonth(monthKeys[index+delta]);};
for(const button of document.querySelectorAll('[data-time-preset]'))button.onclick=()=>preset(button.dataset.timePreset);
for(const id of ['clear-dates','empty-clear-dates'])el(id).onclick=()=>preset('all');
el('more').onclick=more;
let savedTime=null;try{savedTime=JSON.parse(localStorage.getItem(timeStorageKey));}catch(e){}
if(savedTime){
  el('include-unknown').checked=Boolean(savedTime.includeUnknown);
  if(savedTime.mode==='year'&&years.has(savedTime.year))chooseYear(savedTime.year);
  else if(savedTime.mode==='month'&&months.has(savedTime.year+'-'+savedTime.month))chooseMonth(savedTime.year+'-'+savedTime.month);
  else if(savedTime.mode==='custom'&&[savedTime.start,savedTime.end].every(day=>!day||(validDay(day)&&day>=earliest&&day<=latest))&&(!savedTime.start||!savedTime.end||savedTime.start<=savedTime.end))setRange(savedTime.start,savedTime.end,'custom');
  else preset(['all','latest-month','7','30','90'].includes(savedTime.mode)?savedTime.mode:'all');
}else preset('all');
