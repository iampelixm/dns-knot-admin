# Changelog

Формат: дата и краткий список изменений. Новые записи добавляйте **вверх** файла.

## 2026-04-19

- **dnsadmin (UI + API):** вкладка редактора зоны «Форма» (SOA, NS, таблица записей) и вкладка «Текст»; синтаксис zone-файла проверяется через API (dnspython) перед сохранением и по кнопке «Проверить синтаксис».
- **dnsadmin:** эндпоинты `POST .../validate`, `.../parse-form`, `.../render-form`, `PUT .../form`; сохранение сырого текста зоны тоже валидируется перед записью в ConfigMap.
- **dnsadmin:** индикатор доступности Knot (`GET /api/dns-health`, UDP SOA); в Deployment добавлены переменные `KNOT_DNS_HOST`, `DNS_HEALTH_PROBE_ZONE`.
- **dnsadmin-ui backend:** исправлены отступы при добавлении зоны в `knot.conf` (`ensure_zone_in_knot_conf` — выравнивание блока `zone:`).
- **dnsadmin-ui backend:** `zone_editor.py` — разбор/сборка зоны, длинные TXT режутся на строки ≤255 символов для валидного BIND-текста.
- **Knot Deployment:** монтирование всех ключей зон из ConfigMap в `/zones` (убран ограниченный список `items`), чтобы зоны, добавленные через API, попадали в pod.
