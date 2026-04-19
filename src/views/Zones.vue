<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="title-row">
        <div class="title">dnsadmin</div>
        <div class="dns-status">
          <span class="muted">Knot:</span>
          <el-tag v-if="dnsHealth === null" type="info" size="small">проверка…</el-tag>
          <el-tag v-else-if="dnsHealth.ok" type="success" size="small">
            отвечает
            <span v-if="dnsHealth.latency_ms != null" class="lat"> {{ dnsHealth.latency_ms }} ms</span>
          </el-tag>
          <el-tag v-else type="danger" size="small">{{ dnsHealth.message }}</el-tag>
          <el-button link size="small" :loading="dnsHealthLoading" @click="fetchDnsHealth">Обновить</el-button>
        </div>
      </div>
      <el-button link type="danger" @click="logout">Выйти</el-button>
    </el-header>
    <el-main>
      <el-row :gutter="16" align="middle" style="margin-bottom: 12px">
        <el-col :span="8">
          <el-select
            v-model="selectedZone"
            placeholder="Зона"
            filterable
            style="width: 100%"
            @change="onZoneChange"
          >
            <el-option v-for="z in zones" :key="z" :label="z" :value="z" />
          </el-select>
        </el-col>
        <el-col :span="16" class="toolbar">
          <el-button type="primary" @click="loadZone" :loading="loading">Загрузить</el-button>
          <el-button type="warning" @click="openCreateDialog">Новая зона</el-button>
        </el-col>
      </el-row>

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
            <el-button :loading="dsLoading" @click="openDsDialog">Показать DS (SHA-256) для регистратора</el-button>
          </div>
        </el-space>
      </el-card>

      <div class="zone-editor">
        <div class="tab-toolbar">
          <el-alert
            v-if="formParseError && activeTab !== 'text'"
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
          <div v-else class="form-actions">
            <el-button @click="syncFormFromText" :loading="formLoading">Загрузить поля из текста</el-button>
            <el-button @click="applyFormToText" :loading="renderLoading">Перенести в текст (без сохранения)</el-button>
            <el-button type="success" @click="saveFromForm" :loading="formSaving">Сохранить из формы</el-button>
          </div>
        </div>

        <el-tabs v-model="activeTab" type="border-card" class="zone-editor-tabs">
          <el-tab-pane label="Записи" name="records">
          <el-card shadow="never">
            <template #header>A / AAAA / MX / TXT / CNAME …</template>
            <el-table :data="formModel.records" border size="small">
              <el-table-column label="Имя" width="140">
                <template #default="{ row }">
                  <el-input v-model="row.name" placeholder="@" clearable />
                </template>
              </el-table-column>
              <el-table-column label="TTL" width="120">
                <template #default="{ row }">
                  <el-input-number
                    v-model="row.ttl"
                    :min="1"
                    :step="60"
                    controls-position="right"
                    class="ttl-input"
                  />
                </template>
              </el-table-column>
              <el-table-column label="Тип" width="130">
                <template #default="{ row }">
                  <el-select v-model="row.rtype" filterable>
                    <el-option v-for="t in recordTypes" :key="t" :label="t" :value="t" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="Значение">
                <template #default="{ row }">
                  <el-input v-model="row.value" placeholder="1.2.3.4 или 10 mail.example.com" clearable />
                </template>
              </el-table-column>
              <el-table-column width="90" align="center">
                <template #default="{ $index }">
                  <el-button link type="danger" @click="removeRec($index)">Удалить</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button class="mt8" size="small" @click="addRec">Добавить запись</el-button>
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

          <el-tab-pane label="Текст зоны" name="text">
          <el-space direction="vertical" alignment="stretch" style="width: 100%" :size="8">
            <el-alert v-if="validateMessage" :type="validateOk ? 'success' : 'error'" :closable="false" show-icon>
              {{ validateMessage }}
            </el-alert>
            <el-input
              v-model="content"
              type="textarea"
              :rows="22"
              placeholder="Содержимое zone-файла..."
              class="mono"
            />
          </el-space>
          </el-tab-pane>
        </el-tabs>
      </div>

      <el-dialog v-model="createDialogVisible" title="Новая зона" width="560px" @closed="resetCreateForm">
        <el-form label-position="top">
          <el-form-item label="Имя зоны (например example.com)">
            <el-input v-model="newZoneName" placeholder="example.com" clearable />
          </el-form-item>
          <el-form-item label="Содержимое zone-файла (пусто — будет минимальный SOA/NS)">
            <el-input v-model="newZoneContent" type="textarea" :rows="12" class="mono" />
          </el-form-item>
          <el-form-item>
            <el-button @click="fillTemplate">Подставить шаблон по имени</el-button>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="createDialogVisible = false">Отмена</el-button>
          <el-button type="primary" :loading="creating" @click="submitNewZone">Создать</el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="dsDialogVisible" title="DS для регистратора" width="640px" @closed="dsDialogText = ''">
        <el-alert v-if="dsDialogHint" type="warning" :closable="false" show-icon :title="dsDialogHint" class="mb12" />
        <el-input v-model="dsDialogText" type="textarea" :rows="8" readonly class="mono" />
        <template #footer>
          <el-button @click="dsDialogVisible = false">Закрыть</el-button>
          <el-button type="primary" :disabled="!dsDialogText" @click="copyDsToClipboard">Копировать</el-button>
        </template>
      </el-dialog>
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, toRaw } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  api,
  AUTH_TOKEN_KEY,
  emptyZoneForm,
  type DnsHealthResponse,
  type DnssecDsResponse,
  type ZoneFormModel,
  type ZoneSummary,
  type ZonesResponse,
  type ZoneResponse,
  type ValidateResponse,
} from "../api/client";

