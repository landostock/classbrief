const toast = (message) => {
  const el = document.querySelector('#toast');
  if (!el) return;
  el.textContent = message; el.classList.add('show');
  window.setTimeout(() => el.classList.remove('show'), 3200);
};

document.querySelectorAll('[data-dialog-open]').forEach(button => button.addEventListener('click', () => {
  document.getElementById(button.dataset.dialogOpen)?.showModal();
}));
document.querySelectorAll('[data-dialog-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog')?.close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
}));
document.querySelectorAll('[data-confirm]').forEach(button => button.addEventListener('click', event => {
  if (!window.confirm(button.dataset.confirm)) event.preventDefault();
}));

const refreshAutomationForm = (form) => {
  const type = form.querySelector('[data-rule-type]')?.value;
  form.querySelectorAll('[data-field]').forEach(field => {
    const visible = field.dataset.field === 'run_time'
      ? ['daily_overview', 'fixed_slot'].includes(type)
      : field.dataset.field === 'target_start_time'
        ? type === 'fixed_slot'
        : ['subject_id', 'lead_minutes'].includes(field.dataset.field) && type === 'subject_before';
    field.hidden = !visible;
    field.querySelectorAll('input,select').forEach(input => input.disabled = !visible);
  });
};
document.querySelectorAll('.automation-form').forEach(form => {
  refreshAutomationForm(form);
  form.querySelector('[data-rule-type]')?.addEventListener('change', () => refreshAutomationForm(form));
});

document.querySelectorAll('.copy-open-button').forEach(button => button.addEventListener('click', () => {
  // A normal link is not blocked as a popup in mobile browsers or installed
  // PWAs. Copy synchronously while the click still carries user activation.
  const input = document.createElement('textarea');
  input.value = button.dataset.copy; input.setAttribute('readonly', ''); input.style.position = 'fixed'; input.style.opacity = '0';
  document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove();
  navigator.clipboard?.writeText(button.dataset.copy).catch(() => {});
  toast('Anweisung kopiert – in ChatGPT einfügen und senden.');
}));

document.querySelectorAll('.copy-value').forEach(button => button.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch (_) {
    const input = document.createElement('textarea');
    input.value = button.dataset.copy; input.style.position = 'fixed'; input.style.opacity = '0';
    document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove();
  }
  button.textContent = 'Kopiert';
  toast('Befehl kopiert. Jetzt im Terminal einfügen.');
}));

document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-tab]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-selected', item === button ? 'true' : 'false'); });
  document.querySelectorAll('.tab-panel').forEach(panel => { const active = panel.id === button.dataset.tab; panel.hidden = !active; panel.classList.toggle('active', active); });
}));

const preparationOverlay = document.querySelector('#preparation-overlay');
preparationOverlay?.querySelector('.overlay-close')?.addEventListener('click', () => {
  preparationOverlay.hidden = true;
  toast('Die Vorbereitung läuft weiter.');
});

document.querySelectorAll('.prepare-button').forEach(button => button.addEventListener('click', async () => {
  const original = button.innerHTML; button.disabled = true; button.textContent = 'Wird vorbereitet …';
  const status = document.querySelector('#preparation-status');
  const title = document.querySelector('#preparation-title');
  const subject = button.closest('article')?.querySelector('h3, .grow > strong')?.textContent?.trim();
  if (title) title.textContent = subject ? `${subject} wird vorbereitet` : 'Vorbereitung wird erstellt';
  if (status) status.textContent = 'Aktuelle Materialien werden gesucht …';
  if (preparationOverlay) preparationOverlay.hidden = false;
  const stages = [
    [5000, 'Neue und veränderte Dateien werden eingeordnet …'],
    [15000, 'Der letzte bestätigte Unterrichtsstand wird verknüpft …'],
    [35000, 'Antworten auf wahrscheinliche Fragen werden formuliert …'],
    [70000, '30 Sekunden, 2 Minuten und Details werden gestaltet …'],
  ];
  const timers = stages.map(([delay, message]) => window.setTimeout(() => { if (status) status.textContent = message; }, delay));
  try {
    const endpoint = button.dataset.lesson ? `/api/lessons/${button.dataset.lesson}/prepare` : `/api/subjects/${button.dataset.subject}/prepare`;
    const response = await fetch(endpoint, {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Vorbereitung fehlgeschlagen');
    if (data.status === 'already_processing') { toast('Die Vorbereitung läuft bereits.'); if (preparationOverlay) preparationOverlay.hidden = true; button.disabled = false; button.innerHTML = original; return; }
    window.location.href = `/briefings/${data.briefing_id}`;
  } catch (error) {
    if (preparationOverlay) preparationOverlay.hidden = true;
    toast(error.message); button.disabled = false; button.innerHTML = original;
  } finally {
    timers.forEach(window.clearTimeout);
  }
}));

document.querySelector('#partial-toggle')?.addEventListener('click', event => {
  const note = event.currentTarget.closest('form').querySelector('.partial-note');
  note.hidden = false; note.querySelector('textarea').focus();
});

if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js'));

let installPrompt;
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); installPrompt = event;
  document.querySelector('#install-app')?.removeAttribute('hidden');
});
document.querySelector('#install-app')?.addEventListener('click', async event => {
  if (window.matchMedia('(display-mode: standalone)').matches) { toast('ClassBrief ist bereits als App installiert.'); return; }
  if (installPrompt) {
    installPrompt.prompt(); await installPrompt.userChoice; installPrompt = undefined; return;
  }
  const help = document.querySelector('#install-help');
  if (help) help.hidden = false;
  event.currentTarget.textContent = 'Anleitung angezeigt';
});

const pushButton = document.querySelector('#enable-push');
if (pushButton) pushButton.addEventListener('click', async () => {
  try {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Benachrichtigungen wurden nicht erlaubt.');
    const registration = await navigator.serviceWorker.ready;
    const {public_key: key} = await fetch('/api/push/public-key').then(response => response.json());
    const normalized = key.replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')), character => character.charCodeAt(0));
    const subscription = await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: bytes});
    const response = await fetch('/api/push/subscribe', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(subscription)});
    if (!response.ok) throw new Error('Push konnte nicht gespeichert werden.');
    pushButton.textContent = 'Aktiviert'; pushButton.disabled = true; toast('Benachrichtigungen sind aktiviert.');
  } catch (error) { toast(error.message); }
});
