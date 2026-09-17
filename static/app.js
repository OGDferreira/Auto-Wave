const pageButtons = document.querySelectorAll('.nav-button');
const pages = document.querySelectorAll('.page');
const notifyButton = document.getElementById('notify-button');
const configForm = document.getElementById('config-form');
const importForm = document.getElementById('import-form');
const contasList = document.getElementById('contas-list');
const refreshAccountsButton = document.getElementById('refresh-accounts');
const connectFirstAccountButton = document.getElementById('connect-first-account');
const toastRegion = document.getElementById('toast-region');
let accountsCache = [];
let accountPollingTimer;

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
    throw new Error(body || `Request failed: ${response.status}`);
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
    card.className = 'account-card';
    card.dataset.status = account.status || 'pendente';

    const header = document.createElement('div');
    header.className = 'account-header';
    const title = document.createElement('h4');
    title.textContent = account.username;
    const status = document.createElement('span');
    status.className = 'account-status';
    status.textContent = account.status;
    header.append(title, status);

    const metaToken = document.createElement('p');
    metaToken.textContent = account.meta_access_token ? 'Meta token ativo' : 'Meta token pendente';

    const details = document.createElement('p');
    details.textContent = `Views: ${account.views_count || 0} · Leads: ${account.leads_count || 0}`;

    const action = document.createElement('button');
    action.className = 'account-connect-btn';
    action.type = 'button';
    action.textContent = account.status === 'conectada' ? 'Reconectar conta' : 'Conectar via Playwright';
    action.addEventListener('click', async () => {
      action.disabled = true;
      action.classList.add('is-loading');
      action.textContent = 'Conectando…';
      try {
        await fetchJson(`/contas/${account.id}/conectar`, {
          method: 'POST',
        });
        notify(`Conexão de ${account.username} iniciada em background.`, 'success');
        startAccountPolling();
      } catch (error) {
        notify('Não foi possível iniciar a conexão.', 'error');
      } finally {
        action.disabled = false;
        action.classList.remove('is-loading');
        action.textContent = account.status === 'conectada' ? 'Reconectar conta' : 'Conectar via Playwright';
      }
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
  } catch (error) {
    notify('Não foi possível carregar as contas. Verifique o banco de dados.', 'error');
  }
}

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
    alert('Configuração salva com sucesso');
  } catch (error) {
    console.error(error);
    alert('Erro ao salvar as chaves');
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

if (window.__INITIAL_METRICS__) {
  updateMetrics(window.__INITIAL_METRICS__);
}

const savedPage = window.location.hash.replace('#', '') || window.localStorage.getItem('auto-wave-page') || window.__INITIAL_PAGE__ || 'dashboard';
showPage(savedPage);
loadMetrics();
loadConfig();
loadAccounts();
registerServiceWorker();
