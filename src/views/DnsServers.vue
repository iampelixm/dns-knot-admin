<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="title-row">
        <div class="title">DNS-серверы</div>
        <nav class="nav-links">
          <router-link class="nav-link" to="/">Зоны</router-link>
          <router-link class="nav-link" to="/dns-servers">DNS-серверы</router-link>
          <router-link class="nav-link" to="/knot-conf">knot.conf</router-link>
          <router-link class="nav-link" to="/ingress-wizard">Ingress</router-link>
        </nav>
      </div>
      <el-button link type="danger" @click="logout">Выйти</el-button>
    </el-header>

    <el-main class="main">
      <el-card shadow="never">
        <template #header>
          <div class="card-header">
            <span class="card-title">Серверы</span>
            <el-button
              type="primary"
              size="small"
              :loading="loading"
              @click="loadServers"
              style="margin-left: auto; margin-right: 8px"
            >Обновить</el-button>
            <el-button type="warning" size="small" @click="openCreateDialog">+ Добавить сервер</el-button>
          </div>
        </template>

        <el-table
          :data="servers"
          border
          size="small"
          v-loading="loading"
          empty-text="Нет DNS-серверов"
        >
          <el-table-column label="Имя" prop="name" min-width="160">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEditDialog(row)">{{ row.name }}</el-button>
            </template>
          </el-table-column>
          <el-table-column label="Тип" prop="server_type" width="100">
            <template #default="{ row }">
              {{ serverTypeLabel(row.server_type) }}
            </template>
          </el-table-column>
          <el-table-column label="Адрес" prop="address" width="160" />
          <el-table-column label="Порт" prop="port" width="65" />
          <el-table-column label="Зон" prop="zone_count" width="55" align="center" />
          <el-table-column label="TSIG-ключ" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.tsig_key_id" size="small" type="success">есть</el-tag>
              <el-tag v-else size="small" type="info">нет</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Заметки" prop="notes" min-width="130">
            <template #default="{ row }">
              <span class="notes-cell">{{ row.notes }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Действия" width="340" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openViewDialog(row)">Просмотр</el-button>
              <el-button size="small" @click="showInstruction(row)">Инструкция</el-button>
              <el-button size="small" @click="showConfig(row)">Конфиг</el-button>
              <el-button
                size="small"
                :disabled="row.delivery_method === 'manual'"
                @click="fetchAgentConfig(row)"
              >С сервера</el-button>
              <el-button
                size="small"
                :type="row.delivery_method === 'manual' ? 'default' : 'primary'"
                :disabled="row.delivery_method === 'manual'"
                @click="deliverToServer(row)"
              >Доставить</el-button>
              <el-button size="small" type="danger" @click="confirmDelete(row)">Удалить</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-main>
  </el-container>

  <el-dialog v-model="viewDialogVisible" title="Просмотр сервера" width="640px">
    <el-descriptions :column="2" border size="small">
      <el-descriptions-item label="Имя">{{ viewingServer?.name }}</el-descriptions-item>
      <el-descriptions-item label="Тип">{{ serverTypeLabel(viewingServer?.server_type ?? '') }}</el-descriptions-item>
      <el-descriptions-item label="Адрес">{{ viewingServer?.address }}</el-descriptions-item>
      <el-descriptions-item label="Порт">{{ viewingServer?.port }}</el-descriptions-item>
      <el-descriptions-item label="Зон">{{ viewingServer?.zone_count }}</el-descriptions-item>
      <el-descriptions-item label="TSIG-ключ">
        <el-tag v-if="viewingServer?.tsig_key_id" size="small" type="success">{{ viewingServer.tsig_key_id }}</el-tag>
        <el-tag v-else size="small" type="info">нет</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="Доставка">{{ viewingServer?.delivery_method }}</el-descriptions-item>
      <el-descriptions-item label="Создан">{{ viewingServer?.created_at }}</el-descriptions-item>
      <el-descriptions-item label="Обновлён">{{ viewingServer?.updated_at }}</el-descriptions-item>
      <el-descriptions-item label="delivery_config" :span="2">
        <pre class="json-cell">{{ prettyJson(viewingServer?.delivery_config) }}</pre>
      </el-descriptions-item>
      <el-descriptions-item label="Заметки" :span="2">
        {{ viewingServer?.notes }}
      </el-descriptions-item>
    </el-descriptions>
    <template #footer>
      <el-button @click="viewDialogVisible = false">Закрыть</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="createDialogVisible" :title="editingServer ? 'Редактировать сервер' : 'Добавить сервер'" width="640px">
    <el-form label-position="top">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Имя" required>
            <el-input v-model="form.name" placeholder="ns1.example.com" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Тип" required>
            <el-select v-model="form.server_type" style="width: 100%">
              <el-option
                v-for="(info, key) in serverTypes"
                :key="key"
                :label="info.label"
                :value="key"
              />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="16">
          <el-form-item label="Адрес" required>
            <el-input v-model="form.address" placeholder="192.168.1.1 или ns1.example.com" />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item label="Порт" required>
            <el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" style="width: 100%" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="TSIG-ключ">
            <el-input v-model="form.tsig_key_id" placeholder="ID ключа (оставьте пустым если не нужен)" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label=" ">
            <el-checkbox v-model="form.create_key" :disabled="!!form.tsig_key_id">
              Создать новый ключ
            </el-checkbox>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Способ доставки">
            <el-select v-model="form.delivery_method" style="width: 100%">
              <el-option label="Вручную" value="manual" />
              <el-option label="Через агента" value="agent" />
              <el-option label="HTTP API" value="api" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <template v-if="form.delivery_method === 'agent'">
        <el-row :gutter="16">
          <el-col :span="24">
            <el-form-item label=" ">
              <el-checkbox v-model="form.in_cluster">В кластере K8s</el-checkbox>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="16">
            <el-form-item label="URL агента" required>
              <el-input v-model="form.agent_url" placeholder="http://10.200.0.4:8080" class="mono" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="API-ключ" required>
              <el-input v-model="form.api_key" type="password" show-password />
            </el-form-item>
          </el-col>
        </el-row>
        <template v-if="form.in_cluster">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="ConfigMap">
                <el-input v-model="form.configmap_name" placeholder="knot-config" class="mono" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Deployment">
                <el-input v-model="form.deployment_name" placeholder="knot" class="mono" />
              </el-form-item>
            </el-col>
          </el-row>
        </template>
        <template v-else>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="Путь к конфигу">
                <el-input v-model="form.config_path" placeholder="/etc/knot/knot.conf" class="mono" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Команда reload">
                <el-input v-model="form.reload_cmd" placeholder="knotc reload" class="mono" />
              </el-form-item>
            </el-col>
          </el-row>
        </template>
      </template>
      <el-form-item label="Заметки">
        <el-input v-model="form.notes" type="textarea" :rows="2" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="createDialogVisible = false">Отмена</el-button>
      <el-button type="primary" :loading="saving" @click="submitForm">
        {{ editingServer ? 'Сохранить' : 'Создать' }}
      </el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="instructionDialogVisible" title="Инструкция" width="720px">
    <pre class="mono-text">{{ instructionText }}</pre>
    <template #footer>
      <el-button @click="instructionDialogVisible = false">Закрыть</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="configDialogVisible" title="Конфигурация сервера" width="800px">
    <pre class="mono-text">{{ configText }}</pre>
    <template #footer>
      <el-button @click="configDialogVisible = false">Закрыть</el-button>
      <el-button type="primary" @click="copyConfig">Копировать</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  AUTH_TOKEN_KEY,
  type DnsServer,
  type ServerTypeInfo,
  fetchDnsServers,
  fetchServerTypes,
  fetchServerInstruction,
  fetchServerConfig,
  fetchDeliver,
  fetchServerConfigFromAgent,
  createDnsServer,
  updateDnsServer,
  deleteDnsServer,
} from "../api/client";
import { messageFromAxios } from "../utils/dns";

