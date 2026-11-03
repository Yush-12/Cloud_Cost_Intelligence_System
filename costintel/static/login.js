(() => {
  const form = document.querySelector('#connection-form');
  const demo = document.querySelector('#demo-connect');
  const status = document.querySelector('#connection-status');
  const error = document.querySelector('#connection-error');
  const methods = [...document.querySelectorAll('input[name="method"]')];
  const profile = document.querySelector('#local-profile');
  const credentials = [...document.querySelectorAll('#credential-fields input')];
  let busy = false;
  function setMethod() {
    const temporary = methods.find(input => input.checked)?.value === 'credentials';
    document.querySelector('#profile-fields').hidden = temporary;
    document.querySelector('#credential-fields').hidden = !temporary;
    profile.disabled = temporary;
    profile.required = !temporary;
    credentials.forEach(input => { input.disabled = !temporary; input.required = temporary; });
    error.hidden = true;
  }
  methods.forEach(input => input.addEventListener('change', setMethod));
  if (form) setMethod();
  document.querySelectorAll('[data-reveal]').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.reveal);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.textContent = show ? 'Hide' : 'Show';
    button.setAttribute('aria-pressed', String(show));
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
      error.textContent = failure instanceof TypeError ? 'Could not reach the local server. Check that CostIntel is running, then try again.' : failure.message;
      error.focus();
    } finally {
      if (!connected) {
        controls.forEach((control, index) => { control.disabled = states[index]; });
        trigger.removeAttribute('aria-busy');
        busy = false;
      }
    }
  }
  demo.addEventListener('click', () => connect({mode:'demo'}, demo));
  form?.addEventListener('submit', event => {
    event.preventDefault();
    const values = new FormData(form);
    const payload = {mode:'aws', region:values.get('region')};
    if (!profile.disabled) {
      payload.profile = profile.value.trim();
      if (!payload.profile) { profile.setCustomValidity('Enter your configured AWS CLI profile name.'); profile.reportValidity(); return; }
    } else {
      for (const key of ['access_key_id','secret_access_key','session_token']) payload[key] = String(values.get(key) || '').trim();
      const empty = credentials.find(input => !input.value.trim());
      if (empty) { empty.setCustomValidity('This field cannot contain only spaces.'); empty.reportValidity(); return; }
    }
    connect(payload, document.querySelector('#connect-submit'));
  });
  form?.querySelectorAll('input').forEach(input => input.addEventListener('input', () => input.setCustomValidity('')));
})();
