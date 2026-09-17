const pageButtons = document.querySelectorAll('.nav-button');
const pages = document.querySelectorAll('.page');
const notifyButton = document.getElementById('notify-button');
const testNotifyButton = document.getElementById('test-notify-button');
const configForm = document.getElementById('config-form');
const collaboratorForm = document.getElementById('collaborator-form');
const importForm = document.getElementById('import-form');
const contasList = document.getElementById('contas-list');
const refreshAccountsButton = document.getElementById('refresh-accounts');
const connectFirstAccountButton = document.getElementById('connect-first-account');
const toastRegion = document.getElementById('toast-region');
const scheduleForm = document.getElementById('schedule-form');
const scheduleAccount = document.getElementById('schedule-account');
const scheduleMedia = document.getElementById('schedule-media');
const scheduleFile = document.getElementById('schedule-file');
const mediaUploadStatus = document.getElementById('media-upload-status');
const mediaPreview = document.getElementById('media-preview');
const queueList = document.getElementById('fila-list');
const queueCount = document.getElementById('queue-count');
const calendarStrip = document.getElementById('calendar-strip');
const calendarDayList = document.getElementById('calendar-day-list');
const queueAccountFilter = document.getElementById('queue-account-filter');
const queueAllButton = document.getElementById('queue-all-button');
const logOutput = document.getElementById('log-output');
let accountsCache = [];
let accountPollingTimer;
let logsPollingTimer;
let queuePostsCache = [];
let queueSelectedDay = null;

scheduleMedia?.addEventListener('input', () => {
  const value = scheduleMedia.value.trim();
  if (!mediaPreview) return;
  mediaPreview.replaceChildren();
  if (!value) {
    mediaPreview.textContent = 'Pré-visualização da mídia';
    return;
  }
  const image = document.createElement('img');
  image.src = value;
  image.alt = 'Pré-visualização';
  image.addEventListener('error', () => {
    mediaPreview.replaceChildren();
    mediaPreview.textContent = 'Link informado. A pré-visualização não está disponível.';
  }, { once: true });
  const url = document.createElement('span');
  url.className = 'media-preview-url';
  url.textContent = value;
  mediaPreview.append(image, url);
});

function showPage(pageId) {
  const targetPage = document.getElementById(`page-${pageId}`);
  if (!targetPage) {
    return;
  }

  pages.forEach((page) => {
    page.classList.toggle('active', page.id === `page-${pageId}`);
  });

  pageButtons.forEach((button) => {
    button.classList.toggle('active', button.dataset.page === pageId);
  });

  window.history.replaceState(null, '', `#${pageId}`);
  window.localStorage.setItem('auto-wave-page', pageId);
  requestAnimationFrame(() => {
    targetPage.classList.remove('page-enter');
    void targetPage.offsetWidth;
    targetPage.classList.add('page-enter');
  });
}

pageButtons.forEach((button) => {
  button.addEventListener('click', () => showPage(button.dataset.page));
});

function notify(message, type = 'info') {
  if (!toastRegion) {
    return;
  }
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  toastRegion.appendChild(toast);
  window.setTimeout(() => toast.classList.add('toast-visible'), 20);
  window.setTimeout(() => {
    toast.classList.remove('toast-visible');
    window.setTimeout(() => toast.remove(), 250);
  }, 4000);
}

function updateMetrics(metrics) {
  const fields = {
    'metric-views': metrics.views_total || 0,
    'metric-leads': metrics.leads_total || 0,
    'metric-lead-rate': `${metrics.lead_rate || 0}%`,
    'metric-pix': metrics.pix_pago || 0,
    'conversion-overall': `${metrics.overall_conversion || 0}%`,
    'conversion-pix': `${metrics.pix_rate || 0}%`,
    'valor-total': new Intl.NumberFormat('pt-BR', {
      style: 'currency',
      currency: 'BRL',
    }).format(metrics.valor_total || 0),
  };

  Object.entries(fields).forEach(([id, value]) => {
    const element = document.getElementById(id);
    if (element) {
      element.textContent = value;
    }
  });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    const body = await response.text();
    let message = body || `Request failed: ${response.status}`;
    try {
      const parsed = JSON.parse(body);
      message = parsed.detail || parsed.message || message;
    } catch (error) {
      // Keep the original response when the server did not return JSON.
    }
    throw new Error(message);
  }

  return response.json();
}

