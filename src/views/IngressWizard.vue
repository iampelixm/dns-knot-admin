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
      </div>
      <el-button link type="danger" @click="logout">Выйти</el-button>
    </el-header>

    <el-main class="main">
      <div class="page-wrap">
        <el-tabs v-model="activeTab" class="tabs">

          <!-- ── Tab 1: Список Ingress ── -->
          <el-tab-pane label="Ресурсы Ingress" name="list">
            <div class="list-toolbar">
              <el-select
                v-model="listNamespace"
                placeholder="Все namespace"
                clearable filterable
                style="width: 220px"
                @change="loadIngresses"
              >
                <el-option v-for="ns in namespaces" :key="ns" :label="ns" :value="ns" />
              </el-select>
              <el-button :loading="ingressesLoading" @click="loadIngresses">Обновить</el-button>
              <div style="flex: 1" />
              <el-button
                type="primary"
                :disabled="selectedIngresses.length !== 1"
                @click="openInWizard(selectedIngresses[0])"
              >Открыть в мастере</el-button>
              <el-button
                :disabled="!selectedIngresses.length"
                :loading="exportLoading"
                @click="exportSelected"
              >Скачать YAML</el-button>
            </div>

            <el-alert
              v-if="ingressesError"
              type="warning" :title="ingressesError"
              :closable="false" show-icon
              style="margin-bottom: 12px"
            />

            <el-table
              :data="ingresses"
              v-loading="ingressesLoading"
              border size="small"
              @selection-change="selectedIngresses = $event"
            >
              <el-table-column type="selection" width="44" />
              <el-table-column label="Namespace" prop="namespace" sortable min-width="120" />
              <el-table-column label="Имя" prop="name" sortable min-width="160" />
              <el-table-column label="Class" prop="ingress_class" width="100">
                <template #default="{ row }">
                  <span class="muted">{{ row.ingress_class || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="Хосты" min-width="200">
                <template #default="{ row }">
                  <template v-if="row.hosts.length">
                    <el-tag
                      v-for="h in row.hosts" :key="h"
                      size="small" effect="plain" style="margin-right: 4px"
                    >{{ h }}</el-tag>
                  </template>
                  <span v-else class="muted">*</span>
                </template>
              </el-table-column>
              <el-table-column label="Сервисы" min-width="160">
                <template #default="{ row }">
                  <span class="mono">{{ uniqueServices(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="TLS" width="55" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.tls" type="success" size="small" effect="plain">TLS</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Возраст" prop="age" width="70" align="center" />
              <el-table-column width="90" align="center">
                <template #default="{ row }">
                  <el-button size="small" link type="primary" @click="openInWizard(row)">Клонировать</el-button>
                </template>
              </el-table-column>
            </el-table>

            <p v-if="!ingressesLoading && !ingresses.length && !ingressesError" class="muted center-text">
              Ingress-ресурсов не найдено
            </p>
          </el-tab-pane>

          <!-- ── Tab 2: Мастер ── -->
          <el-tab-pane label="Создать манифест" name="wizard">
            <el-card shadow="never" class="wizard-card">
              <el-steps :active="currentStep" finish-status="success" class="steps">
                <el-step title="DNS-записи" />
                <el-step title="Правила" />
                <el-step title="TLS" />
                <el-step title="Аннотации" />
                <el-step title="Результат" />
              </el-steps>

              <div class="step-body">

                <!-- ──── Step 0: DNS records ──── -->
                <div v-if="currentStep === 0">
                  <div class="fqdn-toolbar">
                    <el-select
                      v-model="selectedZones"
                      placeholder="Выберите зоны"
                      multiple collapse-tags filterable
                      v-loading="zonesLoading"
                      style="min-width: 280px; flex: 1"
                      @change="loadFqdns"
                    >
                      <el-option
                        v-for="z in dnsZones"
                        :key="z.name" :label="z.name" :value="z.name"
                      />
                    </el-select>
                    <el-button :loading="fqdnsLoading" @click="loadFqdns">Загрузить</el-button>
                  </div>

                  <el-alert
                    v-if="fqdnsError"
                    type="warning" :title="fqdnsError"
                    :closable="false" show-icon
                    style="margin: 8px 0"
                  />

                  <el-table
                    :data="fqdnRows"
                    v-loading="fqdnsLoading"
                    border size="small"
                    class="fqdn-table"
                    @selection-change="onFqdnSelectionChange"
                  >
                    <el-table-column type="selection" width="44" />
                    <el-table-column label="FQDN" prop="fqdn" sortable min-width="220" />
                    <el-table-column label="Тип" prop="rtype" width="70" align="center">
                      <template #default="{ row }">
                        <el-tag size="small" effect="plain" :type="row.rtype === 'CNAME' ? 'warning' : 'info'">
                          {{ row.rtype }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="Зона" prop="zone" width="160" />
                  </el-table>

                  <p v-if="!fqdnsLoading && !fqdnRows.length && selectedZones.length" class="muted" style="margin: 8px 0">
                    В выбранных зонах нет A/AAAA/CNAME записей
                  </p>
                  <p v-else-if="!selectedZones.length" class="muted" style="margin: 8px 0">
                    Выберите одну или несколько DNS-зон для отображения записей
                  </p>

                  <el-divider style="margin: 16px 0" />

                  <el-form label-position="top" class="step-form">
                    <el-form-item label="Имя Ingress" required>
                      <el-input
                        v-model="form.name"
                        placeholder="my-app-ingress"
                        @input="nameManuallyEdited = true"
                      />
                    </el-form-item>

                    <el-form-item label="Namespace" required>
                      <el-alert
                        v-if="namespacesError"
                        type="warning" :title="namespacesError"
                        :closable="false" show-icon
                        style="margin-bottom: 8px"
                      />
                      <el-select
                        v-if="!namespacesError"
                        v-model="form.namespace"
                        placeholder="Выберите namespace"
                        filterable
                        v-loading="namespacesLoading"
                        style="width: 100%"
                        @change="onNamespaceChange"
                      >
                        <el-option v-for="ns in namespaces" :key="ns" :label="ns" :value="ns" />
                      </el-select>
                      <el-input
                        v-else
                        v-model="form.namespace"
                        placeholder="Введите namespace вручную"
                        @blur="onNamespaceChange(form.namespace)"
                      />
                    </el-form-item>

                    <el-form-item label="Ingress Class">
                      <el-select
                        v-model="form.ingress_class"
                        placeholder="nginx"
                        filterable allow-create
                        style="width: 100%"
                      >
                        <el-option label="nginx" value="nginx" />
                        <el-option label="traefik" value="traefik" />
                        <el-option label="haproxy" value="haproxy" />
                        <el-option label="kong" value="kong" />
                        <el-option label="istio" value="istio" />
                      </el-select>
                    </el-form-item>
                  </el-form>
                </div>

                <!-- ──── Step 1: Rules ──── -->
                <div v-else-if="currentStep === 1">
                  <el-alert
                    v-if="servicesError"
                    type="warning" :title="servicesError"
                    :closable="false" show-icon
                    style="margin-bottom: 12px"
                  />

                  <div v-for="(rule, rIdx) in form.rules" :key="rIdx" class="rule-block">
                    <div class="rule-header">
                      <span class="rule-label">Правило {{ rIdx + 1 }}</span>
                      <el-button
                        v-if="form.rules.length > 1"
                        link type="danger" size="small"
                        @click="removeRule(rIdx)"
                      >Удалить</el-button>
                    </div>

                    <el-form label-position="top" class="step-form">
                      <el-form-item label="Хост">
                        <el-input v-model="rule.host" placeholder="example.com (оставьте пустым для catch-all)" />
                      </el-form-item>
                    </el-form>

                    <div v-for="(path, pIdx) in rule.paths" :key="pIdx" class="path-block">
                      <el-form label-position="top" class="path-form">
                        <el-form-item label="Путь">
                          <el-input v-model="path.path" placeholder="/" style="width: 120px" />
                        </el-form-item>
                        <el-form-item label="Тип пути">
                          <el-select v-model="path.path_type" style="width: 180px">
                            <el-option label="Prefix" value="Prefix" />
                            <el-option label="Exact" value="Exact" />
                            <el-option label="ImplementationSpecific" value="ImplementationSpecific" />
                          </el-select>
                        </el-form-item>
                        <el-form-item label="Сервис">
                          <el-select
                            v-if="!servicesError && cachedServices.length"
                            v-model="path.service_name"
                            filterable allow-create
                            placeholder="Выберите сервис"
                            style="width: 200px"
                            @change="(v: string) => onServiceChange(path, v)"
                          >
                            <el-option v-for="svc in cachedServices" :key="svc.name" :label="svc.name" :value="svc.name" />
                          </el-select>
                          <el-input v-else v-model="path.service_name" placeholder="имя сервиса" style="width: 200px" />
                        </el-form-item>
                        <el-form-item label="Порт">
                          <el-select
                            v-if="portsForService(path.service_name).length"
                            v-model="path.service_port"
                            style="width: 140px"
                          >
                            <el-option
                              v-for="p in portsForService(path.service_name)"
                              :key="p.port"
                              :label="p.name ? `${p.name} (${p.port})` : String(p.port)"
                              :value="p.port"
                            />
                          </el-select>
                          <el-input-number
                            v-else v-model="path.service_port"
                            :min="1" :max="65535"
                            placeholder="80" style="width: 140px"
                          />
                        </el-form-item>
                        <el-form-item label=" " style="align-self: flex-end">
                          <el-button
                            v-if="rule.paths.length > 1"
                            link type="danger" size="small"
                            @click="removePath(rule, pIdx)"
                          >✕</el-button>
                        </el-form-item>
                      </el-form>
                    </div>

                    <el-button link size="small" @click="addPath(rule)" style="margin-top: 4px">
                      + Добавить путь
                    </el-button>
                  </div>

                  <el-button @click="addRule" style="margin-top: 12px">+ Добавить правило</el-button>
                </div>

                <!-- ──── Step 2: TLS ──── -->
                <div v-else-if="currentStep === 2">
                  <el-form label-position="top" class="step-form">
                    <el-form-item label="TLS">
                      <el-switch v-model="tlsEnabled" active-text="Включить TLS" />
                    </el-form-item>
                    <template v-if="tlsEnabled">
                      <el-form-item label="Имя TLS Secret" required>
                        <el-input v-model="tlsForm.secret_name" placeholder="my-app-tls" />
                      </el-form-item>
                      <el-form-item label="Хосты TLS">
                        <!-- Если FQDNs выбраны на шаге 0 — показываем чекбоксы -->
                        <template v-if="selectedFqdns.length">
                          <el-checkbox-group
                            v-model="tlsForm.hosts"
                            style="display: flex; flex-direction: column; gap: 6px"
                          >
                            <el-checkbox
                              v-for="fqdn in selectedFqdns"
                              :key="fqdn"
                              :label="fqdn"
                            >{{ fqdn }}</el-checkbox>
                          </el-checkbox-group>
                        </template>
                        <!-- Fallback: ручной ввод тегами (клонирование или без выбора) -->
                        <template v-else>
                          <div class="tags-input">
                            <el-tag
                              v-for="(host, idx) in tlsForm.hosts" :key="idx"
                              closable style="margin-right: 6px; margin-bottom: 6px"
                              @close="tlsForm.hosts.splice(idx, 1)"
                            >{{ host }}</el-tag>
                            <el-input
                              v-model="tlsHostInput"
                              size="small" placeholder="example.com"
                              style="width: 180px"
                              @keyup.enter="addTlsHost"
                              @blur="addTlsHost"
                            />
                          </div>
                        </template>
                      </el-form-item>
                    </template>
                  </el-form>
                </div>

                <!-- ──── Step 3: Annotations ──── -->
                <div v-else-if="currentStep === 3">
                  <p class="section-label">Распространённые аннотации nginx</p>
                  <el-form label-position="top" class="step-form">
                    <el-form-item v-for="preset in presetAnnotations" :key="preset.key">
                      <template #label>
                        <el-checkbox v-model="preset.enabled" :label="preset.key" />
                      </template>
                      <template v-if="preset.enabled && preset.hasValue">
                        <el-select
                          v-if="preset.widget === 'select'"
                          v-model="preset.value"
                          filterable allow-create
                          :placeholder="preset.placeholder"
                          style="max-width: 360px; width: 100%"
                        >
                          <el-option v-for="opt in preset.options" :key="opt" :label="opt" :value="opt" />
                        </el-select>
                        <el-input
                          v-else
                          v-model="preset.value"
                          :placeholder="preset.placeholder"
                          style="max-width: 360px"
                        />
                      </template>
                    </el-form-item>
                  </el-form>
                  <el-divider />
                  <p class="section-label">Произвольные аннотации</p>
                  <div v-for="(ann, idx) in customAnnotations" :key="idx" class="annotation-row">
                    <el-input v-model="ann.key" placeholder="ключ" style="width: 240px" />
                    <el-input v-model="ann.value" placeholder="значение" style="width: 240px; margin-left: 8px" />
                    <el-button link type="danger" style="margin-left: 8px" @click="customAnnotations.splice(idx, 1)">✕</el-button>
                  </div>
                  <el-button @click="customAnnotations.push({ key: '', value: '' })" style="margin-top: 8px">
                    + Добавить аннотацию
                  </el-button>
                </div>

                <!-- ──── Step 4: Result ──── -->
                <div v-else-if="currentStep === 4">
                  <el-alert
                    v-if="renderError"
                    type="error" :title="renderError"
                    :closable="false" show-icon
                    style="margin-bottom: 12px"
                  />
                  <el-skeleton v-if="rendering" :rows="12" animated />
                  <template v-else-if="renderedYaml">
                    <div class="result-actions">
                      <el-button @click="copyYaml">Копировать</el-button>
                      <el-button @click="downloadYaml">Скачать</el-button>
                    </div>
                    <el-input
                      v-model="renderedYaml"
                      type="textarea" :rows="24"
                      readonly class="yaml-output"
                    />
                  </template>
                </div>

              </div>

              <!-- Navigation -->
              <div class="step-nav">
                <el-button :disabled="currentStep === 0" @click="currentStep -= 1">Назад</el-button>
                <el-button
                  v-if="currentStep < 4"
                  type="primary"
                  :loading="rendering"
                  @click="onNext"
                >{{ currentStep === 3 ? 'Сгенерировать' : 'Далее' }}</el-button>
              </div>
            </el-card>
          </el-tab-pane>

        </el-tabs>
      </div>
    </el-main>

    <!-- Export YAML dialog -->
    <el-dialog v-model="exportDialogVisible" title="Экспорт YAML" width="700px">
      <div class="result-actions" style="margin-bottom: 8px">
        <el-button size="small" @click="copyExportYaml">Копировать</el-button>
        <el-button size="small" @click="downloadExportYaml">Скачать</el-button>
      </div>
      <el-input v-model="exportYaml" type="textarea" :rows="20" readonly class="yaml-output" />
    </el-dialog>

    <AppFooter />
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import AppFooter from "../components/AppFooter.vue";
import {
  AUTH_TOKEN_KEY,
  fetchNamespaces,
  fetchServices,
  fetchIngresses,
  fetchZones,
  fetchZoneFqdns,
  renderIngress,
  type IngressItem,
  type IngressRule,
  type IngressPath,
  type IngressRenderRequest,
  type ServiceInfo,
  type ZoneSummary,
  type FqdnRecord,
} from "../api/client";
import { messageFromAxios } from "../utils/dns";

const router = useRouter();

// ── Tabs ──
const activeTab = ref<"list" | "wizard">("list");

// ── Namespaces ──
const namespaces = ref<string[]>([]);
const namespacesLoading = ref(false);
const namespacesError = ref("");

async function loadNamespaces() {
  namespacesLoading.value = true;
  namespacesError.value = "";
  try {
    namespaces.value = (await fetchNamespaces()).namespaces;
  } catch (e) {
    namespacesError.value = messageFromAxios(e, "Не удалось получить список namespace — введите вручную");
  } finally {
    namespacesLoading.value = false;
  }
}

// ── DNS Zones (for step 0 FQDN picker) ──
const dnsZones = ref<ZoneSummary[]>([]);
const zonesLoading = ref(false);
const selectedZones = ref<string[]>([]);

type FqdnRow = FqdnRecord & { zone: string };
const fqdnRows = ref<FqdnRow[]>([]);
const fqdnsLoading = ref(false);
const fqdnsError = ref("");
const selectedFqdns = ref<string[]>([]);
const nameManuallyEdited = ref(false);

async function loadDnsZones() {
  zonesLoading.value = true;
  try {
    dnsZones.value = (await fetchZones()).zones;
  } catch (e) {
    ElMessage.warning(messageFromAxios(e, "Не удалось загрузить список DNS-зон"));
  } finally {
    zonesLoading.value = false;
  }
}

async function loadFqdns() {
  if (!selectedZones.value.length) {
    fqdnRows.value = [];
    return;
  }
  fqdnsLoading.value = true;
  fqdnsError.value = "";
  try {
    const results = await Promise.all(
      selectedZones.value.map((z) =>
        fetchZoneFqdns(z).then((r) => r.records.map((rec) => ({ ...rec, zone: z })))
      )
    );
    fqdnRows.value = results.flat().sort((a, b) => a.fqdn.localeCompare(b.fqdn));
  } catch (e) {
    fqdnsError.value = messageFromAxios(e, "Не удалось загрузить DNS-записи");
  } finally {
    fqdnsLoading.value = false;
  }
}

function onFqdnSelectionChange(rows: FqdnRow[]) {
  selectedFqdns.value = rows.map((r) => r.fqdn);
  // Автогенерация имени от первой выбранной записи (если не редактировалось вручную)
  if (rows.length > 0 && !nameManuallyEdited.value) {
    form.name = rows[0].fqdn.replace(/\./g, "-") + "-ingress";
  }
}

// ── Ingress list ──
const ingresses = ref<IngressItem[]>([]);
const ingressesLoading = ref(false);
const ingressesError = ref("");
const listNamespace = ref("");
const selectedIngresses = ref<IngressItem[]>([]);

async function loadIngresses() {
  ingressesLoading.value = true;
  ingressesError.value = "";
  try {
    ingresses.value = (await fetchIngresses(listNamespace.value || undefined)).ingresses;
  } catch (e) {
    ingressesError.value = messageFromAxios(e, "Не удалось получить Ingress-ресурсы");
  } finally {
    ingressesLoading.value = false;
  }
}

function uniqueServices(row: IngressItem): string {
  const names = new Set<string>();
  for (const r of row.rules) {
    for (const p of r.paths) {
      if (p.service_name) names.add(`${p.service_name}:${p.service_port}`);
    }
  }
  return [...names].join(", ") || "—";
}

// ── Export selected ──
const exportDialogVisible = ref(false);
const exportYaml = ref("");
const exportLoading = ref(false);

async function exportSelected() {
  if (!selectedIngresses.value.length) return;
  exportLoading.value = true;
  try {
    const parts = await Promise.all(
      selectedIngresses.value.map((ing) =>
        renderIngress({
          name: ing.name,
          namespace: ing.namespace,
          ingress_class: ing.ingress_class || "",
          rules: ing.rules,
          tls: ing.tls,
          annotations: ing.annotations,
        }).then((r) => r.yaml)
      )
    );
    exportYaml.value = parts.join("---\n");
    exportDialogVisible.value = true;
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Ошибка генерации YAML"));
  } finally {
    exportLoading.value = false;
  }
}

async function copyExportYaml() {
  try {
    await navigator.clipboard.writeText(exportYaml.value);
    ElMessage.success("Скопировано в буфер обмена");
  } catch { ElMessage.error("Не удалось скопировать"); }
}

function downloadExportYaml() {
  const names = selectedIngresses.value.map((i) => i.name).join("-");
  const blob = new Blob([exportYaml.value], { type: "text/yaml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `${names}-ingress.yaml`; a.click();
  URL.revokeObjectURL(url);
}

// ── Open in wizard (clone) ──
function openInWizard(ing: IngressItem) {
  currentStep.value = 0;
  renderedYaml.value = "";
  renderError.value = "";
  nameManuallyEdited.value = true; // не перезаписывать имя при клонировании

  form.name = ing.name;
  form.namespace = ing.namespace;
  form.ingress_class = ing.ingress_class || "nginx";
  form.rules = ing.rules.length
    ? ing.rules.map((r) => ({
        host: r.host || "",
        paths: r.paths.length
          ? r.paths.map((p) => ({ ...p }))
          : [{ path: "/", path_type: "Prefix" as const, service_name: "", service_port: 80 }],
      }))
    : [{ host: "", paths: [{ path: "/", path_type: "Prefix" as const, service_name: "", service_port: 80 }] }];

  tlsEnabled.value = !!ing.tls;
  tlsForm.secret_name = ing.tls?.secret_name || "";
  tlsForm.hosts = ing.tls ? [...ing.tls.hosts] : [];

  for (const p of presetAnnotations) p.enabled = false;
  customAnnotations.splice(0);
  for (const [k, v] of Object.entries(ing.annotations)) {
    const preset = presetAnnotations.find((p) => p.key === k);
    if (preset) { preset.enabled = true; if (preset.hasValue) preset.value = v; }
    else customAnnotations.push({ key: k, value: v });
  }

  if (ing.namespace) loadServices(ing.namespace);
  activeTab.value = "wizard";
}

// ── Services ──
const servicesCache = ref<Record<string, ServiceInfo[]>>({});
const servicesLoading = ref(false);
const servicesError = ref("");
const cachedServices = ref<ServiceInfo[]>([]);

async function loadServices(ns: string) {
  if (!ns) return;
  if (servicesCache.value[ns]) { cachedServices.value = servicesCache.value[ns]; return; }
  servicesLoading.value = true;
  servicesError.value = "";
  try {
    const resp = await fetchServices(ns);
    servicesCache.value[ns] = resp.services;
    cachedServices.value = resp.services;
  } catch (e) {
    servicesError.value = messageFromAxios(e, "Не удалось получить сервисы — введите имя вручную");
    cachedServices.value = [];
  } finally {
    servicesLoading.value = false;
  }
}

function portsForService(serviceName: string) {
  return cachedServices.value.find((s) => s.name === serviceName)?.ports ?? [];
}

function onNamespaceChange(ns: string) {
  if (ns) loadServices(ns);
}

function onServiceChange(path: IngressPath, serviceName: string) {
  const ports = portsForService(serviceName);
  if (ports.length) path.service_port = ports[0].port;
}

// ── Wizard form ──
const currentStep = ref(0);

const form = reactive<IngressRenderRequest>({
  name: "",
  namespace: "",
  ingress_class: "nginx",
  rules: [{ host: "", paths: [{ path: "/", path_type: "Prefix", service_name: "", service_port: 80 }] }],
  tls: null,
  annotations: {},
});

// ── TLS ──
const tlsEnabled = ref(false);
const tlsHostInput = ref("");
const tlsForm = reactive({ secret_name: "", hosts: [] as string[] });

function autoTlsName(): string {
  const n = form.name;
  if (!n) return "";
  return n.endsWith("-ingress") ? n.slice(0, -8) + "-tls" : n + "-tls";
}

function initTlsStep() {
  tlsForm.secret_name = autoTlsName();
  if (selectedFqdns.value.length) {
    tlsForm.hosts = [...selectedFqdns.value];
  }
}

function addTlsHost() {
  const h = tlsHostInput.value.trim();
  if (h && !tlsForm.hosts.includes(h)) tlsForm.hosts.push(h);
  tlsHostInput.value = "";
}

watch(tlsEnabled, (enabled) => {
  if (enabled && !tlsForm.secret_name) {
    tlsForm.secret_name = autoTlsName();
    if (!tlsForm.hosts.length && selectedFqdns.value.length) {
      tlsForm.hosts = [...selectedFqdns.value];
    }
  }
});

// ── Annotations ──
type PresetAnnotation = {
  key: string;
  enabled: boolean;
  hasValue: boolean;
  value: string;
  placeholder: string;
  widget?: "input" | "select";
  options?: string[];
};

const presetAnnotations = reactive<PresetAnnotation[]>([
  // cert-manager
  {
    key: "cert-manager.io/cluster-issuer",
    enabled: false, hasValue: true, value: "letsencrypt-prod",
    placeholder: "letsencrypt-prod",
    widget: "select",
    options: ["letsencrypt-prod", "letsencrypt-staging", "letsencrypt", "zerossl-prod", "selfsigned", "ca-issuer"],
  },
  {
    key: "acme.cert-manager.io/http01-ingress-class",
    enabled: false, hasValue: true, value: "traefik", placeholder: "traefik",
  },
  // traefik
  {
    key: "kubernetes.io/ingress.class",
    enabled: false, hasValue: true, value: "traefik", placeholder: "traefik",
  },
  {
    key: "traefik.ingress.kubernetes.io/router.entrypoints",
    enabled: false, hasValue: true, value: "web,websecure", placeholder: "web,websecure",
  },
  // nginx
  { key: "nginx.ingress.kubernetes.io/rewrite-target", enabled: false, hasValue: true, value: "/", placeholder: "/" },
  { key: "nginx.ingress.kubernetes.io/ssl-redirect", enabled: false, hasValue: false, value: "true", placeholder: "" },
  { key: "nginx.ingress.kubernetes.io/proxy-body-size", enabled: false, hasValue: true, value: "10m", placeholder: "10m" },
  { key: "nginx.ingress.kubernetes.io/proxy-read-timeout", enabled: false, hasValue: true, value: "60", placeholder: "60" },
]);

const customAnnotations = reactive<{ key: string; value: string }[]>([]);

// ── Rules helpers ──
function addRule() {
  form.rules.push({ host: "", paths: [{ path: "/", path_type: "Prefix", service_name: "", service_port: 80 }] });
}
function removeRule(idx: number) { form.rules.splice(idx, 1); }
function addPath(rule: IngressRule) {
  rule.paths.push({ path: "/", path_type: "Prefix", service_name: "", service_port: 80 });
}
function removePath(rule: IngressRule, idx: number) { rule.paths.splice(idx, 1); }

// ── Validation ──
function validateStep(): string | null {
  if (currentStep.value === 0) {
    if (!form.name.trim()) return "Укажите имя Ingress";
    if (!form.namespace) return "Выберите или введите namespace";
  }
  if (currentStep.value === 1) {
    for (const rule of form.rules) {
      if (!rule.paths.length) return "Каждое правило должно содержать хотя бы один путь";
      for (const p of rule.paths) {
        if (!p.service_name.trim()) return "Укажите сервис для всех путей";
        if (!p.service_port) return "Укажите порт для всех путей";
      }
    }
  }
  if (currentStep.value === 2 && tlsEnabled.value) {
    if (!tlsForm.secret_name.trim()) return "Укажите имя TLS Secret";
    if (!tlsForm.hosts.length) return "Добавьте хотя бы один хост для TLS";
  }
  return null;
}

// ── Advance from step 0: pre-fill rules from selected FQDNs ──
function applyFqdnsToRules() {
  if (!selectedFqdns.value.length) return;
  form.rules = selectedFqdns.value.map((fqdn) => ({
    host: fqdn,
    paths: [{ path: "/", path_type: "Prefix" as const, service_name: "", service_port: 80 }],
  }));
}

// ── Result ──
const renderedYaml = ref("");
const rendering = ref(false);
const renderError = ref("");

async function doRender() {
  rendering.value = true;
  renderError.value = "";
  renderedYaml.value = "";

  const annotations: Record<string, string> = {};
  for (const p of presetAnnotations) if (p.enabled) annotations[p.key] = p.value;
  for (const c of customAnnotations) if (c.key.trim()) annotations[c.key.trim()] = c.value;

  const payload: IngressRenderRequest = {
    ...form,
    tls: tlsEnabled.value ? { secret_name: tlsForm.secret_name, hosts: [...tlsForm.hosts] } : null,
    annotations,
  };

  try {
    renderedYaml.value = (await renderIngress(payload)).yaml;
  } catch (e) {
    renderError.value = messageFromAxios(e, "Ошибка генерации манифеста");
  } finally {
    rendering.value = false;
  }
}

async function onNext() {
  const err = validateStep();
  if (err) { ElMessage.warning(err); return; }

  if (currentStep.value === 0) {
    applyFqdnsToRules();
    if (form.namespace) loadServices(form.namespace);
    currentStep.value = 1;
  } else if (currentStep.value === 1) {
    initTlsStep();
    currentStep.value = 2;
  } else if (currentStep.value === 3) {
    currentStep.value = 4;
    await doRender();
  } else {
    currentStep.value += 1;
  }
}

watch(currentStep, (step) => {
  if (step === 4 && !renderedYaml.value && !rendering.value) doRender();
});

// ── Copy / Download ──
async function copyYaml() {
  try {
    await navigator.clipboard.writeText(renderedYaml.value);
    ElMessage.success("Скопировано в буфер обмена");
  } catch { ElMessage.error("Не удалось скопировать"); }
}

function downloadYaml() {
  const blob = new Blob([renderedYaml.value], { type: "text/yaml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `${form.name || "ingress"}-ingress.yaml`; a.click();
  URL.revokeObjectURL(url);
}

// ── Auth ──
function logout() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  router.push({ name: "login" });
}

// ── Lifecycle ──
onMounted(async () => {
  await Promise.all([loadNamespaces(), loadDnsZones(), loadIngresses()]);
});
</script>

<style scoped>
.layout { min-height: 100vh; }

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  padding: 0 20px;
}

.title-row { display: flex; align-items: center; gap: 20px; flex: 1; }
.title { font-size: 18px; font-weight: 700; letter-spacing: 0.03em; }
.nav-links { display: flex; gap: 16px; }

.nav-link {
  color: var(--el-text-color-regular);
  text-decoration: none;
  font-size: 14px;
}
.nav-link.router-link-active { color: var(--el-color-primary); font-weight: 600; }

.main { padding: 20px; background: var(--el-fill-color-lighter); }
.page-wrap { max-width: 1100px; margin: 0 auto; }
.tabs { background: var(--el-bg-color); }

.list-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.fqdn-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.fqdn-table { margin-bottom: 4px; }

.wizard-card { margin-top: 4px; }
.steps { margin-bottom: 28px; }
.step-body { min-height: 280px; }

.step-form {
  max-width: 560px;
  display: flex;
  flex-direction: column;
}

.rule-block {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: var(--el-fill-color-extra-light);
}

.rule-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.rule-label { font-weight: 600; font-size: 13px; }
.path-block { margin-bottom: 6px; }

.path-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-end;
}

.tags-input {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  min-width: 320px;
}

.section-label { font-weight: 600; font-size: 13px; margin: 0 0 8px 0; }
.annotation-row { display: flex; align-items: center; margin-bottom: 8px; }

.step-nav {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.result-actions { display: flex; gap: 8px; margin-bottom: 12px; }
.yaml-output { font-family: monospace; font-size: 13px; }
.muted { color: var(--el-text-color-secondary); }
.mono { font-family: monospace; font-size: 12px; }
.center-text { text-align: center; padding: 16px 0; }
</style>