const router = useRouter();

const servers = ref<DnsServer[]>([]);
const serverTypes = ref<Record<string, ServerTypeInfo>>({});
const loading = ref(false);

// View dialog
const viewDialogVisible = ref(false);
const viewingServer = ref<DnsServer | null>(null);

// Create / Edit dialog
const createDialogVisible = ref(false);
const saving = ref(false);
const editingServer = ref<DnsServer | null>(null);

// Instruction dialog
const instructionDialogVisible = ref(false);
const instructionText = ref("");

// Config dialog
const configDialogVisible = ref(false);
const configText = ref("");
const configServerId = ref("");

const form = reactive({
  name: "",
  server_type: "knot",
  address: "",
  port: 53,
  tsig_key_id: "",
  create_key: false,
  delivery_method: "manual",
  delivery_config: "{}",
  notes: "",
  zone_count: 0,
  agent_url: "",
  api_key: "",
  config_path: "/etc/knot/knot.conf",
  reload_cmd: "knotc reload",
  in_cluster: false,
  configmap_name: "knot-config",
  deployment_name: "knot",
});

async function loadServers() {
  loading.value = true;
  try {
    const resp = await fetchDnsServers();
    servers.value = resp.servers;
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить серверы"));
  } finally {
    loading.value = false;
  }
}

