<template>
  <footer class="app-footer">
    <span class="footer-brand">dnsadmin</span>
    <span class="footer-sep">·</span>
    <span>v{{ version }}</span>
    <span class="footer-sep">·</span>
    <span>© {{ year }} Pelixm</span>
    <span class="footer-sep">·</span>
    <a
      href="https://github.com/pelixm/dnsadmin-ui"
      target="_blank"
      rel="noopener"
      class="footer-link"
    >GitHub</a>
  </footer>
</template>

<script setup lang="ts">
import { version } from "../../package.json";

const year = new Date().getFullYear();
</script>

<style scoped>
.app-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 12px 20px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  border-top: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  user-select: none;
}
.footer-brand {
  font-weight: 600;
  letter-spacing: 0.04em;
}
.footer-sep {
  opacity: 0.4;
}
.footer-link {
  color: var(--el-text-color-placeholder);
  text-decoration: none;
}
.footer-link:hover {
  color: var(--el-color-primary);
}
</style>
