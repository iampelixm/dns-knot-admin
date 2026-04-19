<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="title-row">
        <div class="title">knot.conf</div>
        <nav class="nav-links">
          <router-link class="nav-link" to="/">Зоны</router-link>
          <router-link class="nav-link" to="/knot-conf">knot.conf</router-link>
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
      <el-alert
        v-if="secretsNote"
        type="info"
        show-icon
        class="mb12"
        :closable="false"
        :title="secretsNote.title"
      >
        <p class="note-p">{{ secretsNote.doc }}</p>
        <a v-if="secretsNote.doc_url" :href="secretsNote.doc_url" target="_blank" rel="noopener">Документация Knot</a>
      </el-alert>

      <el-tabs v-model="activeTab" type="border-card">
        <el-tab-pane label="Форма" name="form">
          <el-alert
            v-if="formModel.form_parse_warning"
            type="warning"
            show-icon
            :closable="false"
            :title="formModel.form_parse_warning"
            class="mb12"
          />
          <div class="form-actions mb12">
            <el-button @click="reloadModel" :loading="loading">Обновить из кластера</el-button>
            <el-button @click="validateCurrent('form')" :loading="validateLoading">Проверить (YAML + knotc)</el-button>
            <el-button type="success" @click="saveModel" :loading="saveModelLoading">Сохранить и перезапустить Knot</el-button>
          </div>
          <el-card v-if="serverSection" shadow="never" class="mb12">
            <template #header>{{ serverSection.title }}</template>
            <p v-if="serverSection.doc" class="section-doc">{{ serverSection.doc }}</p>
            <el-form label-width="160px" label-position="left">
              <el-form-item
                v-for="f in serverSection.fields || []"
                :key="f.path.join('.')"
                :label="f.label"
              >
                <template #label>
                  <span>{{ f.label }}</span>
                  <el-tooltip v-if="f.doc" :content="f.doc" placement="top" :show-after="400">
                    <el-icon class="help-ico"><QuestionFilled /></el-icon>
                  </el-tooltip>
                </template>
                <el-select
                  v-if="f.type === 'enum' && f.enum"
                  v-model="formModel.server[fieldKey(f)]"
                  clearable
                  style="width: 100%; max-width: 420px"
                >
                  <el-option v-for="opt in f.enum" :key="opt" :label="opt" :value="opt" />
                </el-select>
                <el-input
                  v-else-if="f.widget === 'textarea'"
                  v-model="formModel.server[fieldKey(f)]"
                  type="textarea"
                  :rows="3"
                  :placeholder="f.placeholder || ''"
                  class="mono"
                />
                <el-input v-else v-model="formModel.server[fieldKey(f)]" clearable style="max-width: 520px" />
                <a v-if="f.doc_url" class="doc-link" :href="f.doc_url" target="_blank" rel="noopener">справка</a>
              </el-form-item>
            </el-form>
          </el-card>

          <el-card v-if="includeSection" shadow="never" class="mb12">
            <template #header>{{ includeSection.title }}</template>
            <el-form label-width="120px">
              <el-form-item
                v-for="f in includeSection.fields || []"
                :key="f.path.join('.')"
                :label="f.label"
              >
                <template #label>
                  <span>{{ f.label }}</span>
                  <el-tooltip v-if="f.doc" :content="f.doc" placement="top" :show-after="400">
                    <el-icon class="help-ico"><QuestionFilled /></el-icon>
                  </el-tooltip>
                </template>
                <el-input v-model="formModel.include" type="textarea" :rows="4" class="mono" />
                <a v-if="f.doc_url" class="doc-link" :href="f.doc_url" target="_blank" rel="noopener">справка</a>
              </el-form-item>
            </el-form>
          </el-card>

          <el-card v-if="zoneSection" shadow="never">
            <template #header>{{ zoneSection.title }}</template>
            <p v-if="zoneSection.doc" class="section-doc">{{ zoneSection.doc }}</p>
            <el-button class="mb12" @click="addZone">Добавить зону</el-button>
            <el-table :data="formModel.zone" border size="small">
              <el-table-column
                v-for="col in zoneSection.item_fields || []"
                :key="col.path.join('.')"
                :label="col.label"
                :min-width="col.widget === 'textarea' ? 200 : 140"
              >
                <template #header>
                  <span>{{ col.label }}</span>
                  <el-tooltip v-if="col.doc" :content="col.doc" placement="top" :show-after="400">
                    <el-icon class="help-ico"><QuestionFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <el-select
                    v-if="col.type === 'enum' && col.enum"
                    v-model="row[zoneFieldProp(col)]"
                    style="width: 100%"
                  >
                    <el-option v-for="opt in col.enum" :key="opt" :label="opt" :value="opt" />
                  </el-select>
                  <el-input
                    v-else-if="col.widget === 'textarea'"
                    v-model="row[zoneFieldProp(col)]"
                    type="textarea"
                    :rows="2"
                    class="mono"
                  />
                  <el-input v-else v-model="row[zoneFieldProp(col)]" clearable />
                </template>
              </el-table-column>
              <el-table-column label="" width="90" align="center">
                <template #default="{ $index }">
                  <el-button type="danger" link size="small" @click="removeZone($index)">Удалить</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="YAML" name="yaml">
          <div class="form-actions mb12">
            <el-button @click="reloadRaw" :loading="loading">Загрузить из кластера</el-button>
            <el-button @click="validateCurrent('yaml')" :loading="validateLoading">Проверить</el-button>
            <el-button type="success" @click="saveRaw" :loading="saveRawLoading">Сохранить и перезапустить Knot</el-button>
          </div>
          <el-input v-model="rawContent" type="textarea" :rows="28" class="mono" placeholder="knot.conf" />
        </el-tab-pane>

        <el-tab-pane label="AXFR (Secret)" name="axfr">
          <el-alert
            v-if="axfrStatus && !axfrStatus.readable"
            type="warning"
            show-icon
            :closable="false"
            :title="`Secret AXFR: ${axfrStatus.code} — ${axfrStatus.message}`"
            class="mb12"
          >
            <ul v-if="axfrStatus.hints.length" class="hint-list">
              <li v-for="(h, i) in axfrStatus.hints" :key="i">{{ h }}</li>
            </ul>
          </el-alert>
          <div class="form-actions mb12">
            <el-button @click="loadAxfr" :loading="axfrLoading">Загрузить</el-button>
            <el-button @click="loadAxfrStatus" :loading="axfrStatusLoading">Диагностика</el-button>
            <el-button @click="generateTsig" :loading="tsigGenLoading">Сгенерировать TSIG</el-button>
            <el-button @click="validateAxfrFragment" :loading="axfrValidateLoading">Проверить с текущим knot.conf</el-button>
            <el-button
              type="success"
              @click="saveAxfr"
              :loading="axfrSaving"
              :disabled="axfrStatus?.code === 'not_found' || axfrStatus?.code === 'forbidden'"
            >
              Сохранить Secret и перезапустить Knot
            </el-button>
          </div>
          <p v-if="axfrStatus?.code === 'not_found'" class="muted mb12">
            Пока Secret нет: сгенерируйте TSIG, вставьте YAML ниже, создайте Secret в кластере (`kubectl create secret generic … --from-file=…`), затем сохраните из UI.
          </p>
          <el-input v-model="axfrContent" type="textarea" :rows="22" class="mono" placeholder="key: / acl:" />
        </el-tab-pane>
      </el-tabs>

      <el-card v-if="validateResult" shadow="never" class="mt12">
        <template #header>Результат проверки</template>
        <el-alert
          v-if="validateAxfrHints.length"
          type="warning"
          show-icon
          :closable="false"
          title="AXFR / Secret"
          class="mb12"
        >
          <ul class="hint-list">
            <li v-for="(h, i) in validateAxfrHints" :key="i">{{ h }}</li>
          </ul>
        </el-alert>
        <pre class="mono pre-block">{{ validateResultText }}</pre>
      </el-card>
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { QuestionFilled } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import {
  api,
  AUTH_TOKEN_KEY,
  type AxfrClusterDiag,
  type DnsHealthResponse,
  type KnotConfGetResponse,
  type KnotConfModel,
  type KnotConfSaveResponse,
  type KnotConfValidateResponse,
  type KnotSchemaField,
  type KnotSchemaResponse,
} from "../api/client";

