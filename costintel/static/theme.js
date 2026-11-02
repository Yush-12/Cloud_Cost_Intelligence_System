(() => {
  let chosen;
  try {
    chosen = localStorage.getItem('costintel-theme');
  } catch (_) {}
  const media = matchMedia('(prefers-color-scheme: dark)');

  function apply(value) {
    document.documentElement.dataset.theme = value;
    const b = document.querySelector('#theme-toggle');
    if (b) {
      b.textContent = value === 'dark' ? '☀' : '☾';
      b.title = value === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
      b.setAttribute('aria-label', b.title);
      b.setAttribute('aria-pressed', String(value === 'dark'));
    }
  }

  // Restore sidebar collapsed state
  try {
    if (localStorage.getItem('costintel-sidebar-collapsed') === 'true') {
      document.documentElement.classList.add('sidebar-collapsed');
    }
  } catch (_) {}

  apply(['dark', 'light'].includes(chosen) ? chosen : media.matches ? 'dark' : 'light');
  media.addEventListener('change', e => {
    if (!chosen) apply(e.matches ? 'dark' : 'light');
  });

  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.dataset.theme);
    document.querySelector('#theme-toggle')?.addEventListener('click', () => {
      chosen = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem('costintel-theme', chosen);
      } catch (_) {}
      apply(chosen);
    });
  });
})();
