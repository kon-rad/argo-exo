// The waitlist form: POST /api/waitlist {email}. The server answers ok whether or not the email was already listed.
const form = document.getElementById('waitlist-form');
const status = document.getElementById('waitlist-status');
const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$/;

if (form) {
  const msg = JSON.parse(form.dataset.msg);
  const input = form.querySelector('input[type=email]');
  const button = form.querySelector('button');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = input.value.trim();
    if (!EMAIL.test(email)) { status.textContent = msg.invalid; input.focus(); return; }
    button.disabled = true;
    status.textContent = '';
    try {
      const r = await fetch('/api/waitlist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }),
      });
      if (r.ok) { status.textContent = msg.thanks; form.reset(); }
      else status.textContent = r.status === 400 ? msg.invalid : r.status === 429 ? msg.busy : msg.failed;
    } catch {
      status.textContent = msg.failed;
    } finally {
      button.disabled = false;
    }
  });
}
