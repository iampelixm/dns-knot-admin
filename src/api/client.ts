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

export type ZoneSummary = { name: string; dnssec_signing: boolean };

export type ZonesResponse = { zones: ZoneSummary[] };

export type DnssecPatchResponse = {
  status: string;
  restarted_at: string;
  dnssec_signing: boolean;
};

export type DnssecDsResponse = { ds: string[]; message: string };
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

/** Ответ POST /api/knot-conf/validate */
export type KnotConfValidateResponse = {
  ok: boolean;
  yaml_ok: boolean;
  yaml_error?: string | null;
  knotc: { ran: boolean; ok: boolean; message: string } | null;
};

export type KnotConfGetResponse = {
  raw: string;
  schema_version: string;
};

export type KnotConfSaveResponse = {
  status: string;
  restarted_at: string;
  validation: KnotConfValidateResponse;
};

export type KnotSchemaField = {
  path: string[];
  label: string;
  type: string;
  enum?: string[];
  default?: string;
  widget?: string;
  placeholder?: string;
  doc?: string;
  doc_url?: string;
  required?: boolean;
};

export type KnotSchemaSection = {
  id: string;
  title: string;
  doc?: string;
  doc_url?: string;
  fields?: KnotSchemaField[];
  repeatable?: boolean;
  item_key?: string;
  item_fields?: KnotSchemaField[];
};

export type KnotSchemaResponse = {
  version: string;
  knot_doc_base?: string;
  sections: KnotSchemaSection[];
  secrets_note?: { title: string; doc: string; doc_url?: string };
};

export type KnotConfModel = {
  server: Record<string, string>;
  include: string;
  zone: Array<{
    domain: string;
    file: string;
    master: string;
    notify: string;
    acl: string;
    "dnssec-signing": string;
  }>;
  form_parse_warning?: string | null;
};