async function loadMetrics() {
  try {
    const metrics = await fetchJson('/api/metricas');
    updateMetrics(metrics);
  } catch (error) {
    console.error('Erro ao carregar métricas', error);
  }

}

async function loadLogs() {
  if (!logOutput) return;
  try {
    const payload = await fetchJson('/api/logs');
    logOutput.textContent = payload.logs?.length
      ? payload.logs.join('\n')
      : 'O Render não retornou registros recentes.';
    logOutput.scrollTop = logOutput.scrollHeight;
  } catch (error) {
    logOutput.textContent = error.message || 'Não foi possível carregar os logs do Render.';
  }
}

function startLogsPolling() {
  window.clearInterval(logsPollingTimer);
  loadLogs();
  logsPollingTimer = window.setInterval(loadLogs, 5000);
}

async function loadConfig() {
  try {
    const config = await fetchJson('/api/config');
    Object.entries(config).forEach(([key, value]) => {
      const element = document.getElementById(key);
      if (element) {
        element.value = value || '';
      }
    });
  } catch (error) {
    console.error('Erro ao carregar configuração', error);
  }
}

function renderAccounts(accounts) {
  if (!contasList) {
    return;
  }

  contasList.innerHTML = '';

  if (!accounts.length) {
    contasList.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">◎</div>
        <strong>Nenhuma conta adicionada</strong>
        <span>Importe uma conta acima para começar a conexão.</span>
      </div>
    `;
    return;
  }

  accounts.forEach((account) => {
    const card = document.createElement('article');
    card.className = 'account-card legacy-inspired';
    card.dataset.status = account.status || 'pendente';

    const avatar = document.createElement('div');
    avatar.className = 'account-avatar';
    avatar.textContent = (account.username || '?').replace(/^@/, '').slice(0, 1).toUpperCase();

    const header = document.createElement('div');
    header.className = 'account-header';
    header.appendChild(avatar);
    const identity = document.createElement('div');
    identity.className = 'account-identity';
    const title = document.createElement('h4');
    title.textContent = `@${String(account.username).replace(/^@/, '')}`;
    const status = document.createElement('span');
    status.className = 'account-status';
    status.textContent = account.status === 'conectada' ? 'Conectada' : account.status === 'suspensa' ? 'Suspensa' : 'Pendente';
    identity.append(title, status);
    header.appendChild(identity);

    const metaToken = document.createElement('p');
    metaToken.textContent = account.meta_access_token ? 'Meta token ativo' : 'Meta token pendente';

    const details = document.createElement('p');
    details.className = 'account-metrics';
    details.textContent = `${Number(account.views_count || 0).toLocaleString('pt-BR')} visualizações · ${Number(account.leads_count || 0).toLocaleString('pt-BR')} leads`;

    const action = document.createElement('button');
    action.className = 'account-connect-btn';
    action.type = 'button';
    action.textContent = account.status === 'conectada' ? 'Reconectar via Meta' : 'Conectar via Meta';
    action.addEventListener('click', async () => {
      window.location.href = `/auth/meta/login?account_id=${encodeURIComponent(account.id)}`;
    });

    card.appendChild(header);
    card.appendChild(metaToken);
    card.appendChild(details);
    card.appendChild(action);
    contasList.appendChild(card);
  });
}

async function loadAccounts() {
  try {
    const accounts = await fetchJson('/api/contas');
    accountsCache = accounts;
    renderAccounts(accounts);
    if (queueAccountFilter) {
      queueAccountFilter.innerHTML = '<option value="all">Todas as contas</option>' +
        accounts.map((account) => `<option value="${account.id}">${account.username}</option>`).join('');
    }

    scheduleFile?.addEventListener('change', async () => {
      const file = scheduleFile.files?.[0];
      if (!file) return;
      mediaUploadStatus.textContent = 'Enviando mídia para o Storage…';
      mediaUploadStatus.className = 'media-upload-status is-loading';
      try {
        const formData = new FormData();
        formData.append('media', file);
        const response = await fetch('/api/media/upload', { method: 'POST', body: formData });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Não foi possível enviar a mídia.');
        scheduleMedia.value = payload.url;
        scheduleMedia.dispatchEvent(new Event('input'));
        mediaUploadStatus.textContent = `${file.name} importado com sucesso.`;
        mediaUploadStatus.className = 'media-upload-status is-success';
      } catch (error) {
        mediaUploadStatus.textContent = error.message || 'Falha ao importar a mídia.';
        mediaUploadStatus.className = 'media-upload-status is-error';
      }
    });
    if (scheduleAccount) {
      scheduleAccount.innerHTML = accounts.length
        ? accounts.map((account) => `<option value="${account.id}">${account.username} · ${account.status}</option>`).join('')
        : '<option value="">Nenhuma conta disponível</option>';
    }
  } catch (error) {
    notify('Não foi possível carregar as contas. Verifique o banco de dados.', 'error');
  }

}

function renderQueue(posts) {
    if (!queueList) return;
    queuePostsCache = posts;
    const filterValue = queueAccountFilter?.value || 'all';
    const visiblePosts = filterValue === 'all'
      ? posts
      : posts.filter((post) => String(post.account_id) === filterValue);
    queueCount.textContent = `${posts.length} ${posts.length === 1 ? 'item' : 'itens'}`;
    if (!visiblePosts.length) {
      queueList.innerHTML = '<div class="empty-state"><div class="empty-state-icon">◷</div><strong>Fila vazia</strong><span>Agende sua primeira publicação para começar.</span></div>';
    } else {
      queueList.innerHTML = visiblePosts.map((post) => {
        const date = new Date(post.scheduled_for);
        const account = accountsCache.find((item) => item.id === post.account_id);
        return `<article class="queue-card"><div class="queue-thumb">▧</div><div class="queue-card-body"><strong>${account?.username || `Conta #${post.account_id}`}</strong><span>${date.toLocaleString('pt-BR')}</span><small>${post.caption || 'Sem legenda'}</small></div><span class="status-chip">${post.status}</span><button class="queue-delete" data-post-id="${post.id}" type="button">×</button></article>`;
      }).join('');
      queueList.querySelectorAll('.queue-delete').forEach((button) => {
        button.addEventListener('click', async () => {
          await fetchJson(`/api/fila/${button.dataset.postId}`, { method: 'DELETE' });
          notify('Publicação removida da fila.', 'success');
          loadQueue();
        });
      });
    }
    const days = [...new Set(visiblePosts.map((post) => new Date(post.scheduled_for).toISOString().slice(0, 10)))];
    calendarStrip.innerHTML = days.length
      ? days.map((day) => `<button class="calendar-day ${queueSelectedDay === day ? 'selected' : ''}" data-day="${day}" type="button">${new Date(`${day}T12:00:00`).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', weekday: 'short' })}</button>`).join('')
      : '<span class="calendar-day calendar-muted">Nenhum dia agendado</span>';
    calendarStrip.querySelectorAll('[data-day]').forEach((button) => {
      button.addEventListener('click', () => {
        queueSelectedDay = queueSelectedDay === button.dataset.day ? null : button.dataset.day;
        renderQueue(queuePostsCache);
      });
    });
    const dayPosts = queueSelectedDay
      ? visiblePosts.filter((post) => new Date(post.scheduled_for).toISOString().slice(0, 10) === queueSelectedDay)
      : [];
    calendarDayList.innerHTML = dayPosts.length
      ? `<div class="calendar-day-heading">Publicações de ${new Date(`${queueSelectedDay}T12:00:00`).toLocaleDateString('pt-BR')}</div>` +
        dayPosts.map((post) => `<div class="calendar-post"><strong>${new Date(post.scheduled_for).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</strong><span>${accountsCache.find((account) => account.id === post.account_id)?.username || 'Conta'}</span><small>${post.caption || post.media_url}</small></div>`).join('')
      : '';
  }

  async function loadQueue() {
    try {
      const posts = await fetchJson('/api/fila');
      renderQueue(posts);
    } catch (error) {
      if (queueList) {
        queueList.innerHTML = '<div class="empty-state"><strong>Fila indisponível</strong><span>Não foi possível carregar as publicações agora.</span></div>';
      }
      console.error('Erro ao carregar fila', error);
    }

}

queueAccountFilter?.addEventListener('change', () => {
    queueSelectedDay = null;
    renderQueue(queuePostsCache);
});
queueAllButton?.addEventListener('click', () => {
    queueAccountFilter.value = 'all';
    queueSelectedDay = null;
    renderQueue(queuePostsCache);
});

scheduleForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await fetchJson('/api/fila/agendar', {
        method: 'POST',
        body: JSON.stringify({
          account_id: Number(scheduleAccount.value),
          media_url: document.getElementById('schedule-media').value,
          caption: document.getElementById('schedule-caption').value,
          scheduled_for: new Date(document.getElementById('schedule-date').value).toISOString(),
        }),
      });
      scheduleForm.reset();
      notify('Publicação adicionada à fila.', 'success');
      loadQueue();
    } catch (error) {
      notify('Preencha conta, mídia e horário corretamente.', 'error');
    }
});

