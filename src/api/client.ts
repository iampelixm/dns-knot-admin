import axios from "axios";

/** Ключ sessionStorage для JWT после POST /api/auth/login */
export const AUTH_TOKEN_KEY = "dnsadmin_access_token";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "",
});

api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem(AUTH_TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export type ZonesResponse = { zones: string[] };
export type ZoneResponse = { zone: string; content: string };