async function loadTypes() {
  try {
    const resp = await fetchServerTypes();
    serverTypes.value = resp.types;
  } catch {
    // non-critical
  }
}

function serverTypeLabel(type: string): string {
  return serverTypes.value[type]?.label ?? type;
}

function prettyJson(raw: string | undefined | null): string {
  if (!raw) return "{}";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function resetForm() {
  form.name = "";
  form.server_type = "knot";
  form.address = "";
  form.port = 53;
  form.tsig_key_id = "";
  form.create_key = false;
  form.delivery_method = "manual";
  form.delivery_config = "{}";
  form.notes = "";
  form.zone_count = 0;
  form.agent_url = "";
  form.api_key = "";
  form.config_path = "/etc/knot/knot.conf";
  form.reload_cmd = "knotc reload";
  editingServer.value = null;
}

function openCreateDialog() {
  resetForm();
  createDialogVisible.value = true;
}

function openViewDialog(server: DnsServer) {
  viewingServer.value = server;
  viewDialogVisible.value = true;
}

function openEditDialog(server: DnsServer) {
  editingServer.value = server;
  form.name = server.name;
  form.server_type = server.server_type;
  form.address = server.address;
  form.port = server.port;
  form.tsig_key_id = server.tsig_key_id ?? "";
  form.create_key = false;
  form.delivery_method = server.delivery_method;
  form.delivery_config = server.delivery_config;
  form.notes = server.notes;
  form.zone_count = server.zone_count;
  try {
    const cfg = JSON.parse(server.delivery_config || "{}");
    form.agent_url = cfg.agent_url ?? "";
    form.api_key = cfg.api_key ?? "";
    form.config_path = cfg.config_path ?? "/etc/knot/knot.conf";
    form.reload_cmd = cfg.reload_cmd ?? "knotc reload";
    form.in_cluster = cfg.in_cluster ?? false;
    form.configmap_name = cfg.configmap_name ?? "knot-config";
    form.deployment_name = cfg.deployment_name ?? "knot";
  } catch {
    form.agent_url = "";
    form.api_key = "";
    form.config_path = "/etc/knot/knot.conf";
    form.reload_cmd = "knotc reload";
    form.in_cluster = false;
    form.configmap_name = "knot-config";
    form.deployment_name = "knot";
  }
  createDialogVisible.value = true;
}

function buildDeliveryConfig(): string {
  if (form.delivery_method === "agent") {
    const cfg: Record<string, unknown> = {
      agent_url: form.agent_url,
      api_key: form.api_key,
      config_path: form.config_path,
      reload_cmd: form.reload_cmd,
      in_cluster: form.in_cluster,
    };
    if (form.in_cluster) {
      cfg.configmap_name = form.configmap_name;
      cfg.deployment_name = form.deployment_name;
    }
    return JSON.stringify(cfg);
  }
  if (form.delivery_method === "api") {
    return JSON.stringify({
      api_url: form.agent_url,
      api_key: form.api_key,
    });
  }
  return "{}";
}

async function submitForm() {
  if (!form.name.trim() || !form.address.trim()) {
    ElMessage.warning("Заполните имя и адрес сервера");
    return;
  }

  const body: Record<string, unknown> = {
    name: form.name.trim(),
    server_type: form.server_type,
    address: form.address.trim(),
    port: form.port,
    delivery_method: form.delivery_method,
    delivery_config: buildDeliveryConfig(),
    notes: form.notes,
  };
  if (form.tsig_key_id.trim()) {
    body.tsig_key_id = form.tsig_key_id.trim();
  }
  if (form.create_key) {
    body.create_key = true;
  }

  saving.value = true;
  try {
    if (editingServer.value) {
      await updateDnsServer(editingServer.value.id, body);
      ElMessage.success("Сервер обновлён");
    } else {
      await createDnsServer(body as any);
      ElMessage.success("Сервер создан");
    }
    createDialogVisible.value = false;
    await loadServers();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось сохранить сервер"));
  } finally {
    saving.value = false;
  }
}

async function showInstruction(server: DnsServer) {
  try {
    const resp = await fetchServerInstruction(server.id);
    instructionText.value = resp.instruction;
    instructionDialogVisible.value = true;
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить инструкцию"));
  }
}

async function showConfig(server: DnsServer) {
  configServerId.value = server.id;
  configText.value = "Загрузка...";
  configDialogVisible.value = true;
  try {
    const resp = await fetchServerConfig(server.id);
    configText.value = resp.config;
  } catch (e) {
    configText.value = "Ошибка загрузки: " + messageFromAxios(e, "");
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить конфиг"));
  }
}

async function copyConfig() {
  try {
    await navigator.clipboard.writeText(configText.value);
    ElMessage.success("Скопировано");
  } catch {
    ElMessage.warning("Не удалось скопировать");
  }
}

async function deliverToServer(server: DnsServer) {
  if (server.delivery_method === "manual") {
    ElMessage.warning("Для этого сервера не настроена доставка");
    return;
  }
  try {
    ElMessage.info("Доставка...");
    const resp = await fetchDeliver(server.id);
    if (resp.result.ok) {
      ElMessage.success("Конфиг доставлен: " + resp.result.message);
    } else {
      ElMessage.error("Ошибка доставки: " + resp.result.message);
    }
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось доставить конфиг"));
  }
}

async function fetchAgentConfig(server: DnsServer) {
  try {
    ElMessage.info("Запрос конфига с сервера...");
    const resp = await fetchServerConfigFromAgent(server.id);
    configServerId.value = server.id;
    configText.value = resp.config ?? resp.error ?? "Нет данных";
    configDialogVisible.value = true;
    ElMessage.success("Конфиг получен");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось получить конфиг с сервера"));
  }
}

function confirmDelete(server: DnsServer) {
  ElMessageBox.confirm(`Удалить сервер «${server.name}»?`, "Подтверждение", {
    confirmButtonText: "Удалить",
    cancelButtonText: "Отмена",
    type: "warning",
  }).then(async () => {
    try {
      await deleteDnsServer(server.id);
      ElMessage.success("Сервер удалён");
      await loadServers();
    } catch (e) {
      ElMessage.error(messageFromAxios(e, "Не удалось удалить сервер"));
    }
  }).catch(() => {});
}

function logout() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  router.push({ name: "login" });
}