function startAccountPolling() {
  window.clearInterval(accountPollingTimer);
  accountPollingTimer = window.setInterval(async () => {
    await loadAccounts();
    if (accountsCache.every((account) => account.status !== 'pendente')) {
      window.clearInterval(accountPollingTimer);
    }
  }, 5000);
}

configForm?.addEventListener('submit', async (event) => {
  event.preventDefault();

  const body = {
    meta_app_id: document.getElementById('meta_app_id').value,
    meta_app_secret: document.getElementById('meta_app_secret').value,
    meta_webhook_verify_token: document.getElementById('meta_webhook_verify_token').value,
    vapid_public_key: document.getElementById('vapid_public_key').value,
    vapid_private_key: document.getElementById('vapid_private_key').value,
  };

  try {
    await fetchJson('/api/config', {
      method: 'POST',
      body: JSON.stringify(body),
    });

    notify('Configuração salva com sucesso.', 'success');
  } catch (error) {
    console.error(error);
    notify(error.message || 'Erro ao salvar as chaves.', 'error');
  }
});

collaboratorForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await fetchJson('/api/auth/collaborators', {
      method: 'POST',
      body: JSON.stringify({
        username: document.getElementById('collaborator-username').value,
        password: document.getElementById('collaborator-password').value,
      }),
    });
    collaboratorForm.reset();
    notify('Colaborador criado com sucesso.', 'success');
  } catch (error) {
    notify(error.message || 'Não foi possível criar o colaborador.', 'error');
  }
});

importForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const textarea = document.getElementById('contas-input');

  try {
    await fetch('/contas/importar', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
      },
      body: new URLSearchParams({ contas: textarea.value }).toString(),
    });
    textarea.value = '';
    await loadAccounts();
    notify('Contas importadas. Elas já estão disponíveis para conexão.', 'success');
  } catch (error) {
    notify('Erro ao importar contas.', 'error');
  }
});

refreshAccountsButton?.addEventListener('click', async () => {
  refreshAccountsButton.classList.add('is-loading');
  await loadAccounts();
  refreshAccountsButton.classList.remove('is-loading');
  notify('Lista de contas atualizada.', 'success');
});

connectFirstAccountButton?.addEventListener('click', async () => {
  const pending = accountsCache.filter((account) => account.status === 'pendente');
  if (!pending.length) {
    notify('Não há contas pendentes para conectar.', 'info');
    return;
  }
  connectFirstAccountButton.disabled = true;
  connectFirstAccountButton.textContent = 'Conectando…';
  for (const account of pending) {
    try {
      await fetchJson(`/contas/${account.id}/conectar`, { method: 'POST' });
    } catch (error) {
      notify(`Falha ao iniciar ${account.username}.`, 'error');
    }
  }
  connectFirstAccountButton.disabled = false;
  connectFirstAccountButton.textContent = 'Conectar pendentes';
  notify(`${pending.length} conexão(ões) enviada(s) para a fila.`, 'success');
  startAccountPolling();
});

