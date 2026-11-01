let jobId = null, fonts = [], templateDirty = false, templateReady = false, busyTemplate = false, taskVersion = 0;
let lyricTimer, renderTimer;
const defaults = {title_font_id:'ma_shan_zheng',lyric_font_id:'ma_shan_zheng',title_size:0,lyric_size:0,title_color:'#444444',lyric_color:'#8e1212'};
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const toast = (text,error=false) => { $('toast').textContent=text; $('toast').className=(error?'error ':'')+'show'; setTimeout(()=>$('toast').className='',4500); };
const show = id => { document.querySelectorAll('.panel,.step').forEach(x=>x.classList.remove('active')); $(id).classList.add('active'); document.querySelector(`.step[data-target="${id}"]`).classList.add('active'); };
async function api(url,options={}) { const r=await fetch(url,options),d=await r.json(); if(!r.ok||d.ok===false)throw Error(d.error||d.detail||'请求失败');return d; }
const jsonOptions = (method,data) => ({method,headers:{'content-type':'application/json'},body:JSON.stringify(data)});
document.querySelectorAll('.step').forEach(b=>b.onclick=()=>show(b.dataset.target));
$('openTemplate').onclick=()=>show('preview');
async function detectAudioMeta(file){
  try{const r=await fetch(`/api/audio-meta?filename=${encodeURIComponent(file.name)}`,{method:'POST',body:file}),d=await r.json();if(!d.ok)throw Error(d.error||'解析失败');if(d.title)$('title').value=d.title;if(d.artist)$('artist').value=d.artist;}
  catch(e){toast(`未能自动解析歌名歌手：${e.message}`,true);}
}
$('audioFile').onchange=e=>{const file=e.target.files[0];if(file){$('audioName').textContent=file.name;detectAudioMeta(file);}};
function rememberTask(){localStorage.setItem('musicVideoJob',jobId);history.replaceState(null,'','?job='+encodeURIComponent(jobId));loadExportName();}
function setDirty(value=true){templateDirty=value;$('templateContinue').disabled=value||!templateReady||busyTemplate;$('renderVideo').disabled=value||!templateReady||busyTemplate;if(value)$('templateStatus').textContent='設定已變更，請儲存以更新關鍵幀與成片。';}
function settings(){return {title_font_id:$('titleFont').value,lyric_font_id:$('lyricFont').value,title_size:Number($('titleSize').value),lyric_size:Number($('lyricSize').value),title_color:$('titleColor').value,lyric_color:$('lyricColor').value};}
function setSettings(value){$('fontSearch').value='';populateFonts(value.title_font_id,value.lyric_font_id);$('titleSize').value=value.title_size;$('lyricSize').value=value.lyric_size;$('titleColor').value=value.title_color;$('lyricColor').value=value.lyric_color;updateFontSamples();}
function populateFonts(titleId=$('titleFont').value||defaults.title_font_id,lyricId=$('lyricFont').value||defaults.lyric_font_id){
  const query=$('fontSearch').value.trim().toLowerCase();let matching=0;
  for(const [id,selected] of [['titleFont',titleId],['lyricFont',lyricId]]){
    const groups=new Map();
    for(const font of fonts){const match=`${font.label} ${font.category} ${font.id}`.toLowerCase().includes(query);if(!match&&font.id!==selected)continue;if(id==='titleFont'&&match)matching++;if(!groups.has(font.category))groups.set(font.category,[]);groups.get(font.category).push(font);}
    $(id).innerHTML=[...groups].map(([group,items])=>`<optgroup label="${esc(group)}">${items.map(f=>`<option value="${esc(f.id)}"${f.id===selected?' selected':''}>${esc(f.label)}</option>`).join('')}</optgroup>`).join('');
  }
  $('fontCount').textContent=`${fonts.length} 個本地字體選項 · ${matching} 個符合搜尋 · 字體及授權均已隨項目保存`;
}
function updateFontSamples(){for(const layer of ['title','lyric']){const font=fonts.find(f=>f.id===$(layer+'Font').value);if(!font)continue;const img=$(layer+'FontSample');img.src=`/api/fonts/${encodeURIComponent(font.id)}/sample?text=${encodeURIComponent(layer==='title'?($('title').value||'歌名書法').slice(0,80):'於是我們繼續，風起雲湧')}`;img.hidden=false;$(layer+'FontInfo').innerHTML=`${esc(font.category)} · <a href="/api/fonts/${encodeURIComponent(font.id)}/license" target="_blank" rel="noopener">${esc(font.license)}</a> · <a href="${esc(font.source)}" target="_blank" rel="noopener">作者來源 ↗</a>`;}}
api('/api/version').then(v=>{$('buildInfo').textContent=`${v.commit}${v.dirty?'*':''} · ${v.branch} · pid ${v.pid} · ${v.started_at.replace('T',' ').slice(0,19)}`;}).catch(()=>{$('buildInfo').textContent='版本未知';});
$('fontSearch').oninput=()=>populateFonts();
for(const id of ['titleFont','lyricFont'])$(id).onchange=()=>{updateFontSamples();setDirty();};
for(const id of ['titleSize','lyricSize','titleColor','lyricColor'])$(id).oninput=()=>setDirty();
$('resetTemplate').onclick=()=>{setSettings(defaults);setDirty();};
$('templateContinue').onclick=()=>show('finish');
function drawFrames(urls,frames=[]){
  const images=urls.map(url=>url+'?t='+Date.now());
  const choose=index=>{$('templateFrame').src=images[index];$('templateFrame').alt=frames[index]?.lyric||`歌曲關鍵幀 ${index+1}`;$('templateFrame').hidden=false;$('templatePlaceholder').hidden=true;[...$('templateFrames').children].forEach((el,i)=>el.classList.toggle('active',i===index));};
  $('templateFrames').replaceChildren();
  images.forEach((url,index)=>{const button=document.createElement('button');button.type='button';button.setAttribute('aria-label',`查看第 ${index+1} 張關鍵幀`);const img=document.createElement('img');img.src=url;img.alt=frames[index]?.lyric||`關鍵幀 ${index+1}`;button.append(img);button.onclick=()=>choose(index);$('templateFrames').append(button);});
  if(images.length)choose(0);else{$('templateFrame').hidden=true;$('templatePlaceholder').hidden=false;}
}
$('templateFrame').onclick=()=>{$('largeFrame').src=$('templateFrame').src;$('frameCaption').textContent=$('templateFrame').alt;$('frameViewer').showModal();};
$('closeFrame').onclick=()=>$('frameViewer').close();
$('frameViewer').onclick=e=>{if(e.target===$('frameViewer'))$('frameViewer').close();};
$('saveTemplate').onclick=async()=>{
  if(!jobId)return toast('请先建立歌曲任务并批准歌词',true);
  if(!['titleSize','lyricSize'].every(id=>$(id).value!==''&&$(id).reportValidity()))return;
  const current=jobId,version=taskVersion;busyTemplate=true;$('saveTemplate').disabled=true;$('saveTemplate').textContent='正在生成實際關鍵幀…';
  document.querySelectorAll('.template-controls input,.template-controls select,#resetTemplate,#savedDesignJobs').forEach(el=>el.disabled=true);setDirty();
  try{const data=await api(`/api/jobs/${current}/template`,jsonOptions('PUT',settings()));if(version!==taskVersion)return;templateReady=true;setSettings(data.settings);drawFrames(data.previews,data.frames);$('metadata').textContent=JSON.stringify(data.job.metadata,null,2);$('coverImage').style.display='none';$('coverEmpty').style.display='block';setDirty(false);const fallback=[...new Set(Object.values(data.glyphs).map(r=>r.fallback).join(''))].join('');$('templateStatus').textContent='設定已儲存。五張關鍵幀與成片使用相同渲染器。'+(fallback?`以下字形由霞鶩文楷繁體補齊：${fallback}`:'');toast('字幕設定與預覽已更新');loadSavedJobs(false);watchRender();}
  catch(e){toast(e.message,true);$('templateStatus').textContent=e.message;}
  finally{busyTemplate=false;$('saveTemplate').disabled=false;$('saveTemplate').textContent='儲存設定並更新預覽';document.querySelectorAll('.template-controls input,.template-controls select,#resetTemplate,#savedDesignJobs').forEach(el=>el.disabled=false);setDirty(templateDirty);}
};
$('createJob').onclick=async()=>{
  const file=$('audioFile').files[0],title=$('title').value.trim(),artist=$('artist').value.trim();if(!file||!title||!artist)return toast('请选择歌曲并填写歌名、歌手',true);$('createJob').disabled=true;
  try{const data=await api('/api/jobs?'+new URLSearchParams({title,artist,filename:file.name}),{method:'POST',body:file});jobId=data.job.job_id;taskVersion++;clearTimeout(lyricTimer);templateReady=false;setSettings(defaults);setDirty(false);drawFrames([]);$('lyricEditor').value='';$('editedConfirmed').checked=false;$('lyricConfirmed').checked=false;$('lyricStatus').textContent='可直接貼上或上傳 LRC，或點擊搜索同步歌詞';$('audioPlayer').src=`/api/jobs/${jobId}/audio`;rememberTask();show('direct');toast('任务已建立，可上传或编辑歌词');loadSavedJobs(false);}
  catch(e){toast(e.message,true);}finally{$('createJob').disabled=false;}
};
async function prepareLyrics(){if(!jobId)return toast('请先建立任务',true);try{await api(`/api/jobs/${jobId}/lyrics/prepare`,{method:'POST'});$('lyricStatus').textContent='正在搜索同步歌词…';clearTimeout(lyricTimer);pollLyrics(jobId);}catch(e){toast(e.message,true);}}
async function pollLyrics(current){try{const d=await api(`/api/jobs/${current}/lyrics/status`);if(current!==jobId)return;$('lyricStatus').textContent=d.message||d.status;if(d.status==='ready'){$('lyricEditor').value=d.text||'';toast(d.message);}else if(d.status==='failed')toast(d.message,true);else if(['searching'].includes(d.status))lyricTimer=setTimeout(()=>pollLyrics(current),1500);}catch(e){toast(e.message,true);}}
$('retryLyrics').onclick=prepareLyrics;
function lyricsApproved(lines){templateReady=false;setDirty();$('lyricStatus').textContent=`已批准 ${lines} 句歌词`;toast('歌词已批准，请设置字幕并更新预览');show('preview');}
$('approveEdited').onclick=async()=>{if(!jobId)return toast('请先建立任务',true);try{const d=await api(`/api/jobs/${jobId}/lyrics/approve`,jsonOptions('POST',{text:$('lyricEditor').value,confirmed:$('editedConfirmed').checked}));lyricsApproved(d.lines);}catch(e){toast(e.message,true);}};
$('uploadLyrics').onclick=async()=>{if(!jobId)return toast('请先建立任务',true);const file=$('lyricFile').files[0];if(!file)return toast('请选择同步 LRC 文件',true);try{const d=await api(`/api/jobs/${jobId}/lyrics?confirmed=${$('lyricConfirmed').checked}`,{method:'POST',body:file});$('lyricEditor').value=await file.text();lyricsApproved(d.lines);}catch(e){toast(e.message,true);}};
async function restoreTask(id){
  if(!id)return;const version=++taskVersion;clearTimeout(lyricTimer);templateReady=false;setDirty(false);
  try{ $('previewLoadStatus').textContent='正在讀取歌曲與字幕設定…';const [job,template]=await Promise.all([api(`/api/jobs/${encodeURIComponent(id)}`),api(`/api/jobs/${encodeURIComponent(id)}/template`)]);if(version!==taskVersion)return;jobId=job.job_id;$('title').value=job.title;$('artist').value=job.artist;$('audioPlayer').src=`/api/jobs/${jobId}/audio`;$('lyricEditor').value='';$('editedConfirmed').checked=false;$('lyricStatus').textContent=job.lyrics_approved?'歌词已批准':'请校对并批准歌词';setSettings(template.settings);templateReady=template.ready;setDirty(false);drawFrames(template.previews);$('metadata').textContent=job.metadata?JSON.stringify(job.metadata,null,2):'儲存模板後生成投稿資料。';$('coverImage').style.display='none';$('coverEmpty').style.display='block';if(job.outputs?.cover){$('coverImage').src=`/api/jobs/${jobId}/file/cover.png?t=${Date.now()}`;$('coverImage').style.display='block';$('coverEmpty').style.display='none';}$('previewLoadStatus').textContent=`${job.title} · ${job.artist}`;$('templateStatus').textContent=template.ready?'已恢復儲存的設定與關鍵幀。':'儲存即可套用統一模板，原有字幕時間軸與轉場會保留。';$('savedDesignJobs').value=jobId;rememberTask();show(job.lyrics_approved?'preview':'direct');watchRender();const lyricState=await api(`/api/jobs/${jobId}/lyrics/status`);if(version===taskVersion&&lyricState.text)$('lyricEditor').value=lyricState.text;}
  catch(e){if(version===taskVersion){$('previewLoadStatus').textContent=e.message;toast(e.message,true);}}
}
$('savedDesignJobs').onchange=e=>restoreTask(e.target.value);
async function loadSavedJobs(restore=true){try{const data=await api('/api/design-jobs');$('savedDesignJobs').innerHTML='<option value="">请选择歌曲</option>'+data.jobs.map(j=>`<option value="${esc(j.id)}">${esc(j.title)} · ${esc(j.artist)}</option>`).join('');$('savedDesignJobs').value=jobId||'';if(restore)await restoreTask(new URLSearchParams(location.search).get('job')||localStorage.getItem('musicVideoJob')||data.jobs[0]?.id);}catch(e){$('previewLoadStatus').textContent=e.message;}}
$('makeCover').onclick=async()=>{if(!jobId||templateDirty||!templateReady)return toast('请先储存字幕模板',true);try{const d=await api(`/api/jobs/${jobId}/cover`,{method:'POST'});$('coverImage').src=d.cover_url+'?t='+Date.now();$('coverImage').style.display='block';$('coverEmpty').style.display='none';toast('2K 封面已生成');}catch(e){toast(e.message,true);}};
async function initialize(){try{fonts=(await api('/api/fonts')).fonts;setSettings(defaults);await loadSavedJobs();}catch(e){$('fontCount').textContent=e.message;toast(e.message,true);}}
initialize();
async function watchRender() {
  clearTimeout(renderTimer);
  const currentJob = jobId;
  if (!currentJob) return;
  try {
    const response = await fetch(`/api/jobs/${currentJob}/render/progress`);
    if (!response.ok) throw Error('无法读取渲染进度');
    const progress = await response.json();
    if (currentJob !== jobId) return;
    $('renderProgress').hidden = progress.status === 'idle';
    $('renderBar').value = progress.percent || 0;
    $('renderStage').textContent = progress.stage;
    const elapsed = progress.status === 'running' && progress.started_at
      ? Math.floor(Date.now() / 1000 - progress.started_at) : progress.elapsed_seconds || 0;
    $('renderNumbers').textContent = `${progress.percent || 0}% · 已用时 ${Math.floor(elapsed / 60)}分${elapsed % 60}秒`;
    $('renderVideo').disabled = progress.status === 'running' || templateDirty || !templateReady;
    if (progress.status === 'running') renderTimer = setTimeout(watchRender, 1500);
  } catch (error) {
    if (currentJob !== jobId) return;
    $('renderProgress').hidden = false;
    $('renderStage').textContent = error.message + '，正在重试…';
    renderTimer = setTimeout(watchRender, 3000);
  }
}
$('renderVideo').onclick = async () => {
  if (!jobId || templateDirty || !templateReady) return toast('请先储存字幕设置并更新预览', true);
  $('renderVideo').disabled = true;
  try {
    const response = await fetch(`/api/jobs/${jobId}/render`, {method: 'POST'});
    const data = await response.json();
    if (!data.ok) throw Error(data.error);
    toast(data.message);
    watchRender();
  } catch (error) {
    $('renderVideo').disabled = false;
    toast(error.message, true);
  }
};
async function loadExportName(){
  if(!jobId)return;
  try{const d=await api(`/api/jobs/${encodeURIComponent(jobId)}/export-name`);$('exportClimax').value=d.climax;$('exportClimax').dataset.auto=d.auto_climax;$('exportNamePreview').textContent=`文件名：${d.name}.mp4`;}
  catch(e){console.warn('导出句加载失败',e.message);}
}
async function saveExportName(climax){
  try{const d=await api(`/api/jobs/${encodeURIComponent(jobId)}/export-name`,jsonOptions('PUT',{climax}));$('exportClimax').value=d.climax;$('exportNamePreview').textContent=`文件名：${d.name}.mp4`;toast('導出文件名已保存');}
  catch(e){toast(e.message,true);}
}
$('saveExportClimax').onclick=()=>{if(!jobId)return toast('请先完成任务',true);saveExportName($('exportClimax').value.trim());};
$('resetExportClimax').onclick=()=>{if(!jobId)return toast('请先完成任务',true);saveExportName('');};
$('makePackage').onclick=async()=>{if(!jobId)return toast('请先完成任务',true);const r=await fetch(`/api/jobs/${jobId}/package`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({copyright_confirmed:$('rights').checked})}),d=await r.json();toast(d.ok?`投稿包：${d.path}`:d.error,!d.ok);};
