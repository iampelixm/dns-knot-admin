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

export type ZoneSummary = { name: string; dnssec_signing: boolean; is_follower?: boolean; follower_of?: string | null };

export type ZonesResponse = { zones: ZoneSummary[] };

export type DnssecPatchResponse = {
  status: string;
  restarted_at: string;
  dnssec_signing: boolean;
};

export type DnssecDsResponse = {
  ds: string[];
  /** RR DNSKEY с авторитативного сервера (KSK+ZSK), строки в виде zone-файла */
  dnskey?: string[];
  message: string;
  /** .RU / .РФ / .SU — регистраторы часто требуют и DNSKEY, и DS */
  registrar_ru_family?: boolean;
};
export type ZoneResponse = { zone: string; content: string };

export type ZoneContentResponse = { content: string };

export type ZoneMetaResponse = { zone: Record<string, unknown> };
export type ValidateResponse = { valid: boolean; errors: string[] };
export type DnsHealthResponse = {
  ok: boolean;
  message: string;
  latency_ms: number | null;
  /** Куда ушёл UDP SOA (имя или IP). Старый API может не отдавать поле. */
  probe_host?: string;
  /** KNOT_DNS_PROBE_HOST | knot.conf.listen | knot_pod_ip | KNOT_DNS_HOST */
  probe_source?: string;
  probe_port?: number;
};

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
  source?: string;
};

export type RecordRowWithSource = RecordRow;

export type ZoneFormModel = {
  soa: SoaForm;
  ns: NsRow[];
  records: RecordRow[];
};

export type ZoneFormWithSource = ZoneFormModel;

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

/** Диагностика AXFR Secret в ответе validate и GET /api/knot-conf/axfr-status */
export type AxfrClusterDiag = {
  readable: boolean;
  code: string;
  message: string;
  hints: string[];
  namespace: string;
  secret_name: string;
  secret_key: string;
  keys_in_data: string[];
  http_status: number | null;
};

