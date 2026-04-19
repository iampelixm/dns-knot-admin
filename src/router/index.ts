import { createRouter, createWebHistory } from "vue-router";
import Login from "../views/Login.vue";
import Zones from "../views/Zones.vue";

import { AUTH_TOKEN_KEY } from "../api/client";

function hasAuth(): boolean {
  return !!sessionStorage.getItem(AUTH_TOKEN_KEY);
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: Login, meta: { public: true } },
    { path: "/", name: "zones", component: Zones },
  ],
});

router.beforeEach((to, _from, next) => {
  if (to.meta.public) {
    next();
    return;
  }
  if (!hasAuth()) {
    next({ name: "login", query: { redirect: to.fullPath } });
    return;
  }
  next();
});

export default router;
