// SENTINEL-X — connexion (externalisé pour CSP sans 'unsafe-inline')
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = document.getElementById('err');
  err.textContent = '';
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  try {
    const r = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      err.textContent = j.detail || 'Échec de connexion';
      return;
    }
    location.href = '/';
  } catch (ex) {
    err.textContent = 'Erreur réseau';
  }
});