const router = useRouter();
const activeTab = ref("form");
const loading = ref(false);
const validateLoading = ref(false);
const saveRawLoading = ref(false);
const saveModelLoading = ref(false);
const dnsHealth = ref<DnsHealthResponse | null>(null);
const dnsHealthLoading = ref(false);

const rawContent = ref("");
const schema = ref<KnotSchemaResponse | null>(null);
const validateResult = ref<KnotConfValidateResponse | null>(null);

const formModel = reactive<KnotConfModel>({
  server: {},
  include: "",
  zone: [],
  form_parse_warning: null,
});

const axfrContent = ref("");
const axfrAvailable = ref(true);
const axfrLoading = ref(false);
const axfrSaving = ref(false);
const axfrValidateLoading = ref(false);
const axfrStatus = ref<AxfrClusterDiag | null>(null);
const axfrStatusLoading = ref(false);
const tsigGenLoading = ref(false);

const serverSection = computed(() => schema.value?.sections.find((s) => s.id === "server") ?? null);
const includeSection = computed(() => schema.value?.sections.find((s) => s.id === "include") ?? null);
const zoneSection = computed(() => schema.value?.sections.find((s) => s.id === "zone") ?? null);
const secretsNote = computed(() => schema.value?.secrets_note ?? null);

const validateResultText = computed(() => {
  const v = validateResult.value;
  if (!v) return "";
  return JSON.stringify(v, null, 2);
});