watch(() => form.delivery_method, (method) => {
  if (method === "agent" && !form.agent_url && !form.api_key) {
    form.in_cluster = false;
    form.agent_url = "http://37.230.115.233:8080";
    form.api_key = "dns-agent-a1b2c3d4e5f6g7h8";
  }
});

onMounted(async () => {
  await Promise.all([loadServers(), loadTypes()]);
});
</script>

<style scoped>
html, body, #app {
  height: 100%;
  margin: 0;
}
.layout {
  min-height: 100vh;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  padding: 0 20px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 20px;
  flex: 1;
}
.title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.03em;
}
.nav-links {
  display: flex;
  gap: 16px;
}
.nav-link {
  color: var(--el-text-color-secondary);
  text-decoration: none;
  font-size: 14px;
}
.nav-link:hover,
.nav-link.router-link-active {
  color: var(--el-color-primary);
}
.main {
  padding: 20px;
}
.card-header {
  display: flex;
  align-items: center;
}
.card-title {
  font-weight: 600;
}
.notes-cell {
  display: block;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono-text {
  white-space: pre-wrap;
  font-family: var(--el-font-family-mono, "SFMono-Regular", Consolas, monospace);
  font-size: 13px;
  line-height: 1.6;
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 4px;
  max-height: 500px;
  overflow-y: auto;
}
.json-cell {
  white-space: pre-wrap;
  font-family: var(--el-font-family-mono, "SFMono-Regular", Consolas, monospace);
  font-size: 12px;
  margin: 0;
}
.mono :deep(.el-textarea__inner) {
  font-family: var(--el-font-family-mono, "SFMono-Regular", Consolas, monospace);
  font-size: 12px;
}
</style>