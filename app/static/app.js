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
const preparationProgressState = document.querySelector('#preparation-progress-state');
const clarificationState = document.querySelector('#clarification-state');
const clarificationQuestions = document.querySelector('#clarification-questions');
const clarificationDock = document.querySelector('#clarification-dock');
let currentClarification;
let preparationTimers = [];

const clearPreparationTimers = () => {
  preparationTimers.forEach(window.clearTimeout);
  preparationTimers = [];
};

const showProgress = (subject = '') => {
  currentClarification = undefined;
  clarificationDock.hidden = true;
  clarificationState.hidden = true;
  preparationProgressState.hidden = false;
  const title = document.querySelector('#preparation-title');
  const status = document.querySelector('#preparation-status');
  if (title) title.textContent = subject ? `${subject} wird vorbereitet` : 'Vorbereitung wird erstellt';
  if (status) status.textContent = 'Aktuelle Materialien werden gesucht …';
  preparationOverlay.hidden = false;
};

const renderClarification = (item, open = true) => {
  currentClarification = item;
  clearPreparationTimers();
  clarificationQuestions.replaceChildren();
  document.querySelector('#clarification-title').textContent = `Eine kurze Frage zu ${item.subject}`;
  item.questions.forEach((question, questionIndex) => {
    const fieldset = document.createElement('fieldset');
    fieldset.className = 'clarification-question';
    const legend = document.createElement('legend');
    legend.textContent = `${questionIndex + 1}. ${question.prompt}`;
    fieldset.appendChild(legend);
    if (question.why_it_matters) {
      const reason = document.createElement('small');
      reason.textContent = question.why_it_matters;
      fieldset.appendChild(reason);
    }
    question.options.forEach(option => {
      const label = document.createElement('label');
      label.className = 'clarification-option';
      const input = document.createElement('input');
      input.type = 'radio'; input.name = question.id; input.value = option.id;
      const text = document.createElement('span');
      text.textContent = option.label;
      label.append(input, text); fieldset.appendChild(label);
    });
    clarificationQuestions.appendChild(fieldset);
  });
  preparationProgressState.hidden = true;
  clarificationState.hidden = false;
  document.querySelector('#clarification-dock-subject').textContent = `${item.subject} · ${item.questions.length === 1 ? '1 Frage' : `${item.questions.length} Fragen`}`;
  clarificationDock.hidden = open;
  if (open) preparationOverlay.hidden = false;
};

preparationOverlay?.querySelector('.overlay-close')?.addEventListener('click', () => {
  preparationOverlay.hidden = true;
  if (currentClarification) {
    clarificationDock.hidden = false;
    toast('Die Rückfrage bleibt unten für dich geöffnet.');
  } else {
    toast('Die Vorbereitung läuft weiter.');
  }
});
clarificationDock?.addEventListener('click', () => {
  clarificationDock.hidden = true;
  preparationOverlay.hidden = false;
});

const finishPreparation = async endpoint => {
  const response = await fetch(endpoint, {method: 'POST'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Vorbereitung fehlgeschlagen');
  if (data.status === 'already_processing') {
    toast('Die Vorbereitung läuft bereits.');
    preparationOverlay.hidden = true;
    return false;
  }
  window.location.href = `/briefings/${data.briefing_id}`;
  return true;
};

document.querySelectorAll('.prepare-button').forEach(button => button.addEventListener('click', async () => {
  const original = button.innerHTML; button.disabled = true; button.textContent = 'Wird vorbereitet …';
  const status = document.querySelector('#preparation-status');
  const subject = button.closest('article')?.querySelector('h3, .grow > strong')?.textContent?.trim();
  showProgress(subject);
  const stages = [
    [5000, 'Neue und veränderte Dateien werden eingeordnet …'],
    [15000, 'Der letzte bestätigte Unterrichtsstand wird geprüft …'],
    [35000, 'ClassBrief prüft, ob eine kurze Rückfrage helfen würde …'],
  ];
  preparationTimers = stages.map(([delay, message]) => window.setTimeout(() => { if (status) status.textContent = message; }, delay));
  try {
    const base = button.dataset.lesson ? `/api/lessons/${button.dataset.lesson}` : `/api/subjects/${button.dataset.subject}`;
    const response = await fetch(`${base}/clarifications`, {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Vorbereitung fehlgeschlagen');
    if (data.status === 'questions') {
      renderClarification(data.clarification, true);
      toast('Eine kurze Rückfrage macht dein Briefing genauer.');
    } else if (data.status === 'ready') {
      window.location.href = `/briefings/${data.briefing_id}`;
    } else if (data.status === 'already_processing') {
      preparationOverlay.hidden = true; toast('Die Vorbereitung läuft bereits.');
    } else {
      clearPreparationTimers();
      if (status) status.textContent = '30 Sekunden, 2 Minuten und Details werden gestaltet …';
      await finishPreparation(`${base}/prepare`);
    }
  } catch (error) {
    if (preparationOverlay) preparationOverlay.hidden = true;
    toast(error.message);
  } finally {
    if (!currentClarification) clearPreparationTimers();
    button.disabled = false; button.innerHTML = original;
  }
}));

const submitClarification = async skip => {
  if (!currentClarification) return;
  const answers = {};
  if (!skip) {
    for (const question of currentClarification.questions) {
      const selected = clarificationState.querySelector(`input[name="${CSS.escape(question.id)}"]:checked`);
      if (!selected) { toast('Bitte beantworte jede Frage oder fahre ohne Antworten fort.'); return; }
      answers[question.id] = selected.value;
    }
  }
  const id = currentClarification.id;
  const subject = currentClarification.subject;
  showProgress(subject);
  document.querySelector('#preparation-status').textContent = 'Deine Antworten werden ins Briefing eingearbeitet …';
  try {
    const response = await fetch(`/api/clarifications/${id}/answer`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({answers, skip}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Antworten konnten nicht übernommen werden.');
    if (data.status === 'already_processing') { preparationOverlay.hidden = true; toast('Die Vorbereitung läuft bereits.'); return; }
    window.location.href = `/briefings/${data.briefing_id}`;
  } catch (error) {
    toast(error.message);
    currentClarification = undefined;
    const pending = await fetch('/api/clarifications/pending').then(response => response.json()).catch(() => null);
    if (pending?.status === 'questions') renderClarification(pending.clarification, true);
  }
};

clarificationState?.addEventListener('submit', event => { event.preventDefault(); submitClarification(false); });
document.querySelector('#clarification-skip')?.addEventListener('click', () => submitClarification(true));

if (!['/login', '/access'].includes(window.location.pathname)) {
  fetch('/api/clarifications/pending').then(response => response.json()).then(data => {
    if (data.status !== 'questions') return;
    const requested = new URLSearchParams(window.location.search).get('clarification');
    renderClarification(data.clarification, requested === String(data.clarification.id));
  }).catch(() => {});
}

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