const router = useRouter();

const zoneSummaries = ref<ZoneSummary[]>([]);
const zones = computed(() => zoneSummaries.value.map((z) => z.name));
const selectedZone = ref("");
const content = ref("");
const loading = ref(false);
const saving = ref(false);
const createDialogVisible = ref(false);
const newZoneName = ref("");
const newZoneContent = ref("");
const creating = ref(false);

const activeTab = ref<"records" | "soa" | "ns" | "text">("records");
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
const dnssecDirty = computed(() => dnssecLocal.value !== dnssecServer.value);

const dsLoading = ref(false);
const dsDialogVisible = ref(false);
const dsDialogText = ref("");
const dsDialogHint = ref("");

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

async function openDsDialog() {
  if (!selectedZone.value) return;
  dsLoading.value = true;
  dsDialogHint.value = "";
  try {
    const { data } = await api.get<DnssecDsResponse>(
      `/api/zones/${encodeURIComponent(selectedZone.value)}/dnssec-ds`,
    );
    dsDialogText.value = data.ds.join("\n");
    dsDialogHint.value = data.message || "";
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
    ElMessage.success("Скопировано в буфер");
  } catch {
    ElMessage.error("Не удалось скопировать");
  }
}

const recordTypes = ["A", "AAAA", "MX", "TXT", "CNAME", "PTR", "SRV", "NS"];

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

function defaultZoneContent(zone: string): string {
  const z = zone.trim().replace(/\.$/, "");
  if (!z) return "";
  const serial = `${new Date().toISOString().slice(0, 10).replace(/-/g, "")}01`;
  return `$ORIGIN ${z}.
$TTL 300
@ IN SOA ns1.${z}. hostmaster.${z}. (
  ${serial} ; serial
  7200       ; refresh
  3600       ; retry
  1209600    ; expire
  300        ; minimum
)
@ IN NS ns1.${z}.
`;
}

function openCreateDialog() {
  resetCreateForm();
  createDialogVisible.value = true;
}

function resetCreateForm() {
  newZoneName.value = "";
  newZoneContent.value = "";
}

function fillTemplate() {
  const name = newZoneName.value.trim().replace(/\.$/, "");
  if (!name) {
    ElMessage.warning("Сначала введите имя зоны");
    return;
  }
  newZoneContent.value = defaultZoneContent(name);
}

async function submitNewZone() {
  const name = newZoneName.value.trim().replace(/\.$/, "");
  if (!name) {
    ElMessage.warning("Укажите имя зоны");
    return;
  }
  if (zones.value.includes(name)) {
    ElMessage.warning("Такая зона уже есть — выберите её в списке и редактируйте");
    return;
  }
  const body = newZoneContent.value.trim() || defaultZoneContent(name);
  if (!body.trim()) {
    ElMessage.warning("Нет содержимого зоны");
    return;
  }
  creating.value = true;
  try {
    await api.put(`/api/zones/${encodeURIComponent(name)}`, { content: body });
    ElMessage.success("Зона создана, Knot перезапускается");
    createDialogVisible.value = false;
    resetCreateForm();
    await loadZones();
    selectedZone.value = name;
    await loadZone();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось создать зону"));
  } finally {
    creating.value = false;
  }
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
}

function assignForm(data: ZoneFormModel) {
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
    })),
  );
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
  if (!selectedZone.value && data.zones.length) {
    selectedZone.value = data.zones[0]!.name;
  }
  syncDnssecFromServer();
}

async function loadZone() {
  if (!selectedZone.value) return;
  loading.value = true;
  formParseError.value = "";
  try {
    const { data } = await api.get<ZoneResponse>(`/api/zones/${encodeURIComponent(selectedZone.value)}`);
    content.value = data.content;
    await syncFormFromText(false);
    ElMessage.success("Зона загружена");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить зону"));
  } finally {
    loading.value = false;
  }
}

async function onZoneChange() {
  syncDnssecFromServer();
  await loadZone();
}

async function syncFormFromText(showToast = true) {
  if (!selectedZone.value) return;
  formLoading.value = true;
  formParseError.value = "";
  try {
    const { data } = await api.post<{ form: ZoneFormModel }>(
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

function addNs() {
  formModel.ns.push({ host: "" });
}

function removeNs(i: number) {
  formModel.ns.splice(i, 1);
  if (!formModel.ns.length) formModel.ns.push({ host: "" });
}

function addRec() {
  formModel.records.push({ name: "@", rtype: "A", value: "" });
}

function removeRec(i: number) {
  formModel.records.splice(i, 1);
}

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
.dns-status {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
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
  margin-bottom: 16px;
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
</style>
