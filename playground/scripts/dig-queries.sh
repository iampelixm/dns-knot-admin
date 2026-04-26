#!/usr/bin/env bash
# DNS-запросы ко всем трём серверам.
# Демонстрирует разные типы записей и сравнивает ответы серверов.
set -euo pipefail
cd "$(dirname "$0")"
source vars.sh

require_running

SERVERS=(
    "primary:${PRIMARY_HOST}:${PRIMARY_PORT}"
    "secondary1:${SECONDARY1_HOST}:${SECONDARY1_PORT}"
    "secondary2:${SECONDARY2_HOST}:${SECONDARY2_PORT}"
)

# Вспомогательная: запрос на один сервер с форматированием
query_one() {
    local label="$1" host="$2" port="$3" name="$4" type="$5"
    local result
    result=$(dig @"$host" -p "$port" "$name" "$type" +short +time=2 +tries=1 2>/dev/null)
    if [[ -n "$result" ]]; then
        printf "    %-12s %s\n" "${label}:" "$result"
    else
        printf "    %-12s %s\n" "${label}:" "(нет ответа)"
    fi
}

# Запрос на все три сервера
query_all() {
    local name="$1" type="$2"
    echo ""
    echo "  dig $name $type"
    for s in "${SERVERS[@]}"; do
        IFS=: read -r label host port <<< "$s"
        query_one "$label" "$host" "$port" "$name" "$type"
    done
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║           DNS-запросы к playground серверам          ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── SOA (serial синхронизации) ──────────────────────────────────────────────
section "SOA — serial синхронизации"
for zone in "${ZONES[@]}"; do
    echo ""
    info "Зона: $zone"
    for s in "${SERVERS[@]}"; do
        IFS=: read -r label host port <<< "$s"
        serial=$(dig_soa_serial "$host" "$port" "$zone")
        if [[ -n "$serial" ]]; then
            ok "$label → serial $serial"
        else
            fail "$label → нет ответа"
        fi
    done
done

# ── example.test ────────────────────────────────────────────────────────────
section "example.test — основные типы записей"
query_all "example.test"          "A"
query_all "example.test"          "NS"
query_all "example.test"          "MX"
query_all "example.test"          "TXT"
query_all "www.example.test"      "A"
query_all "www.example.test"      "AAAA"
query_all "ftp.example.test"      "CNAME"
query_all "mail.example.test"     "A"
query_all "api.example.test"      "A"
query_all "ns1.example.test"      "A"
query_all "ns2.example.test"      "A"
query_all "ns3.example.test"      "A"

# ── TXT-записи (DMARC, DKIM) ────────────────────────────────────────────────
section "example.test — TXT (SPF, DMARC, DKIM)"
query_all "_dmarc.example.test"               "TXT"
query_all "default._domainkey.example.test"   "TXT"

# ── SRV ─────────────────────────────────────────────────────────────────────
section "example.test — SRV"
query_all "_xmpp-client._tcp.example.test"    "SRV"

# ── playground.local ────────────────────────────────────────────────────────
section "playground.local — инфра-зона"
query_all "playground.local"          "A"
query_all "primary.playground.local"  "A"
query_all "slave1.playground.local"   "A"
query_all "slave2.playground.local"   "A"
query_all "cache.playground.local"    "A"
query_all "db.playground.local"       "CNAME"
query_all "sync.playground.local"     "TXT"

# ── db.test ─────────────────────────────────────────────────────────────────
section "db.test — зона баз данных"
query_all "db.test"                        "NS"
query_all "primary.db.test"                "A"
query_all "replica1.db.test"               "A"
query_all "postgres.db.test"               "CNAME"
query_all "_postgresql._tcp.db.test"       "SRV"

# ── Отладочный запрос: версия сервера ───────────────────────────────────────
section "Версия и NSID серверов"
for s in "${SERVERS[@]}"; do
    IFS=: read -r label host port <<< "$s"
    echo ""
    info "$label ($host:$port)"
    echo "  NSID:"
    dig @"$host" -p "$port" +nsid +short version.bind TXT CH 2>/dev/null \
        | sed 's/^/    /' || echo "    (нет ответа)"
    echo "  version.bind:"
    dig @"$host" -p "$port" version.bind TXT CH +short 2>/dev/null \
        | sed 's/^/    /' || true
done

echo ""
