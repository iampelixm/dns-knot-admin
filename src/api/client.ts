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
export type ValidateResponse = { valid: boolean; errors: string[] };
export type DnsHealthResponse = { ok: boolean; message: string; latency_ms: number | null };

export type SoaForm = {
  ttl: number;
  primary_ns: string;
  admin_email: string;
  serial: number;
  refresh: number;
  retry: number;
  expire: number;
  minimum: number;
};

export type NsRow = { host: string };

export type RecordRow = {
  name: string;
  /** Пусто — в файле не пишется отдельный TTL (берётся $TTL). */
  ttl?: number;
  rtype: string;
  value: string;
};

export type ZoneFormModel = {
  soa: SoaForm;
  ns: NsRow[];
  records: RecordRow[];
};

export function defaultSoa(): SoaForm {
  const serial = Number(`${new Date().toISOString().slice(0, 10).replace(/-/g, "")}01`);
  return {
    ttl: 300,
    primary_ns: "",
    admin_email: "",
    serial,
    refresh: 7200,
    retry: 3600,
    expire: 1209600,
    minimum: 300,
  };
}

export function emptyZoneForm(): ZoneFormModel {
  return { soa: defaultSoa(), ns: [{ host: "" }], records: [] };
}