const validateAxfrHints = computed(() => {
  const h = validateResult.value?.axfr?.hints;
  return Array.isArray(h) ? h : [];
});

function fieldKey(f: KnotSchemaField): string {
  return f.path[f.path.length - 1]!;
}

function zoneFieldProp(f: KnotSchemaField): keyof KnotConfModel["zone"][0] {
  const k = f.path[0];
  if (k === "dnssec-signing") return "dnssec-signing";
  return k as keyof KnotConfModel["zone"][0];
}

function messageFromAxios(err: unknown, fallback: string): string {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (typeof d === "object" && d !== null) return JSON.stringify(d);
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

function logout() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  router.push({ name: "login" });
}

function emptyZoneRow(): KnotConfModel["zone"][0] {
  return {
    domain: "",
    file: "",
    master: "",
    notify: "",
    acl: "",
    "dnssec-signing": "off",
  };
}

function assignFormModel(m: KnotConfModel) {
  formModel.server = { ...(m.server || {}) };
  formModel.include = m.include || "";
  formModel.zone.splice(0, formModel.zone.length, ...(m.zone?.length ? m.zone : []).map((z) => ({ ...z })));
  formModel.form_parse_warning = m.form_parse_warning ?? null;
  if (!formModel.zone.length) formModel.zone.push(emptyZoneRow());
}