/** Ответ POST /api/knot-conf/validate */
export type KnotConfValidateResponse = {
  ok: boolean;
  yaml_ok: boolean;
  yaml_error?: string | null;
  knotc: { ran: boolean; ok: boolean; message: string } | null;
  axfr?: {
    config_includes_knot_path: boolean;
    source: string;
    cluster: AxfrClusterDiag | null;
    hints: string[];
  } | null;
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

export type AxfrKeyRow = {
  id: string;
  algorithm: string;
  secret: string;
  storage?: string | null;
  file?: string | null;
};

export type AxfrAclRow = {
  id: string;
  action: string;
  address: string[];
  key?: string | null;
};

export type AxfrStructured = {
  keys: AxfrKeyRow[];
  acls: AxfrAclRow[];
};

export type AxfrGetResponse = {
  content: string;
  structured: AxfrStructured | null;
  structured_parse_warning?: string | null;
};

export type AxfrParseFragmentResponse = {
  structured: AxfrStructured | null;
  structured_parse_warning?: string | null;
};

export type AxfrTsigGenerateResponse = {
  yaml: string;
  key_id: string;
  structured?: AxfrStructured | null;
  structured_parse_warning?: string | null;
};

export type KnotInstance = {
  id: string;
  label: string;
  ip: string;
  role: "primary" | "secondary" | string;
  configmap?: string;
  deployment?: string;
};

export type ZoneServerStatus = {
  id: string;
  label: string;
  ip: string;
  role: string;
  ok: boolean;
  serial: number | null;
  synced: boolean | null;
  message: string | null;
};

export type ZoneSyncEntry = {
  zone: string;
  servers: ZoneServerStatus[];
  primary_serial: number | null;
};

export type ZonesSyncStatusResponse = {
  instances: KnotInstance[];
  zones: ZoneSyncEntry[];
  warning?: string;
};

export type KnotConfModel = {
  server: Record<string, string>;
  include: string;
  zone: Array<{
    domain: string;
    file: string;
    master: string;
    notify: string;
    acl: string[];
    "dnssec-signing": string;
  }>;
  form_parse_warning?: string | null;
};

export async function fetchZones(): Promise<ZonesResponse> {
  const { data } = await api.get<ZonesResponse>("/api/zones");
  return data;
}

export type FqdnRecord = { fqdn: string; rtype: string };
export type ZoneFqdnsResponse = { records: FqdnRecord[] };

export async function fetchZoneFqdns(zone: string): Promise<ZoneFqdnsResponse> {
  const { data } = await api.get<ZoneFqdnsResponse>(`/api/zones/${encodeURIComponent(zone)}/fqdns`);
  return data;
}

// --- Ingress Wizard ---

export type NamespacesResponse = { namespaces: string[] };

export type ServicePortInfo = { name: string | null; port: number; protocol: string };
export type ServiceInfo = { name: string; ports: ServicePortInfo[] };
export type ServicesResponse = { services: ServiceInfo[] };

export type IngressPath = {
  path: string;
  path_type: "Prefix" | "Exact" | "ImplementationSpecific";
  service_name: string;
  service_port: number;
};

export type IngressRule = { host: string; paths: IngressPath[] };

export type IngressTls = { secret_name: string; hosts: string[] };

export type IngressRenderRequest = {
  name: string;
  namespace: string;
  ingress_class: string;
  rules: IngressRule[];
  tls: IngressTls | null;
  annotations: Record<string, string>;
};

export type IngressRenderResponse = { yaml: string };

export async function fetchNamespaces(): Promise<NamespacesResponse> {
  const { data } = await api.get<NamespacesResponse>("/api/k8s/namespaces");
  return data;
}

export async function fetchServices(namespace: string): Promise<ServicesResponse> {
  const { data } = await api.get<ServicesResponse>("/api/k8s/services", { params: { namespace } });
  return data;
}

export async function renderIngress(body: IngressRenderRequest): Promise<IngressRenderResponse> {
  const { data } = await api.post<IngressRenderResponse>("/api/k8s/ingress/render", body);
  return data;
}

export type IngressItem = {
  name: string;
  namespace: string;
  ingress_class: string | null;
  hosts: string[];
  rules: IngressRule[];
  tls: IngressTls | null;
  annotations: Record<string, string>;
  age: string;
};

export type IngressListResponse = { ingresses: IngressItem[] };

export async function fetchIngresses(namespace?: string): Promise<IngressListResponse> {
  const { data } = await api.get<IngressListResponse>("/api/k8s/ingresses", {
    params: namespace ? { namespace } : {},
  });
  return data;
}

// --- DNS Servers ---

export type DnsServer = {
  id: string;
  name: string;
  server_type: string;
  address: string;
  port: number;
  tsig_key_id: string | null;
  delivery_method: string;
  delivery_config: string;
  notes: string;
  zone_count: number;
  created_at: string;
  updated_at: string;
};

export type DnsServersResponse = { servers: DnsServer[] };

export type ServerTypeInfo = {
  label: string;
  doc_url: string;
};

export type ServerTypesResponse = { types: Record<string, ServerTypeInfo> };

export type DnsServerInstructionResponse = { instruction: string };

export type CreateDnsServerBody = {
  id?: string;
  name: string;
  server_type: string;
  address: string;
  port: number;
  tsig_key_id?: string | null;
  create_key?: boolean;
  delivery_method?: string;
  delivery_config?: string;
  notes?: string;
};

export async function fetchDnsServers(): Promise<DnsServersResponse> {
  const { data } = await api.get<DnsServersResponse>("/api/dns-servers");
  return data;
}

export async function fetchServerTypes(): Promise<ServerTypesResponse> {
  const { data } = await api.get<ServerTypesResponse>("/api/dns-servers/types");
  return data;
}

export async function fetchDnsServer(id: string): Promise<{ server: DnsServer }> {
  const { data } = await api.get<{ server: DnsServer }>(`/api/dns-servers/${encodeURIComponent(id)}`);
  return data;
}

export async function fetchServerInstruction(id: string): Promise<DnsServerInstructionResponse> {
  const { data } = await api.get<DnsServerInstructionResponse>(
    `/api/dns-servers/${encodeURIComponent(id)}/instruction`,
  );
  return data;
}

export async function createDnsServer(body: CreateDnsServerBody): Promise<{ server: DnsServer }> {
  const { data } = await api.post<{ server: DnsServer }>("/api/dns-servers", body);
  return data;
}

export async function updateDnsServer(
  id: string,
  body: Partial<CreateDnsServerBody>,
): Promise<{ server: DnsServer }> {
  const { data } = await api.put<{ server: DnsServer }>(
    `/api/dns-servers/${encodeURIComponent(id)}`,
    body,
  );
  return data;
}

export async function deleteDnsServer(id: string): Promise<void> {
  await api.delete(`/api/dns-servers/${encodeURIComponent(id)}`);
}

export type DnsServerConfigResponse = { config: string };

export async function fetchServerConfig(id: string): Promise<DnsServerConfigResponse> {
  const { data } = await api.get<DnsServerConfigResponse>(
    `/api/dns-servers/${encodeURIComponent(id)}/config`,
  );
  return data;
}

export type DeliverResponse = { result: { ok: boolean; message: string; serials?: Record<string, number> } };

export async function fetchDeliver(id: string): Promise<DeliverResponse> {
  const { data } = await api.post<DeliverResponse>(
    `/api/dns-servers/${encodeURIComponent(id)}/deliver`,
  );
  return data;
}

export type FetchConfigResponse = { config?: string; error?: string };

export async function fetchServerConfigFromAgent(id: string): Promise<FetchConfigResponse> {
  const { data } = await api.post<FetchConfigResponse>(
    `/api/dns-servers/${encodeURIComponent(id)}/fetch-config`,
  );
  return data;
}

export type ZoneServer = {
  id: number;
  zone_name: string;
  server_id: string;
  role: string;
  tsig_key_id: string | null;
  serial: number;
  status: string;
  last_ok_at: string | null;
  settings: string;
  created_at: string;
  updated_at: string;
  server_name: string;
  server_type: string;
  address: string;
  port: number;
};

export type ZoneServersResponse = { servers: ZoneServer[] };
export type ServerZonesResponse = { zones: ZoneServer[] };

export async function fetchZoneServers(zoneName: string): Promise<ZoneServersResponse> {
  const { data } = await api.get<ZoneServersResponse>(
    `/api/zones/${encodeURIComponent(zoneName)}/servers`,
  );
  return data;
}

export async function fetchServerZones(serverId: string): Promise<ServerZonesResponse> {
  const { data } = await api.get<ServerZonesResponse>(
    `/api/dns-servers/${encodeURIComponent(serverId)}/zones`,
  );
  return data;
}

export type AssignZoneToServerBody = {
  server_id: string;
  role?: string;
  tsig_key_id?: string | null;
  serial?: number;
  settings?: string;
};

export async function assignZoneToServer(
  zoneName: string,
  body: AssignZoneToServerBody,
): Promise<{ server: ZoneServer }> {
  const { data } = await api.post<{ server: ZoneServer }>(
    `/api/zones/${encodeURIComponent(zoneName)}/servers`,
    body,
  );
  return data;
}

export async function removeZoneFromServer(
  zoneName: string,
  serverId: string,
): Promise<void> {
  await api.delete(`/api/zones/${encodeURIComponent(zoneName)}/servers/${encodeURIComponent(serverId)}`);
}

/** Массовая замена набора серверов зоны (ровно один primary). */
export type ZoneServerReplaceItem = {
  server_id: string;
  role: "primary" | "secondary";
};

export type ZoneServerReplaceResponse = {
  servers: ZoneServer[];
  status: string;
  sync?: {
    updated: string[];
    restarted_at: string;
  };
  sync_error?: string;
};

export async function replaceZoneServers(
  zoneName: string,
  servers: ZoneServerReplaceItem[],
): Promise<ZoneServerReplaceResponse> {
  const { data } = await api.put<ZoneServerReplaceResponse>(
    `/api/zones/${encodeURIComponent(zoneName)}/servers`,
    { servers, resync: true },
  );
  return data;
}

export async function deleteZone(zoneName: string): Promise<void> {
  await api.delete(`/api/zones/${encodeURIComponent(zoneName)}`);
}

// --- Follower zones ---

export type FollowZoneBody = {
  master_zone: string;
  soa: SoaForm;
  ns: NsRow[];
};

export type FollowerInfoResponse = {
  zone: string;
  follower_of: string | null;
};

export type FollowersResponse = {
  followers: string[];
};

export async function followZone(zoneName: string, body: FollowZoneBody): Promise<void> {
  await api.post(`/api/zones/${encodeURIComponent(zoneName)}/follow`, body);
}

export async function unfollowZone(zoneName: string): Promise<void> {
  await api.post(`/api/zones/${encodeURIComponent(zoneName)}/unfollow`);
}

export async function fetchFollowers(zoneName: string): Promise<FollowersResponse> {
  const { data } = await api.get<FollowersResponse>(
    `/api/zones/${encodeURIComponent(zoneName)}/followers`,
  );
  return data;
}

export async function fetchFollowerInfo(zoneName: string): Promise<FollowerInfoResponse> {
  const { data } = await api.get<FollowerInfoResponse>(
    `/api/zones/${encodeURIComponent(zoneName)}/follower-info`,
  );
  return data;
}
