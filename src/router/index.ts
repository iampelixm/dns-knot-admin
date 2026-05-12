import { createRouter, createWebHistory } from "vue-router";
import Login from "../views/Login.vue";
import KnotConfig from "../views/KnotConfig.vue";
import Home from "../views/Home.vue";
import Zones from "../views/Zones.vue";

import { AUTH_TOKEN_KEY } from "../api/client";

function hasAuth(): boolean {
  return !!sessionStorage.getItem(AUTH_TOKEN_KEY);
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: Login, meta: { public: true } },
    { path: "/", name: "home", component: Home },
    { path: "/zones/:zone", name: "zone-editor", component: Zones },
    { path: "/knot-conf", name: "knot-conf", component: KnotConfig },
    { path: "/ingress-wizard", name: "ingress-wizard", component: () => import("../views/IngressWizard.vue") },
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