function ensureServerKeysFromSchema() {
  const sec = serverSection.value;
  if (!sec?.fields) return;
  for (const f of sec.fields) {
    const k = fieldKey(f);
    if (formModel.server[k] === undefined || formModel.server[k] === "") {
      if (f.default !== undefined && f.default !== null) formModel.server[k] = String(f.default);
      else formModel.server[k] = "";
    }
  }
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

async function loadSchema() {
  const { data } = await api.get<KnotSchemaResponse>("/api/knot-conf/schema");
  schema.value = data;
}

async function reloadRaw() {
  loading.value = true;
  try {
    const { data } = await api.get<KnotConfGetResponse>("/api/knot-conf");
    rawContent.value = data.raw;
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить knot.conf"));
  } finally {
    loading.value = false;
  }
}

async function reloadModel() {
  loading.value = true;
  try {
    const { data } = await api.get<KnotConfModel>("/api/knot-conf/model");
    assignFormModel(data);
    ensureServerKeysFromSchema();
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить модель"));
  } finally {
    loading.value = false;
  }
}

async function loadAxfrStatus() {
  axfrStatusLoading.value = true;
  try {
    const { data } = await api.get<AxfrClusterDiag>("/api/knot-conf/axfr-status");
    axfrStatus.value = data;
  } catch {
    axfrStatus.value = null;
  } finally {
    axfrStatusLoading.value = false;
  }
}

async function loadAxfr() {
  axfrLoading.value = true;
  try {
    const { data } = await api.get<{ content: string }>("/api/knot-conf/axfr");
    axfrContent.value = data.content;
    axfrAvailable.value = true;
    await loadAxfrStatus();
  } catch (e) {
    axfrAvailable.value = false;
    axfrContent.value = "";
    const det = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (det && typeof det === "object" && det !== null && "hints" in det) {
      axfrStatus.value = det as AxfrClusterDiag;
    } else {
      await loadAxfrStatus();
    }
  } finally {
    axfrLoading.value = false;
  }
}

async function generateTsig() {
  tsigGenLoading.value = true;
  try {
    const { data } = await api.post<{ yaml: string; key_id: string }>("/api/knot-conf/axfr/generate-tsig", {
      with_acl: true,
    });
    axfrContent.value = data.yaml;
    ElMessage.success(
      `Сгенерирован TSIG «${data.key_id}». Проверьте ACL (адреса вторичек), затем сохраните или создайте Secret в кластере.`,
    );
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось сгенерировать TSIG"));
  } finally {
    tsigGenLoading.value = false;
  }
}

async function loadAll() {
  await loadSchema();
  await Promise.all([reloadRaw(), reloadModel(), loadAxfrStatus(), loadAxfr()]);
}

async function validateCurrent(source: "yaml" | "form") {
  let content = rawContent.value;
  if (source === "form") {
    try {
      const { data } = await api.post<{ content: string }>("/api/knot-conf/render-model", toRawForm());
      content = data.content;
    } catch (e) {
      ElMessage.error(messageFromAxios(e, "Не удалось собрать YAML из формы"));
      return;
    }
  }
  validateLoading.value = true;
  validateResult.value = null;
  try {
    const { data } = await api.post<KnotConfValidateResponse>("/api/knot-conf/validate", { content });
    validateResult.value = data;
    if (data.ok) ElMessage.success("Проверка пройдена");
    else ElMessage.error("Проверка не пройдена — см. блок ниже");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Ошибка проверки"));
  } finally {
    validateLoading.value = false;
  }
}

function toRawForm(): KnotConfModel {
  return JSON.parse(JSON.stringify(formModel)) as KnotConfModel;
}

async function validateAxfrFragment() {
  axfrValidateLoading.value = true;
  validateResult.value = null;
  try {
    const { data: kc } = await api.get<KnotConfGetResponse>("/api/knot-conf");
    const { data } = await api.post<KnotConfValidateResponse>("/api/knot-conf/validate", {
      content: kc.raw,
      axfr_override: axfrContent.value,
    });
    validateResult.value = data;
    if (data.ok) ElMessage.success("Проверка пройдена");
    else ElMessage.error("Проверка не пройдена — см. блок ниже");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Ошибка проверки"));
  } finally {
    axfrValidateLoading.value = false;
  }
}

async function saveRaw() {
  saveRawLoading.value = true;
  validateResult.value = null;
  try {
    const { data } = await api.put<KnotConfSaveResponse>("/api/knot-conf", { content: rawContent.value });
    validateResult.value = data.validation;
    ElMessage.success(`Сохранено, перезапуск ${data.restarted_at}`);
    await reloadModel();
  } catch (e) {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    validateResult.value = typeof d === "object" && d !== null ? (d as KnotConfValidateResponse) : null;
    ElMessage.error(messageFromAxios(e, "Ошибка сохранения"));
  } finally {
    saveRawLoading.value = false;
  }
}

async function saveModel() {
  saveModelLoading.value = true;
  validateResult.value = null;
  try {
    const { data } = await api.put<KnotConfSaveResponse>("/api/knot-conf/model", toRawForm());
    validateResult.value = data.validation;
    rawContent.value = (await api.get<KnotConfGetResponse>("/api/knot-conf")).raw;
    ElMessage.success(`Сохранено, перезапуск ${data.restarted_at}`);
    await reloadModel();
  } catch (e) {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    validateResult.value = typeof d === "object" && d !== null ? (d as KnotConfValidateResponse) : null;
    ElMessage.error(messageFromAxios(e, "Ошибка сохранения"));
  } finally {
    saveModelLoading.value = false;
  }
}

async function saveAxfr() {
  axfrSaving.value = true;
  try {
    await api.put("/api/knot-conf/axfr", { content: axfrContent.value });
    ElMessage.success("Secret обновлён, Knot перезапускается");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось сохранить Secret"));
  } finally {
    axfrSaving.value = false;
  }
}

function addZone() {
  formModel.zone.push(emptyZoneRow());
}

function removeZone(i: number) {
  formModel.zone.splice(i, 1);
  if (!formModel.zone.length) formModel.zone.push(emptyZoneRow());
}

onMounted(async () => {
  try {
    await loadAll();
    await fetchDnsHealth();
  } catch {
    ElMessage.error("Нет доступа к API");
    logout();
  }
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
.form-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.mb12 {
  margin-bottom: 12px;
}
.mt12 {
  margin-top: 12px;
}
.mono :deep(textarea),
.mono :deep(input) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
}
.pre-block {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}
.section-doc {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.help-ico {
  margin-left: 4px;
  vertical-align: middle;
  cursor: help;
  color: var(--el-text-color-secondary);
}
.doc-link {
  margin-left: 8px;
  font-size: 12px;
}
.note-p {
  margin: 0 0 8px;
  font-size: 13px;
}
.hint-list {
  margin: 8px 0 0;
  padding-left: 1.2em;
  font-size: 13px;
}
</style>
