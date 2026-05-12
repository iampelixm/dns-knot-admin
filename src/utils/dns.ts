export function defaultZoneContent(zone: string): string {
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

export function messageFromAxios(err: unknown, fallback: string): string {
  const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d))
    return d
      .map((x) => {
        if (typeof x === "string") return x;
        if (typeof x === "object" && x && "msg" in x) return String((x as { msg: string }).msg);
        return JSON.stringify(x);
      })
      .join("; ");
  return fallback;
}
