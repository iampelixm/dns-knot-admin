<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="title-row">
        <div class="title">dnsadmin</div>
        <nav class="nav-links">
          <router-link class="nav-link" to="/">Зоны</router-link>
          <router-link class="nav-link" to="/knot-conf">knot.conf</router-link>
          <router-link class="nav-link" to="/ingress-wizard">Ingress</router-link>
        </nav>
        <div class="dns-status">
          <span class="muted">Knot:</span>
          <el-tag v-if="dnsHealth === null" type="info" size="small">проверка…</el-tag>
          <el-tag v-else-if="dnsHealth.ok" type="success" size="small">
            отвечает
            <span v-if="dnsHealth.latency_ms != null" class="lat"> {{ dnsHealth.latency_ms }} ms</span>
          </el-tag>
          <el-tag v-else type="danger" size="small">{{ dnsHealth.message }}</el-tag>
          <el-button link size="small" :loading="healthLoading" @click="loadHealth">Обновить</el-button>
        </div>
      </div>
      <el-button link type="danger" @click="logout">Выйти</el-button>
    </el-header>

    <el-main class="main">

      <el-row :gutter="20" class="content-row">
        <!-- Zones column -->
        <el-col :xs="24" :md="15" :lg="16">
          <el-card shadow="never" class="zones-card">
            <template #header>
              <div class="card-header">
                <span class="card-title">Зоны DNS</span>
                <el-button
                  type="primary"
                  size="small"
                  :loading="zonesLoading"
                  @click="loadZones"
                  style="margin-left: auto; margin-right: 8px"
                >Обновить</el-button>
                <el-button type="warning" size="small" @click="openCreateDialog">+ Новая зона</el-button>
              </div>
            </template>

            <el-table
              :data="zonesWithSync"
              :default-sort="{ prop: 'name', order: 'ascending' }"
              border
              size="small"
              v-loading="zonesLoading"
            >
              <el-table-column label="Зона" prop="name" sortable min-width="200">
                <template #default="{ row }">
                  <router-link
                    class="zone-link"
                    :to="{ name: 'zone-editor', params: { zone: row.name } }"
                  >{{ row.name }}</router-link>
                </template>
              </el-table-column>

              <el-table-column label="DNSSEC" width="90" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.dnssec_signing" type="success" size="small">ON</el-tag>
                  <el-tag v-else type="info" size="small" effect="plain">OFF</el-tag>
                </template>
              </el-table-column>

              <el-table-column label="Синхронизация" min-width="160">
                <template #default="{ row }">
                  <template v-if="row.servers.length">
                    <el-space wrap :size="4">
                      <el-tooltip
                        v-for="srv in row.servers"
                        :key="srv.id"
                        :content="serverTooltip(srv, row.primary_serial)"
                        placement="top"
                      >
                        <el-tag
                          :type="serverTagType(srv)"
                          size="small"
                          effect="plain"
                        >{{ srv.label }}</el-tag>
                      </el-tooltip>
                    </el-space>
                  </template>
                  <span v-else-if="syncLoading" class="muted">—</span>
                  <el-button
                    v-else
                    link
                    size="small"
                    @click="loadSync"
                  >Проверить</el-button>
                </template>
              </el-table-column>

              <el-table-column width="90" align="center">
                <template #default="{ row }">
                  <el-button
                    size="small"
                    type="primary"
                    @click="$router.push({ name: 'zone-editor', params: { zone: row.name } })"
                  >Открыть</el-button>
                </template>
              </el-table-column>
            </el-table>

            <p v-if="!zonesLoading && !zonesWithSync.length" class="muted center-text">
              Зон нет — создайте первую.
            </p>
          </el-card>
        </el-col>

        <!-- Servers column -->
        <el-col :xs="24" :md="9" :lg="8">
          <el-card shadow="never" class="servers-card">
            <template #header>
              <div class="card-header">
                <span class="card-title">Серверы Knot</span>
                <el-button
                  size="small"
                  :loading="syncLoading"
                  style="margin-left: auto"
                  @click="loadSync"
                >Обновить</el-button>
              </div>
            </template>

            <el-alert
              v-if="syncWarning"
              type="warning"
              :closable="false"
              show-icon
              :title="syncWarning"
              style="margin-bottom: 12px"
            />

            <el-table
              v-if="serverStats.length"
              :data="serverStats"
              border
              size="small"
            >
              <el-table-column label="Сервер" min-width="110">
                <template #default="{ row }">
                  <div class="server-label">
                    <el-icon class="server-status-dot" :class="row.hasError ? 'status-error' : 'status-ok'">
                      <CircleCheckFilled v-if="!row.hasError" />
                      <CircleCloseFilled v-else />
                    </el-icon>
                    {{ row.label }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="IP" prop="ip" width="120" />
              <el-table-column label="Роль" width="90" align="center">
                <template #default="{ row }">
                  <el-tag
                    :type="row.role === 'primary' ? 'warning' : 'info'"
                    size="small"
                    effect="plain"
                  >{{ row.role }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Зоны" width="80" align="center">
                <template #default="{ row }">
                  <span :class="row.synced < row.total ? 'text-warning' : 'text-ok'">
                    {{ row.synced }}/{{ row.total }}
                  </span>
                </template>
              </el-table-column>
            </el-table>

            <el-empty
              v-else-if="!syncLoading && !syncWarning"
              description="Нажмите «Обновить» для проверки"
              :image-size="60"
            />
          </el-card>
        </el-col>
      </el-row>
    </el-main>
    <AppFooter />
  </el-container>

  <CreateZoneDialog
    v-model:visible="createDialogVisible"
    :existing-zones="zoneSummaries.map(z => z.name)"
    @created="onZoneCreated"
  />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { CircleCheckFilled, CircleCloseFilled } from "@element-plus/icons-vue";
import AppFooter from "../components/AppFooter.vue";
import CreateZoneDialog from "../components/CreateZoneDialog.vue";
import {
  api,
  AUTH_TOKEN_KEY,
  type DnsHealthResponse,
  type ZoneSummary,
  type ZonesResponse,
  type ZonesSyncStatusResponse,
  type ZoneSyncEntry,
  type ZoneServerStatus,
  type KnotInstance,
} from "../api/client";
import { messageFromAxios } from "../utils/dns";

const router = useRouter();

// ---- Zones ----
const zoneSummaries = ref<ZoneSummary[]>([]);
const zonesLoading = ref(false);

async function loadZones() {
  zonesLoading.value = true;
  try {
    const { data } = await api.get<ZonesResponse>("/api/zones");
    zoneSummaries.value = data.zones;
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить список зон"));
  } finally {
    zonesLoading.value = false;
  }
}

// ---- Sync status ----
const syncLoading = ref(false);
const syncZones = ref<ZoneSyncEntry[]>([]);
const syncInstances = ref<KnotInstance[]>([]);
const syncWarning = ref("");

async function loadSync() {
  syncLoading.value = true;
  try {
    const { data } = await api.get<ZonesSyncStatusResponse>("/api/zones/sync-status");
    syncInstances.value = data.instances;
    syncZones.value = data.zones;
    syncWarning.value = data.warning ?? "";
  } catch (e) {
    syncWarning.value = messageFromAxios(e, "Не удалось получить статус серверов");
  } finally {
    syncLoading.value = false;
  }
}

// ---- DNS health ----
const dnsHealth = ref<DnsHealthResponse | null>(null);
const healthLoading = ref(false);
let healthTimer: ReturnType<typeof setInterval> | null = null;

async function loadHealth() {
  healthLoading.value = true;
  try {
    const { data } = await api.get<DnsHealthResponse>("/api/dns-health");
    dnsHealth.value = data;
  } catch {
    dnsHealth.value = { ok: false, message: "Нет ответа API", latency_ms: null };
  } finally {
    healthLoading.value = false;
  }
}

// ---- Derived data ----
const zonesWithSync = computed(() =>
  zoneSummaries.value.map((z) => {
    const entry = syncZones.value.find((s) => s.zone === z.name);
    return { ...z, servers: entry?.servers ?? [], primary_serial: entry?.primary_serial ?? null };
  }),
);

type ServerStat = KnotInstance & {
  total: number;
  synced: number;
  hasError: boolean;
};

const serverStats = computed<ServerStat[]>(() =>
  syncInstances.value.map((inst) => {
    const statuses = syncZones.value.flatMap((z) => z.servers).filter((s) => s.id === inst.id);
    const total = statuses.length;
    const synced = statuses.filter((s) => s.synced === true || s.role === "primary").length;
    const hasError = statuses.some((s) => !s.ok);
    return { ...inst, total, synced, hasError };
  }),
);

function serverTagType(srv: ZoneServerStatus): "success" | "danger" | "warning" | "info" {
  if (!srv.ok) return "danger";
  if (srv.role === "primary") return "warning";
  if (srv.synced === true) return "success";
  if (srv.synced === false) return "danger";
  return "info";
}

function serverTooltip(srv: ZoneServerStatus, primarySerial: number | null): string {
  if (!srv.ok) return `${srv.label}: нет ответа — ${srv.message ?? ""}`;
  if (srv.role === "primary") return `${srv.label}: primary (serial ${srv.serial ?? "?"})`;
  if (srv.synced === true) return `${srv.label}: синхронизирован (serial ${srv.serial ?? "?"})`;
  if (srv.synced === false)
    return `${srv.label}: отстаёт (${primarySerial} → ${srv.serial ?? "?"})`;
  return `${srv.label}: статус неизвестен`;
}

// ---- Create zone ----
const createDialogVisible = ref(false);

function openCreateDialog() {
  createDialogVisible.value = true;
}

async function onZoneCreated(name: string) {
  await router.push({ name: "zone-editor", params: { zone: name } });
}

// ---- Auth ----
function logout() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  router.push({ name: "login" });
}

// ---- Lifecycle ----
onMounted(async () => {
  try {
    await Promise.all([loadZones(), loadHealth(), loadSync()]);
    healthTimer = setInterval(loadHealth, 30000);
  } catch {
    ElMessage.error("Нет доступа к API");
    logout();
  }
});

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer);
});
</script>

<style scoped>
html,
body,
#app {
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
  color: var(--el-text-color-regular);
  text-decoration: none;
  font-size: 14px;
}

.nav-link.router-link-active {
  color: var(--el-color-primary);
  font-weight: 600;
}

.dns-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.lat {
  font-size: 11px;
  opacity: 0.8;
}

.main {
  padding: 20px;
  background: var(--el-fill-color-lighter);
}

.content-row {
  max-width: 1400px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  align-items: center;
}

.card-title {
  font-weight: 600;
}

.zones-card,
.servers-card {
  margin-bottom: 20px;
}

.zone-link {
  color: var(--el-color-primary);
  text-decoration: none;
  font-weight: 500;
}

.zone-link:hover {
  text-decoration: underline;
}

.server-label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.server-status-dot {
  font-size: 14px;
}

.status-ok {
  color: var(--el-color-success);
}

.status-error {
  color: var(--el-color-danger);
}

.text-ok {
  color: var(--el-color-success);
  font-weight: 500;
}

.text-warning {
  color: var(--el-color-danger);
  font-weight: 500;
}

.center-text {
  text-align: center;
  padding: 16px 0;
}

.mono {
  font-family: monospace;
}
</style>
