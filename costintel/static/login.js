(() => {
  const form = document.querySelector('#connection-form');
  const demo = document.querySelector('#demo-connect');
  const status = document.querySelector('#connection-status');
  const error = document.querySelector('#connection-error');
  const credentials = [...document.querySelectorAll('#credential-fields input')];
  let busy = false;
  document.querySelectorAll('.connection-details').forEach(details => {
    const summary = details.querySelector('summary');
    const body = details.querySelector('.details-body');
    if (!summary || !body) return;
    let animation;
    summary.addEventListener('click', event => {
      event.preventDefault();
      animation?.cancel();
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (details.open && !details.classList.contains('is-closing')) {
        details.classList.add('is-closing');
        if (reducedMotion) { details.open = false; details.classList.remove('is-closing'); return; }
        animation = body.animate([{height: `${body.scrollHeight}px`, opacity: 1}, {height: '0px', opacity: 0}], {duration: 280, easing: 'ease-in-out'});
        animation.onfinish = () => { details.open = false; details.classList.remove('is-closing'); animation = null; };
      } else {
        details.open = true;
        details.classList.remove('is-closing');
        if (reducedMotion) return;
        animation = body.animate([{height: '0px', opacity: 0}, {height: `${body.scrollHeight}px`, opacity: 1}], {duration: 320, easing: 'cubic-bezier(.22,1,.36,1)'});
        animation.onfinish = () => { animation = null; };
      }
    });
  });
  document.querySelectorAll('[data-reveal]').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.reveal);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(show));
    button.title = `${show ? 'Hide' : 'Show'} ${input.id === 'aws-secret-key' ? 'secret access key' : 'session token'}`;
    button.setAttribute('aria-label', `${show ? 'Hide' : 'Show'} ${input.id === 'aws-secret-key' ? 'secret access key' : 'session token'}`);
  }));
  async function connect(payload, trigger) {
    if (busy) return;
    busy = true;
    error.hidden = true;
    status.hidden = false;
    status.textContent = payload.mode === 'demo' ? 'Opening the demo. No AWS connection is needed…' : 'Verifying your AWS identity and collecting inventory…';
    const controls = [...document.querySelectorAll('#connect button, #connect input, #connect select')];
    const states = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    trigger.setAttribute('aria-busy', 'true');
    let connected = false;
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type':'application/json', 'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Connection failed. Check your details and try again.');
      connected = true;
      form?.reset();
      status.textContent = 'Workspace connected. Opening your dashboard…';
      window.location.assign('/');
    } catch (failure) {
      status.hidden = true;
      error.hidden = false;
      error.textContent = failure instanceof TypeError
        ? (document.body.dataset.hosted === 'true' ? 'Could not reach the hosted service. Check your connection, then try again.' : 'Could not reach the local server. Check that CostIntel is running, then try again.')
        : failure.message;
      error.focus();
    } finally {
      if (!connected) {
        controls.forEach((control, index) => { control.disabled = states[index]; });
        trigger.removeAttribute('aria-busy');
        busy = false;
      }
    }
  }
  demo?.addEventListener('click', () => connect({mode:'demo'}, demo));
  form?.addEventListener('submit', event => {
    event.preventDefault();
    const values = new FormData(form);
    const payload = {mode:'aws', region:values.get('region')};
    for (const key of ['access_key_id','secret_access_key','session_token']) payload[key] = String(values.get(key) || '').trim();
    const empty = credentials.find(input => !input.value.trim());
    if (empty) { empty.setCustomValidity('This field cannot contain only spaces.'); empty.reportValidity(); return; }
    connect(payload, document.querySelector('#connect-submit'));
  });
  form?.querySelectorAll('input').forEach(input => input.addEventListener('input', () => input.setCustomValidity('')));
})();
