<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="title">dnsadmin</div>
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
            @change="loadZone"
          >
            <el-option v-for="z in zones" :key="z" :label="z" :value="z" />
          </el-select>
        </el-col>
        <el-col :span="16">
          <el-button type="primary" @click="loadZone" :loading="loading">Загрузить</el-button>
          <el-button type="success" @click="saveZone" :loading="saving"
            >Сохранить и перезагрузить Knot</el-button
          >
          <el-button type="warning" @click="openCreateDialog">Новая зона</el-button>
        </el-col>
      </el-row>
      <el-input
        v-model="content"
        type="textarea"
        :rows="22"
        placeholder="Содержимое zone-файла..."
        class="mono"
      />

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
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, AUTH_TOKEN_KEY, type ZonesResponse, type ZoneResponse } from "../api/client";
const router = useRouter();

const zones = ref<string[]>([]);
const selectedZone = ref("");
const content = ref("");
const loading = ref(false);
const saving = ref(false);
const createDialogVisible = ref(false);
const newZoneName = ref("");
const newZoneContent = ref("");
const creating = ref(false);

function messageFromAxios(err: unknown, fallback: string): string {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof d === "string") return d;
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

async function loadZones() {
  const { data } = await api.get<ZonesResponse>("/api/zones");
  zones.value = data.zones;
  if (!selectedZone.value && data.zones.length) {
    selectedZone.value = data.zones[0]!;
  }
}

async function loadZone() {
  if (!selectedZone.value) return;
  loading.value = true;
  try {
    const { data } = await api.get<ZoneResponse>(`/api/zones/${encodeURIComponent(selectedZone.value)}`);
    content.value = data.content;
    ElMessage.success("Зона загружена");
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось загрузить зону"));
  } finally {
    loading.value = false;
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
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Ошибка сохранения"));
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  try {
    await loadZones();
    await loadZone();
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
}
.title {
  font-weight: 600;
}
.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
}
</style>