async function registerServiceWorker() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return;
  }

  try {
    await navigator.serviceWorker.register('/static/sw.js', { scope: '/' });
  } catch (error) {
    console.error('Erro ao registrar service worker', error);
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const output = new Uint8Array(rawData.length);

  for (let i = 0; i < rawData.length; ++i) {
    output[i] = rawData.charCodeAt(i);
  }

  return output;
}

async function enablePushNotifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    alert('Push notifications não são suportadas neste navegador');
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    alert('Permissão de notificação negada');
    return;
  }

  const registration = await navigator.serviceWorker.ready;
  const publicKey = document.getElementById('vapid_public_key').value;

  if (!publicKey) {
    alert('Cadastre a chave VAPID antes de ativar as notificações');
    return;
  }

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });

  await fetchJson('/api/push/subscribe', {
    method: 'POST',
    body: JSON.stringify(subscription.toJSON()),
  });

  alert('Push notifications ativadas com sucesso');
}

notifyButton?.addEventListener('click', enablePushNotifications);

testNotifyButton?.addEventListener('click', async () => {
  testNotifyButton.disabled = true;
  try {
    await fetchJson('/api/push/send', {
      method: 'POST',
      body: JSON.stringify({
        title: 'Auto-Wave',
        body: 'Esta é uma notificação de teste do seu painel.',
      }),
    });
    notify('Notificação de teste enviada.', 'success');
  } catch (error) {
    notify(error.message || 'Não foi possível enviar a notificação de teste.', 'error');
  } finally {
    testNotifyButton.disabled = false;
  }
});

async function bootPanel() {
  try {
    const authResponse = await fetch('/api/auth/me', {
      headers: { 'Cache-Control': 'no-cache' },
      cache: 'no-store',
    });
    if (authResponse.status === 401) {
      window.location.replace('/login');
      return;
    }
    if (!authResponse.ok) {
      throw new Error(`Falha ao validar a sessão (HTTP ${authResponse.status}). Tente atualizar novamente.`);
    }
    const user = await authResponse.json();
    if (user.role === 'collaborator') {
      document.querySelectorAll('[data-page="dashboard"], [data-page="config"], [data-page="fila"], [data-page="logs"]').forEach((button) => button.remove());
      document.querySelector('.owner-only')?.remove();
    }
    if (window.__INITIAL_METRICS__) updateMetrics(window.__INITIAL_METRICS__);
    const savedPage = window.location.hash.replace('#', '') || window.localStorage.getItem('auto-wave-page') || window.__INITIAL_PAGE__ || 'dashboard';
    showPage(user.role === 'collaborator' ? 'contas' : savedPage);
    loadMetrics();
    loadConfig();
    loadAccounts();
    loadQueue();
    startLogsPolling();
    registerServiceWorker();
  } catch (error) {
    console.error('Falha ao iniciar o painel', error);
    notify(error.message || 'Não foi possível iniciar o painel.', 'error');
  }
}

bootPanel();
