// Runs before styles load so a saved dark preference does not flash a light page.
(() => {
 const media=window.matchMedia('(prefers-color-scheme: dark)');
 let preference=null;
 try { preference=localStorage.getItem('costintel-theme'); } catch (_) {}
 const valid=value=>value==='dark'||value==='light';
 function apply(theme){
  document.documentElement.dataset.theme=theme;
  const button=document.querySelector('#theme-toggle');
  if(button){button.setAttribute('aria-pressed',String(theme==='dark'));button.textContent=theme==='dark'?'☀ Light mode':'☾ Dark mode';}
 }
 apply(valid(preference)?preference:media.matches?'dark':'light');
 media.addEventListener('change',event=>{if(!valid(preference))apply(event.matches?'dark':'light')});
 document.addEventListener('DOMContentLoaded',()=>{
  apply(document.documentElement.dataset.theme);
  document.querySelector('#theme-toggle')?.addEventListener('click',()=>{
   preference=document.documentElement.dataset.theme==='dark'?'light':'dark';
   try { localStorage.setItem('costintel-theme',preference); } catch (_) {}
   apply(preference);
  });
 });
})();
