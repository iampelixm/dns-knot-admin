<template>
  <div class="wrap">
    <el-card class="card" shadow="hover">
      <template #header>
        <span>dnsadmin — вход</span>
      </template>
      <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="Пользователь">
          <el-input v-model="form.username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="Пароль">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :loading="loading" style="width: 100%">
            Войти
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, AUTH_TOKEN_KEY } from "../api/client";

const route = useRoute();
const router = useRouter();
const loading = ref(false);

const form = reactive({
  username: "",
  password: "",
});

async function onSubmit() {
  loading.value = true;
  try {
    const { data } = await api.post<{ access_token: string }>("/api/auth/login", {
      username: form.username,
      password: form.password,
    });
    sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
    await api.get("/api/zones");
    const redirect = (route.query.redirect as string) || "/";
    await router.replace(redirect);
    ElMessage.success("Успешно");
  } catch {
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
    ElMessage.error("Неверный логин или пароль");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.wrap {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #f0f4ff 0%, #e8f5e9 100%);
}
.card {
  width: 380px;
}
</style>
