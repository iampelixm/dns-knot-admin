<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="title-row">
        <div class="title">dnsadmin</div>
        <nav class="nav-links">
          <router-link class="nav-link" to="/">Зоны</router-link>
          <router-link class="nav-link" to="/dns-servers">DNS-серверы</router-link>
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
          <span
            v-if="dnsHealth?.probe_host != null && dnsHealth.probe_host !== ''"
            class="muted probe-hint"
            :title="dnsProbeTitle(dnsHealth)"
          >
            → {{ dnsHealth.probe_host }}:{{ dnsHealth.probe_port ?? 53 }}
            <span class="probe-src">({{ dnsHealth.probe_source }})</span>
          </span>
          <el-button link size="small" :loading="dnsHealthLoading" @click="fetchDnsHealth">Обновить</el-button>
        </div>
      </div>
      <el-button link type="danger" @click="logout">Выйти</el-button>
    </el-header>
    <el-main>
      <el-row :gutter="16" align="middle" style="margin-bottom: 12px">
        <el-col :span="1" style="min-width: 80px">
          <el-button link @click="$router.push({ name: 'home' })" style="font-size: 13px">← Зоны</el-button>
        </el-col>
        <el-col :span="7">
          <el-select
            v-model="selectedZone"
            placeholder="Зона"
            filterable
            style="width: 100%"
            @change="onZoneChange"
          >
            <el-option v-for="z in zones" :key="z" :label="z" :value="z" />
          </el-select>
          <span v-if="followerOf" class="follower-badge">
            <el-tag size="small" type="warning" effect="plain">
              → {{ followerOf }}
            </el-tag>
          </span>
        </el-col>
        <el-col :span="16" class="toolbar">
          <el-button type="primary" @click="loadZone" :loading="loading">Загрузить</el-button>
          <el-button type="warning" @click="openCreateDialog">Новая зона</el-button>
          <el-button
            v-if="followerOf"
            type="info"
            @click="unfollowCurrentZone"
            :loading="unfollowLoading"
          >Отвязать от master</el-button>
          <el-button
            type="danger"
            @click="openDeleteConfirm"
            :disabled="!selectedZone"
          >Удалить зону</el-button>
          <el-button @click="exportZones" :loading="exportLoading">Экспорт</el-button>
          <el-upload
            :show-file-list="false"
            :http-request="importZones"
            accept=".tgz,.tar.gz,.gz"
          >
            <el-button :loading="importLoading">Импорт</el-button>
          </el-upload>
        </el-col>
      </el-row>

      <div class="zone-editor">
        <div class="tab-toolbar">
          <el-alert
            v-if="formParseError && activeTab !== 'text' && activeTab !== 'dnssec'"
            type="warning"
            :closable="false"
            :title="formParseError"
            show-icon
            class="tab-toolbar-alert"
          />
          <div v-if="activeTab === 'text'" class="form-actions">
            <el-button @click="validateText" :loading="validateLoading">Проверить синтаксис</el-button>
            <el-button type="success" @click="saveZone" :loading="saving">Сохранить и перезагрузить Knot</el-button>
          </div>
          <div v-else-if="activeTab !== 'dnssec'" class="form-actions">
            <el-button @click="syncFormFromText" :loading="formLoading">Загрузить поля из текста</el-button>
            <el-button @click="applyFormToText" :loading="renderLoading">Перенести в текст (без сохранения)</el-button>
            <el-button type="success" @click="saveFromForm" :loading="formSaving">Сохранить из формы</el-button>
          </div>
        </div>

        <el-tabs v-model="activeTab" type="border-card" class="zone-editor-tabs">
          <el-tab-pane label="Записи" name="records">
          <el-card shadow="never">
            <template #header>A / AAAA / MX / TXT / CNAME …</template>
            <div class="records-wrapper">
              <table class="dns-records-table">
                <colgroup>
                  <col class="col-name" />
                  <col class="col-ttl" />
                  <col class="col-type" />
                  <col class="col-value" />
                  <col class="col-action" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Имя</th>
                    <th>TTL</th>
                    <th>Тип</th>
                    <th>Значение</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody v-for="group in displayGroups" :key="group.key">
                  <tr class="group-header-row" @click="toggleGroup(group.key)">
                    <td colspan="5">
                      <div class="group-header-inner">
                        <el-icon class="collapse-icon">
                          <ArrowDown v-if="!collapsedGroups.has(group.key)" />
                          <ArrowRight v-else />
                        </el-icon>
                        <span class="group-name">{{ group.key === "@" ? "@ — корень зоны" : group.key }}</span>
                        <el-tag size="small" round type="info">{{ group.records.length }}</el-tag>
                        <el-button size="small" class="group-add-btn" @click.stop="addRecInGroup(group.key)">
                          + Добавить
                        </el-button>
                      </div>
                    </td>
                  </tr>
                  <template v-if="!collapsedGroups.has(group.key)">
                    <tr v-for="rec in group.records" :key="formModel.records.indexOf(rec)" class="record-row">
                      <td>
                        <el-input v-model="rec.name" placeholder="@" :disabled="rec.source === 'master'" />
                        <el-tag v-if="rec.source === 'master'" size="small" type="warning" effect="plain" class="source-tag">из master</el-tag>
                      </td>
                      <td>
                        <el-input-number
                          v-model="rec.ttl"
                          :min="1"
                          :step="60"
                          controls-position="right"
                          class="ttl-input"
                          :disabled="rec.source === 'master'"
                        />
                      </td>
                      <td>
                        <el-select v-model="rec.rtype" filterable :disabled="rec.source === 'master'">
                          <el-option v-for="t in recordTypes" :key="t" :label="t" :value="t" />
                        </el-select>
                      </td>
                      <td>
                        <el-input
                          v-model="rec.value"
                          type="textarea"
                          :autosize="{ minRows: 1 }"
                          placeholder="1.2.3.4 или 10 mail.example.com"
                          :disabled="rec.source === 'master'"
                        />
                      </td>
                      <td class="action-cell">
                        <el-button v-if="rec.source !== 'master'" link type="danger" @click="removeRec(rec)">Удалить</el-button>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
              <el-button class="mt8" size="small" @click="addRec">Добавить запись (@)</el-button>
            </div>
          </el-card>
          </el-tab-pane>

          <el-tab-pane label="SOA" name="soa">
          <el-card shadow="never">
            <template #header>SOA</template>
            <el-form :model="formModel.soa" label-width="160px" class="soa-form">
              <el-form-item label="$TTL / SOA TTL">
                <el-input-number v-model="formModel.soa.ttl" :min="1" :step="60" controls-position="right" />
              </el-form-item>
              <el-form-item label="Primary NS (mname)">
                <el-input v-model="formModel.soa.primary_ns" placeholder="ns1.example.com" clearable />
              </el-form-item>
              <el-form-item label="Почта админа">
                <el-input v-model="formModel.soa.admin_email" placeholder="hostmaster@example.com" clearable />
              </el-form-item>
              <el-form-item label="Serial">
                <el-input-number v-model="formModel.soa.serial" :min="0" :step="1" controls-position="right" />
              </el-form-item>
              <el-form-item label="Refresh">
                <el-input-number v-model="formModel.soa.refresh" :min="1" :step="60" controls-position="right" />
              </el-form-item>
              <el-form-item label="Retry">
                <el-input-number v-model="formModel.soa.retry" :min="1" :step="60" controls-position="right" />
              </el-form-item>
              <el-form-item label="Expire">
                <el-input-number v-model="formModel.soa.expire" :min="1" :step="3600" controls-position="right" />
              </el-form-item>
              <el-form-item label="Minimum">
                <el-input-number v-model="formModel.soa.minimum" :min="1" :step="60" controls-position="right" />
              </el-form-item>
            </el-form>
          </el-card>
          </el-tab-pane>

          <el-tab-pane label="NS" name="ns">
          <el-card shadow="never">
            <template #header>Серверы имён зоны</template>
            <el-table :data="formModel.ns" border size="small">
              <el-table-column label="Сервер имён">
                <template #default="{ row }">
                  <el-input v-model="row.host" placeholder="ns1.example.com" clearable />
                </template>
              </el-table-column>
              <el-table-column width="90" align="center">
                <template #default="{ $index }">
                  <el-button link type="danger" @click="removeNs($index)">Удалить</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button class="mt8" size="small" @click="addNs">Добавить NS</el-button>
          </el-card>
          </el-tab-pane>

          <el-tab-pane label="DNSSEC" name="dnssec">
            <el-card v-if="selectedZone" class="dnssec-card" shadow="never">
              <template #header>DNSSEC (Knot)</template>
              <el-space direction="vertical" alignment="stretch" style="width: 100%" :size="10">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="После включения подписи опубликуйте DS-записи у регистратора родительской зоны."
                />
                <div class="dnssec-row">
                  <span class="dnssec-label">Подписывать зону</span>
                  <el-switch v-model="dnssecLocal" />
                  <el-button
                    type="primary"
                    :loading="dnssecSaving"
                    :disabled="!dnssecDirty"
                    @click="applyDnssec"
                  >
                    Применить в knot.conf
                  </el-button>
                </div>
                <div>
                  <el-button :loading="dsLoading" @click="openDsDialog">Показать DS и DNSKEY</el-button>
                </div>
                <div v-if="!dnssecServer">
                  <el-button type="success" :loading="dnssecInitLoading" @click="initDnssec">
                    Включить DNSSEC (с генерацией ключей)
                  </el-button>
                </div>
              </el-space>
            </el-card>
            <el-alert v-else type="info" :closable="false" show-icon title="Выберите зону в списке выше." />
          </el-tab-pane>

          <el-tab-pane label="Серверы" name="servers">
            <el-card shadow="never" class="roles-card">
              <template #header>
                <div style="display:flex;align-items:center;justify-content:space-between">
                  <span>Роли для зоны</span>
                  <span>
                    <el-button size="small" :loading="rolesLoading" @click="loadZoneRoles">Обновить</el-button>
                    <el-button
                      size="small"
                      type="primary"
                      :loading="rolesSaving"
                      :disabled="!rolesDirty || !selectedZone"
                      @click="saveZoneRoles"
                    >Сохранить роли</el-button>
                  </span>
                </div>
              </template>
              <el-alert
                v-if="!selectedZone"
                type="info"
                :closable="false"
                show-icon
                title="Выберите зону в списке выше."
                style="margin-bottom:12px"
              />
              <template v-else>
                <el-table :data="sortedRoleServers" border size="small" :empty-text="roleEmptyText">
                  <el-table-column label="Сервер" width="200">
                    <template #default="{ row }">
                      <div>{{ row.name }}</div>
                      <div class="muted small-p" style="margin:0">{{ row.id }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="Адрес" width="170">
                    <template #default="{ row }">
                      <span class="mono-small">{{ row.address }}:{{ row.port }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="Тип / доставка" width="170">
                    <template #default="{ row }">
                      <el-tag size="small" effect="plain">{{ row.server_type }}</el-tag>
                      <span class="muted" style="margin-left:6px">{{ row.delivery_method }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="Роль" min-width="220">
                    <template #default="{ row }">
                      <el-radio-group
                        v-model="row.zone_role"
                        @change="onRoleChange"
                      >
                        <el-radio-button label="primary">primary</el-radio-button>
                        <el-radio-button label="secondary">secondary</el-radio-button>
                        <el-radio-button label="none">нет</el-radio-button>
                      </el-radio-group>
                    </template>
                  </el-table-column>
                  <el-table-column label="Источник" width="120">
                    <template #default="{ row }">
                      <el-tag v-if="row.origin === 'db'" size="small" type="success" effect="plain">назначен</el-tag>
                      <el-tag v-else size="small" type="info" effect="plain">fallback</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
                <el-alert
                  v-if="rolesHint"
                  type="info"
                  :closable="false"
                  show-icon
                  class="roles-hint"
                  :title="rolesHint"
                />
                <p v-if="roleSourceNote" class="muted small-p" style="margin:8px 0 0">
                  {{ roleSourceNote }}
                </p>
              </template>
            </el-card>

            <el-card shadow="never" style="margin-top:16px">
              <template #header>
                <div style="display:flex;align-items:center;justify-content:space-between">
                  <span>Состояние зоны на серверах</span>
                  <span>
                    <el-button
                      size="small"
                      type="warning"
                      :loading="syncForceLoading"
                      :disabled="!selectedZone"
                      @click="forceSyncZone"
                    >Форсировать синхронизацию</el-button>
                    <el-button size="small" :loading="syncLoading" @click="fetchSyncStatus">Обновить</el-button>
                  </span>
                </div>
              </template>
              <el-alert
                v-if="syncWarning"
                type="warning"
                :closable="false"
                show-icon
                :title="syncWarning"
                style="margin-bottom:12px"
              />
              <el-alert
                v-if="!syncWarning && !selectedZone"
                type="info"
                :closable="false"
                show-icon
                title="Выберите зону в списке выше."
              />
              <template v-if="currentZoneSync">
                <el-table :data="currentZoneSync.servers" border size="small">
                  <el-table-column label="Сервер" prop="label" width="160" />
                  <el-table-column label="IP" prop="ip" width="140" />
                  <el-table-column label="Роль" width="110">
                    <template #default="{ row }">
                      <el-tag
                        :type="row.role === 'primary' ? 'warning' : 'info'"
                        size="small"
                        effect="plain"
                      >{{ row.role }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="SOA Serial" width="140">
                    <template #default="{ row }">
                      <span v-if="row.serial !== null" class="mono-small">{{ row.serial }}</span>
                      <span v-else class="muted">—</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="Синхронизация">
                    <template #default="{ row }">
                      <el-tag v-if="!row.ok" type="danger" size="small">
                        {{ row.message || 'нет ответа' }}
                      </el-tag>
                      <el-tag v-else-if="row.role === 'primary'" type="warning" size="small">primary</el-tag>
                      <el-tag v-else-if="row.synced === true" type="success" size="small">в синхронизации</el-tag>
                      <el-tag v-else-if="row.synced === false" type="danger" size="small">
                        отстаёт ({{ currentZoneSync.primary_serial }} → {{ row.serial }})
                      </el-tag>
                      <el-tag v-else type="info" size="small">неизвестно</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
                <p v-if="syncUpdatedAt" class="muted small-p" style="margin-top:8px">
                  Обновлено: {{ syncUpdatedAt }}
                </p>
              </template>
              <el-empty
                v-else-if="!syncWarning && !syncLoading && selectedZone"
                description="Нажмите «Обновить» для проверки"
                :image-size="60"
              />
            </el-card>
          </el-tab-pane>

          <el-tab-pane label="Текст зоны" name="text">
            <el-space direction="vertical" alignment="stretch" style="width: 100%" :size="8">
              <el-alert v-if="followerOf" type="warning" :closable="false" show-icon>
                Текст зоны генерируется автоматически из записей мастер-зоны и локальных записей. Редактирование недоступно.
              </el-alert>
              <el-alert v-if="validateMessage" :type="validateOk ? 'success' : 'error'" :closable="false" show-icon>
                {{ validateMessage }}
              </el-alert>
              <ZoneTextEditor v-if="!followerOf" v-model="content" />
              <el-input v-else v-model="content" type="textarea" :rows="24" readonly class="mono" />
            </el-space>
          </el-tab-pane>
        </el-tabs>
      </div>

      <CreateZoneDialog
        v-model:visible="createDialogVisible"
        :existing-zones="zones"
        :zone-summaries="zoneSummaries"
        @created="onZoneCreated"
      />

      <el-dialog
        v-model="dsDialogVisible"
        title="DNSSEC: DS и DNSKEY"
        width="720px"
        @closed="resetDsDialog"
      >
        <el-alert v-if="dsDialogHint" type="info" :closable="false" show-icon :title="dsDialogHint" class="mb12" />
        <el-alert
          v-if="dsDialogRuFamily"
          type="warning"
          :closable="false"
          show-icon
          class="mb12"
          title="Зона .RU / .РФ / .SU: по правилам многих регистраторов передайте и DNSKEY, и DS (оба блока ниже). Имя в записях — с точкой в конце (example.ru.)."
        />
        <el-alert
          v-else
          type="success"
          :closable="false"
          show-icon
          class="mb12"
          title="Для большинства международных доменов достаточно DS (SHA-256). DNSKEY уже публикуются на ваших NS. Строки ниже — полные RR с точкой в конце имени."
        />
        <el-tabs v-model="dsDialogTab" class="ds-tabs">
          <el-tab-pane label="DS для регистратора" name="ds">
            <p class="muted small-p">Вставьте в панели родительской зоны / регистратора.</p>
            <el-input v-model="dsDialogText" type="textarea" :rows="6" readonly class="mono" />
          </el-tab-pane>
          <el-tab-pane label="DNSKEY (с сервера)" name="dnskey">
            <p class="muted small-p">
              Тот же ответ DNSKEY, из которого считается DS. Для публикации у родителя не нужен — только для
              проверки, бэкапа или документации.
            </p>
            <el-input v-model="dsDialogDnskeyText" type="textarea" :rows="10" readonly class="mono" />
          </el-tab-pane>
        </el-tabs>
        <template #footer>
          <el-button @click="dsDialogVisible = false">Закрыть</el-button>
          <el-button :disabled="!dsDialogText" @click="copyDsToClipboard">Копировать DS</el-button>
          <el-button type="primary" :disabled="!dsDialogDnskeyText" @click="copyDnskeyToClipboard">
            Копировать DNSKEY
          </el-button>
        </template>
      </el-dialog>
    </el-main>
    <AppFooter />
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, toRaw, watch } from "vue";
import { useRouter, useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { ArrowDown, ArrowRight } from "@element-plus/icons-vue";
import AppFooter from "../components/AppFooter.vue";
import ZoneTextEditor from "../components/ZoneTextEditor.vue";
import CreateZoneDialog from "../components/CreateZoneDialog.vue";
import {
  api,
  AUTH_TOKEN_KEY,
  emptyZoneForm,
  unfollowZone,
  fetchFollowerInfo,
  fetchDnsServers,
  replaceZoneServers,
  type DnsHealthResponse,
  type DnsServer,
  type DnssecDsResponse,
  type ZoneFormModel,
  type ZoneFormWithSource,
  type ZoneSummary,
  type ZoneServerReplaceItem,
  type ZoneSyncEntry,
  type ZonesSyncStatusResponse,
  type ZonesResponse,
  type ZoneContentResponse,
  type ValidateResponse,
  type RecordRow,
  type RecordRowWithSource,
} from "../api/client";
const router = useRouter();
const route = useRoute();

const zoneSummaries = ref<ZoneSummary[]>([]);
const zones = computed(() => zoneSummaries.value.map((z) => z.name));
const selectedZone = ref((route.params.zone as string) || "");
const content = ref("");
const loading = ref(false);
const saving = ref(false);
const createDialogVisible = ref(false);

const activeTab = ref<"records" | "soa" | "ns" | "dnssec" | "servers" | "text">("records");
const formModel = reactive<ZoneFormModel>(emptyZoneForm());
const formLoading = ref(false);
const formSaving = ref(false);
const renderLoading = ref(false);
const formParseError = ref("");

const validateLoading = ref(false);
const validateMessage = ref("");
const validateOk = ref(false);

const dnsHealth = ref<DnsHealthResponse | null>(null);
const dnsHealthLoading = ref(false);
let dnsTimer: ReturnType<typeof setInterval> | null = null;

const dnssecLocal = ref(false);
const dnssecServer = ref(false);
const dnssecSaving = ref(false);
const dnssecInitLoading = ref(false);
const exportLoading = ref(false);
const importLoading = ref(false);
const dnssecDirty = computed(() => dnssecLocal.value !== dnssecServer.value);

const syncLoading = ref(false);
const syncForceLoading = ref(false);
const syncZones = ref<ZoneSyncEntry[]>([]);
const syncWarning = ref("");
const syncUpdatedAt = ref("");
const currentZoneSync = computed<ZoneSyncEntry | null>(
  () => syncZones.value.find((z) => z.zone === selectedZone.value) ?? null,
);

type RoleServerRow = DnsServer & {
  zone_role: "primary" | "secondary" | "none";
  origin: "db" | "fallback";
};

const rolesLoading = ref(false);
const rolesSaving = ref(false);
const roleServers = ref<RoleServerRow[]>([]);
const roleSourceNote = ref("");
const rolesHint = ref("");
const rolesDirty = ref(false);
const roleEmptyText = ref("");
const sortedRoleServers = computed<RoleServerRow[]>(() => {
  const rank: Record<RoleServerRow["zone_role"], number> = { primary: 0, secondary: 1, none: 2 };
  return [...roleServers.value].sort(
    (a, b) => rank[a.zone_role] - rank[b.zone_role],
  );
});

function applyRolesToServers(servers: DnsServer[], assigned: Map<string, string>) {
  roleServers.value = servers.map((s) => ({
    ...s,
    zone_role: (assigned.get(s.id) as "primary" | "secondary") ?? "none",
    origin: assigned.has(s.id) ? "db" : "fallback",
  }));
  // если роли для зоны ещё не заданы — по умолчанию первая строка = primary
  if (!roleServers.value.some((s) => s.zone_role !== "none")) {
    const first = roleServers.value[0];
    if (first) first.zone_role = "primary";
  }
}

async function loadZoneRoles() {
  if (!selectedZone.value) return;
  rolesLoading.value = true;
  rolesHint.value = "";
  roleEmptyText.value = "";
  roleSourceNote.value = "Загрузка…";
  try {
    const [ds, zs] = await Promise.all([
      fetchDnsServers(),
      api.get<{ servers: Array<{ server_id: string; role: string }> }>(
        `/api/zones/${encodeURIComponent(selectedZone.value)}/servers`,
      ),
    ]);
    if (!ds.servers.length) {
      roleEmptyText.value = "Нет DNS-серверов. Добавьте сервер на странице «DNS-серверы».";
      roleServers.value = [];
      roleSourceNote.value = "";
      return;
    }
    const assigned = new Map<string, string>();
    for (const s of zs.data.servers) assigned.set(s.server_id, s.role);
    applyRolesToServers(ds.servers, assigned);
    const hasDbRoles = assigned.size > 0;
    const roles = roleServers.value.filter((s) => s.zone_role !== "none");
    if (!hasDbRoles) {
      const p = roleServers.value.find((s) => s.zone_role === "primary");
      rolesHint.value = "Для зоны не назначены роли — используется глобальная топология (fallback). По умолчанию primary — первый сервер в списке. Сохраните, чтобы закрепить роли.";
      roleSourceNote.value = p ? `Primary: ${p.name} (${p.id}).` : "";
    } else {
      const p = roles.find((s) => s.zone_role === "primary");
      roleSourceNote.value = p
        ? `Primary: ${p.name} (${p.id}). In-cluster серверы без роли «нет» будут убраны из knot.conf этой зоны.`
        : "Роли заданы, но primary не выбран — Knot может работать некорректно. Укажите primary.";
    }
    rolesDirty.value = false;
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить роли серверов"));
  } finally {
    rolesLoading.value = false;
  }
}

function onRoleChange() {
  rolesDirty.value = true;
}

function zoneRolePayload(): ZoneServerReplaceItem[] {
  return roleServers.value
    .filter((s) => s.zone_role === "primary" || s.zone_role === "secondary")
    .map((s) => ({ server_id: s.id, role: s.zone_role as "primary" | "secondary" }));
}

async function saveZoneRoles() {
  if (!selectedZone.value) return;
  const chosen = zoneRolePayload();
  if (chosen.filter((s) => s.role === "primary").length > 1) {
    ElMessage.error("May be only one primary. Не более одного primary.");
    return;
  }
  rolesSaving.value = true;
  try {
    const resp = await replaceZoneServers(selectedZone.value, chosen);
    rolesDirty.value = false;
    ElMessage.success(
      chosen.length
        ? `Роли сохранены (${chosen.length} серверов)`
        : "Роли сброшены — зона вернётся к глобальной топологии",
    );
    if (resp.sync_error) {
      ElMessage.warning(`Серверы сохранены, но ресинк knot.conf не удался: ${resp.sync_error}`);
    }
    await loadZoneRoles();
    fetchSyncStatus();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось сохранить роли"));
  } finally {
    rolesSaving.value = false;
  }
}

const dsLoading = ref(false);
const dsDialogVisible = ref(false);
const dsDialogText = ref("");
const dsDialogDnskeyText = ref("");
const dsDialogTab = ref<"ds" | "dnskey">("ds");
const dsDialogRuFamily = ref(false);
const dsDialogHint = ref("");

const followerOf = ref<string | null>(null);
const unfollowLoading = ref(false);

async function fetchSyncStatus() {
  syncLoading.value = true;
  try {
    const { data } = await api.get<ZonesSyncStatusResponse>("/api/zones/sync-status");
    syncZones.value = data.zones;
    syncWarning.value = data.warning ?? "";
    syncUpdatedAt.value = new Date().toLocaleTimeString();
  } catch (e) {
    syncWarning.value = messageFromAxios(e, "Не удалось получить статус синхронизации");
  } finally {
    syncLoading.value = false;
  }
}

async function forceSyncZone() {
  if (!selectedZone.value) return;
  syncForceLoading.value = true;
  try {
    const { data } = await api.post(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/sync`,
    );
    ElMessage.success(`Синхронизировано: ${(data.synced_secondaries || []).join(", ") || "нет secondary"}`);
    fetchSyncStatus();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось синхронизировать зону"));
  } finally {
    syncForceLoading.value = false;
  }
}

function syncDnssecFromServer() {
  const s = zoneSummaries.value.find((z) => z.name === selectedZone.value);
  const v = s?.dnssec_signing ?? false;
  dnssecServer.value = v;
  dnssecLocal.value = v;
}

async function applyDnssec() {
  if (!selectedZone.value) return;
  dnssecSaving.value = true;
  try {
    await api.patch(`/api/zones/${encodeURIComponent(selectedZone.value)}/dnssec`, {
      signing: dnssecLocal.value,
    });
    ElMessage.success("knot.conf обновлён, Knot перезапускается");
    await loadZones();
    syncDnssecFromServer();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось обновить DNSSEC"));
  } finally {
    dnssecSaving.value = false;
  }
}

async function initDnssec() {
  if (!selectedZone.value) return;
  dnssecInitLoading.value = true;
  try {
    const resp = await api.post(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/dnssec/init`,
    );
    ElMessage.success("DNSSEC включён, Knot перезапущен, ключи сгенерированы");
    await loadZones();
    syncDnssecFromServer();
    if (resp.data?.ds) {
      dsDialogText.value = resp.data.ds.join("\n") + "\n\n" + (resp.data.dnskey ?? []).join("\n");
      dsDialogVisible.value = true;
    }
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось включить DNSSEC"));
  } finally {
    dnssecInitLoading.value = false;
  }
}

function resetDsDialog() {
  dsDialogText.value = "";
  dsDialogDnskeyText.value = "";
  dsDialogTab.value = "ds";
  dsDialogRuFamily.value = false;
}

async function openDsDialog() {
  if (!selectedZone.value) return;
  dsLoading.value = true;
  dsDialogHint.value = "";
  try {
    const { data } = await api.get<DnssecDsResponse>(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/dnssec-ds`,
    );
    dsDialogText.value = data.ds.join("\n");
    dsDialogDnskeyText.value = (data.dnskey || []).join("\n");
    dsDialogHint.value = data.message || "";
    dsDialogRuFamily.value = Boolean(data.registrar_ru_family);
    dsDialogVisible.value = true;
  } catch (e) {
    ElMessage.warning(messageFromAxios(e, "DS недоступны (подпись выключена или DNSKEY ещё не в эфире)"));
  } finally {
    dsLoading.value = false;
  }
}

async function copyDsToClipboard() {
  try {
    await navigator.clipboard.writeText(dsDialogText.value);
    ElMessage.success("DS скопированы");
  } catch {
    ElMessage.error("Не удалось скопировать");
  }
}

async function copyDnskeyToClipboard() {
  try {
    await navigator.clipboard.writeText(dsDialogDnskeyText.value);
    ElMessage.success("DNSKEY скопированы");
  } catch {
    ElMessage.error("Не удалось скопировать");
  }
}

const recordTypes = ["A", "AAAA", "MX", "TXT", "CNAME", "PTR", "SRV", "NS"];

// Reversed DNS labels key for hierarchical sort: "a.n" → "n\0a", "n" → "n"
function dnsNameSortKey(name: string): string {
  if (!name || name === "@") return "\x00";
  return name.split(".").reverse().join("\x00");
}

// Group key = last label of the name (or "@")
function dnsGroupKey(name: string): string {
  if (!name || name === "@") return "@";
  const parts = name.split(".");
  return parts[parts.length - 1]!;
}

type DisplayGroup = { key: string; records: RecordRow[] };
const displayGroups = ref<DisplayGroup[]>([]);
const collapsedGroups = ref(new Set<string>());

// Вызывается явно: при загрузке зоны, добавлении и удалении записи.
// НЕ является computed — не реагирует на изменения полей записей,
// поэтому список не прыгает при вводе.
function rebuildDisplay() {
  const sorted = [...formModel.records].sort((a, b) => {
    const ka = dnsNameSortKey(a.name || "@");
    const kb = dnsNameSortKey(b.name || "@");
    if (ka !== kb) return ka < kb ? -1 : 1;
    return (a.rtype || "").localeCompare(b.rtype || "");
  });
  const groups = new Map<string, RecordRow[]>();
  for (const rec of sorted) {
    const key = dnsGroupKey(rec.name || "@");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(rec);
  }
  displayGroups.value = [...groups.entries()].map(([key, records]) => ({ key, records }));
}

function toggleGroup(key: string) {
  const s = new Set(collapsedGroups.value);
  s.has(key) ? s.delete(key) : s.add(key);
  collapsedGroups.value = s;
}

watch(selectedZone, () => {
  collapsedGroups.value = new Set();
});

function messageFromAxios(err: unknown, fallback: string): string {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d))
    return d
      .map((x) => {
        if (typeof x === "string") return x;
        if (typeof x === "object" && x && "msg" in x) return String((x as { msg: string }).msg);
        return JSON.stringify(x);
      })
      .join("; ");
  return fallback;
}



function openCreateDialog() {
  createDialogVisible.value = true;
}

async function exportZones() {
  exportLoading.value = true;
  try {
    const resp = await api.get("/api/zones/export", { responseType: "blob" });
    const url = URL.createObjectURL(new Blob([resp.data], { type: "application/gzip" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "zones-export.tgz";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось выгрузить бэкап"));
  } finally {
    exportLoading.value = false;
  }
}

async function importZones(options: { file: File }) {
  importLoading.value = true;
  try {
    const form = new FormData();
    form.append("file", options.file);
    const { data } = await api.post("/api/zones/import", form);
    ElMessage.success(`Импортировано ${data.zones_imported} зон, Knot перезапущен`);
    await loadZones();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось импортировать зоны"));
  } finally {
    importLoading.value = false;
  }
}

async function onZoneCreated(name: string) {
  await router.push({ name: "zone-editor", params: { zone: name } });
}

function logout() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  router.push({ name: "login" });
}

function resetFormShell() {
  const f = emptyZoneForm();
  formModel.soa = f.soa;
  formModel.ns.splice(0, formModel.ns.length, ...f.ns);
  formModel.records.splice(0, formModel.records.length, ...f.records);
  rebuildDisplay();
}

function assignForm(data: ZoneFormWithSource) {
  formModel.soa = { ...data.soa };
  formModel.ns.splice(0, formModel.ns.length, ...(data.ns.length ? data.ns : [{ host: "" }]));
  formModel.records.splice(
    0,
    formModel.records.length,
    ...data.records.map((r) => ({
      name: r.name,
      ttl: r.ttl === null || r.ttl === undefined ? undefined : r.ttl,
      rtype: r.rtype,
      value: r.value,
      source: r.source || "local",
    })),
  );
  rebuildDisplay();
}

const DNS_PROBE_SOURCE_HINT: Record<string, string> = {
  KNOT_DNS_PROBE_HOST: "переменная KNOT_DNS_PROBE_HOST",
  "knot.conf.listen": "server.listen в knot.conf (ConfigMap)",
  knot_pod_ip: "IP pod Knot",
  KNOT_DNS_HOST: "имя KNOT_DNS_HOST",
};

function dnsProbeTitle(h: DnsHealthResponse): string {
  const src = h.probe_source ?? "";
  const from = DNS_PROBE_SOURCE_HINT[src] ?? src;
  return `UDP SOA на ${h.probe_host}:${h.probe_port ?? 53}. Откуда адрес: ${from}.`;
}

async function fetchDnsHealth() {
  dnsHealthLoading.value = true;
  try {
    const { data } = await api.get<DnsHealthResponse>("/api/dns-health");
    dnsHealth.value = data;
  } catch {
    dnsHealth.value = { ok: false, message: "Нет ответа API", latency_ms: null };
  } finally {
    dnsHealthLoading.value = false;
  }
}

async function loadZones() {
  const { data } = await api.get<ZonesResponse>("/api/zones");
  zoneSummaries.value = data.zones;
  syncDnssecFromServer();
}

async function loadZone() {
  if (!selectedZone.value) return;
  loading.value = true;
  formParseError.value = "";
  followerOf.value = null;
  try {
    const { data } = await api.get<ZoneContentResponse>(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/content`,
    );
    content.value = data.content;
    await syncFormFromText(false);

    const fi = await fetchFollowerInfo(selectedZone.value);
    followerOf.value = fi.follower_of;

    ElMessage.success("Зона загружена");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить зону"));
  } finally {
    loading.value = false;
  }
}

async function onZoneChange() {
  await router.replace({ name: "zone-editor", params: { zone: selectedZone.value } });
  syncDnssecFromServer();
  await loadZone();
  if (activeTab.value === "servers") loadZoneRoles();
}

async function syncFormFromText(showToast = true) {
  if (!selectedZone.value) return;
  formLoading.value = true;
  formParseError.value = "";
  try {
    const { data } = await api.post<{ form: ZoneFormWithSource }>(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/parse-form`,
      { content: content.value },
    );
    assignForm(data.form);
    if (showToast) ElMessage.success("Поля обновлены из текста");
  } catch (e) {
    formParseError.value = messageFromAxios(e, "Не удалось разобрать zone-файл на поля");
    resetFormShell();
    if (showToast) ElMessage.warning(formParseError.value);
  } finally {
    formLoading.value = false;
  }
}

async function applyFormToText() {
  if (!selectedZone.value) return;
  renderLoading.value = true;
  try {
    const { data } = await api.post<{ content: string }>(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/render-form`,
      toRaw(formModel),
    );
    content.value = data.content;
    validateMessage.value = "";
    ElMessage.success("Текст обновлён из формы (ещё не сохранено в кластере)");
    activeTab.value = "text";
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось собрать zone-файл"));
  } finally {
    renderLoading.value = false;
  }
}

async function saveFromForm() {
  if (!selectedZone.value) return;
  formSaving.value = true;
  try {
    await api.put(`/api/zones/${encodeURIComponent(selectedZone.value)}/form`, toRaw(formModel));
    ElMessage.success("Сохранено из формы, Knot перезапускается");
    await loadZone();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Ошибка сохранения"));
  } finally {
    formSaving.value = false;
  }
}

async function validateText() {
  if (!selectedZone.value) return;
  validateLoading.value = true;
  validateMessage.value = "";
  try {
    const { data } = await api.post<ValidateResponse>(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/validate`,
      { content: content.value },
    );
    validateOk.value = data.valid;
    validateMessage.value = data.valid
      ? "Синтаксис zone-файла допустим (dnspython)."
      : data.errors.join("; ");
    if (data.valid) ElMessage.success("Синтаксис в порядке");
    else ElMessage.error("Ошибки в zone-файле");
  } catch (e) {
    validateOk.value = false;
    validateMessage.value = messageFromAxios(e, "Проверка не удалась");
    ElMessage.error(validateMessage.value);
  } finally {
    validateLoading.value = false;
  }
}

async function saveZone() {
  if (!selectedZone.value) return;
  saving.value = true;
  try {
    await api.put(`/api/zones/${encodeURIComponent(selectedZone.value)}`, {
      content: content.value,
    });
    ElMessage.success("Сохранено, Knot перезапускается");
    validateMessage.value = "";
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Ошибка сохранения"));
  } finally {
    saving.value = false;
  }
}

async function openDeleteConfirm() {
  if (!selectedZone.value) return;
  try {
    await ElMessageBox.confirm(
      `Вы уверены, что хотите удалить зону «${selectedZone.value}»? Зона будет удалена из ConfigMap и БД.`,
      "Удаление зоны",
      { confirmButtonText: "Удалить", cancelButtonText: "Отмена", type: "warning" },
    );
    await api.delete(`/api/zones/${encodeURIComponent(selectedZone.value)}`);
    ElMessage.success(`Зона «${selectedZone.value}» удалена`);
    await router.push({ name: "home" });
  } catch {
    // cancelled or error
  }
}

function addNs() {
  formModel.ns.push({ host: "" });
}

function removeNs(i: number) {
  formModel.ns.splice(i, 1);
  if (!formModel.ns.length) formModel.ns.push({ host: "" });
}

function addRec() {
  formModel.records.push({ name: "@", rtype: "A", value: "", source: "local" });
  rebuildDisplay();
}

function addRecInGroup(groupKey: string) {
  const name = groupKey === "@" ? "@" : groupKey;
  formModel.records.push({ name, rtype: "A", value: "", source: "local" });
  const s = new Set(collapsedGroups.value);
  s.delete(groupKey);
  collapsedGroups.value = s;
  rebuildDisplay();
}

function removeRec(rec: RecordRowWithSource) {
  const idx = formModel.records.indexOf(rec);
  if (idx >= 0) formModel.records.splice(idx, 1);
  rebuildDisplay();
}

async function unfollowCurrentZone() {
  if (!selectedZone.value || !followerOf.value) return;
  unfollowLoading.value = true;
  try {
    await unfollowZone(selectedZone.value);
    ElMessage.success(`Зона отвязана от ${followerOf.value}`);
    followerOf.value = null;
    await loadZone();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось отвязать зону"));
  } finally {
    unfollowLoading.value = false;
  }
}

watch(activeTab, (tab) => {
  if (tab === "servers" && !syncZones.value.length && !syncWarning.value) {
    fetchSyncStatus();
  }
  if (tab === "servers") {
    loadZoneRoles();
  }
});

watch(selectedZone, () => {
  collapsedGroups.value = new Set();
  rolesDirty.value = false;
  rolesHint.value = "";
  roleSourceNote.value = "";
});

onMounted(async () => {
  try {
    await loadZones();
    await loadZone();
    await fetchDnsHealth();
    dnsTimer = setInterval(fetchDnsHealth, 30000);
  } catch {
    ElMessage.error("Нет доступа к API");
    logout();
  }
});

onUnmounted(() => {
  if (dnsTimer) clearInterval(dnsTimer);
});
</script>

<style scoped>
.layout {
  min-height: 100%;
  flex-direction: column;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color);
  gap: 12px;
}
.title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}
.title {
  font-weight: 600;
}
.nav-links {
  display: flex;
  gap: 12px;
  align-items: center;
}
.nav-link {
  color: var(--el-color-primary);
  text-decoration: none;
  font-size: 14px;
}
.nav-link.router-link-active {
  font-weight: 600;
}
.dns-status {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.probe-hint {
  font-size: 12px;
  max-width: min(420px, 40vw);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.probe-src {
  opacity: 0.82;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.lat {
  opacity: 0.85;
  font-weight: 400;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.form-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
}
.soa-form {
  max-width: 720px;
}
.mt8 {
  margin-top: 8px;
}
.ttl-input {
  width: 110px;
}
.dnssec-card {
  margin-bottom: 0;
}
.dnssec-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.dnssec-label {
  font-size: 14px;
}
.small-p {
  margin: 0 0 8px;
  font-size: 13px;
}
.mono-small {
  font-family: monospace;
  font-size: 13px;
}
.ds-tabs {
  margin-top: 4px;
}
.zone-editor {
  width: 100%;
}
.tab-toolbar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 12px;
}
.tab-toolbar-alert {
  width: 100%;
}
.zone-editor-tabs {
  width: 100%;
}
.mb12 {
  margin-bottom: 12px;
}
.roles-card {
  margin-bottom: 0;
}
.roles-hint {
  margin-top: 12px;
}
.records-wrapper {
  width: 100%;
  overflow-x: auto;
}
.dns-records-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.dns-records-table thead th {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  padding: 6px 10px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  text-align: left;
  white-space: nowrap;
}
.col-name  { min-width: 200px; }
.col-ttl   { width: 120px; }
.col-type  { width: 130px; }
.col-value { min-width: 240px; }
.col-action { width: 90px; }

.group-header-row td {
  background: var(--el-fill-color);
  border: 1px solid var(--el-border-color);
  border-top: 2px solid var(--el-border-color-darker);
  padding: 4px 10px;
  cursor: pointer;
  user-select: none;
}
.group-header-inner {
  display: flex;
  align-items: center;
  gap: 8px;
}
.collapse-icon { color: var(--el-text-color-secondary); flex-shrink: 0; }
.group-name { font-weight: 600; color: var(--el-text-color-primary); }
.group-add-btn { margin-left: auto; }

.record-row td {
  border: 1px solid var(--el-border-color-lighter);
  padding: 4px 8px;
  vertical-align: middle;
}
.action-cell { text-align: center; }
.follower-badge { margin-left: 8px; }
.source-tag { margin-left: 4px; flex-shrink: 0; }
:deep(.el-textarea__inner) {
  word-break: break-all;
}
</style>
