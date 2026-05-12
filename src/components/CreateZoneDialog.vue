<template>
  <el-dialog
    :model-value="visible"
    title="Новая зона"
    width="640px"
    @update:model-value="$emit('update:visible', $event)"
    @closed="resetForm"
  >
    <el-form label-position="top" class="create-form">

      <!-- Zone name -->
      <el-form-item label="Имя зоны" required>
        <el-input
          v-model="zoneName"
          placeholder="example.com"
          @input="onNameInput"
        />
      </el-form-item>

      <el-divider content-position="left">SOA</el-divider>

      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Primary NS (mname)">
            <el-input v-model="form.soa.primary_ns" placeholder="ns1.example.com." />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Почта администратора">
            <el-input v-model="form.soa.admin_email" placeholder="hostmaster@example.com" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="$TTL (сек)">
            <el-input-number
              v-model="form.soa.ttl"
              :min="1"
              :step="60"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Serial">
            <el-input-number
              v-model="form.soa.serial"
              :min="0"
              :step="1"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <el-collapse-transition>
        <div v-if="showAdvanced">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="Refresh (сек)">
                <el-input-number v-model="form.soa.refresh" :min="1" :step="60" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Retry (сек)">
                <el-input-number v-model="form.soa.retry" :min="1" :step="60" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="Expire (сек)">
                <el-input-number v-model="form.soa.expire" :min="1" :step="3600" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Minimum (сек)">
                <el-input-number v-model="form.soa.minimum" :min="1" :step="60" controls-position="right" style="width:100%" />
              </el-form-item>
            </el-col>
          </el-row>
        </div>
      </el-collapse-transition>

      <el-button link size="small" class="advanced-toggle" @click="showAdvanced = !showAdvanced">
        {{ showAdvanced ? "▲ Скрыть" : "▼ Refresh / Retry / Expire / Minimum" }}
      </el-button>

      <el-divider content-position="left">NS-серверы</el-divider>

      <div v-for="(row, i) in form.ns" :key="i" class="ns-row">
        <el-input v-model="row.host" :placeholder="`ns${i + 1}.${zoneName || 'example.com'}.`" />
        <el-button
          link
          type="danger"
          :disabled="form.ns.length <= 1"
          @click="removeNs(i)"
        >Удалить</el-button>
      </div>
      <el-button size="small" class="mt8" @click="addNs">+ Добавить NS</el-button>

    </el-form>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">Отмена</el-button>
      <el-button type="primary" :loading="creating" @click="submit">Создать</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, toRaw } from "vue";
import { ElMessage } from "element-plus";
import { api, defaultSoa, type ZoneFormModel, type NsRow } from "../api/client";
import { messageFromAxios } from "../utils/dns";

const props = defineProps<{
  visible: boolean;
  existingZones: string[];
}>();

const emit = defineEmits<{
  (e: "update:visible", v: boolean): void;
  (e: "created", zoneName: string): void;
}>();

const zoneName = ref("");
const showAdvanced = ref(false);
const creating = ref(false);

const form = reactive<ZoneFormModel>({
  soa: defaultSoa(),
  ns: [{ host: "" }],
  records: [],
});

// Авто-заполнение SOA и NS при вводе имени зоны.
// Поля перезаписываются только пока они ещё не редактировались вручную
// (содержат паттерн ns1.* / hostmaster@*) — иначе пользователь сам выбрал значение.
function onNameInput() {
  const n = zoneName.value.trim().replace(/\.$/, "");
  if (!n) return;

  if (!form.soa.primary_ns || /^ns1\./.test(form.soa.primary_ns))
    form.soa.primary_ns = `ns1.${n}.`;

  if (!form.soa.admin_email || /^hostmaster@/.test(form.soa.admin_email))
    form.soa.admin_email = `hostmaster@${n}`;

  if (form.ns.length === 1 && (!form.ns[0]!.host || /^ns1\./.test(form.ns[0]!.host)))
    form.ns[0]!.host = `ns1.${n}.`;
}

function addNs() {
  form.ns.push({ host: "" });
}

function removeNs(i: number) {
  if (form.ns.length > 1) form.ns.splice(i, 1);
}

function resetForm() {
  zoneName.value = "";
  showAdvanced.value = false;
  Object.assign(form.soa, defaultSoa());
  form.ns.splice(0, form.ns.length, { host: "" } as NsRow);
  form.records.splice(0, form.records.length);
}

async function submit() {
  const name = zoneName.value.trim().replace(/\.$/, "");
  if (!name) {
    ElMessage.warning("Укажите имя зоны");
    return;
  }
  if (props.existingZones.includes(name)) {
    ElMessage.warning("Зона уже существует");
    return;
  }
  if (!form.soa.primary_ns.trim()) {
    ElMessage.warning("Укажите Primary NS");
    return;
  }
  const nsHosts = form.ns.map((r) => r.host.trim()).filter(Boolean);
  if (!nsHosts.length) {
    ElMessage.warning("Добавьте хотя бы один NS-сервер");
    return;
  }

  // Отправляем только заполненные NS
  const payload: ZoneFormModel = {
    ...toRaw(form),
    ns: nsHosts.map((host) => ({ host })),
  };

  creating.value = true;
  try {
    await api.put(`/api/zones/${encodeURIComponent(name)}/form`, payload);
    ElMessage.success("Зона создана, Knot перезапускается");
    emit("update:visible", false);
    emit("created", name);
  } catch (e) {
    ElMessage.error(messageFromAxios(e, "Не удалось создать зону"));
  } finally {
    creating.value = false;
  }
}
</script>

<style scoped>
.create-form :deep(.el-divider__text) {
  font-weight: 600;
  color: var(--el-text-color-regular);
}
.ns-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.ns-row .el-input {
  flex: 1;
}
.advanced-toggle {
  margin-bottom: 4px;
  color: var(--el-text-color-secondary);
}
.mt8 {
  margin-top: 4px;
}
</style>
