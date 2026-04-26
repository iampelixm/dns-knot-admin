<template>
  <div ref="containerRef" class="zone-cm-wrap" />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { basicSetup, EditorView } from "codemirror";
import { EditorState } from "@codemirror/state";
import { StreamLanguage, defaultHighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { autocompletion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { hoverTooltip } from "@codemirror/view";

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ "update:modelValue": [string] }>();

const containerRef = ref<HTMLElement>();
let view: EditorView | null = null;

// ── DNS Zone syntax ──────────────────────────────────────────────────────────

const dnsLang = StreamLanguage.define({
  name: "dns-zone",
  token(stream) {
    if (stream.eatSpace()) return null;

    // Comment
    if (stream.peek() === ";") {
      stream.skipToEnd();
      return "comment";
    }

    // Quoted string (TXT, SPF …)
    if (stream.peek() === '"') {
      stream.next();
      while (!stream.eol()) {
        const ch = stream.next();
        if (ch === "\\") stream.next();
        else if (ch === '"') break;
      }
      return "string";
    }

    // Directives $ORIGIN / $TTL / $INCLUDE / $GENERATE
    if (stream.match(/^\$(ORIGIN|TTL|INCLUDE|GENERATE)\b/i)) return "keyword";

    // Record types — longer tokens first to avoid partial matches
    if (
      stream.match(
        /^(AAAA|DNSKEY|NSEC3PARAM|NSEC3|RRSIG|TLSA|SSHFP|NAPTR|HINFO|CAA|MX|TXT|CNAME|NS|SOA|PTR|SRV|DS|LOC|RP|A)\b/,
      )
    )
      return "typeName";

    // Record class
    if (stream.match(/^(IN|CH|HS)\b/)) return "keyword";

    // @ (zone apex) and wildcard *
    if (stream.match(/^[@*]/)) return "atom";

    // TTL / numeric values (3600, 86400s, 24h …)
    if (stream.match(/^\d+[smhwdSMHWD]?\b/)) return "number";

    stream.next();
    return null;
  },
});

// ── Autocomplete ──────────────────────────────────────────────────────────────

const RECORD_TYPES = [
  "A", "AAAA", "MX", "TXT", "CNAME", "NS", "SOA", "PTR",
  "SRV", "CAA", "DNSKEY", "DS", "RRSIG", "NSEC", "NSEC3",
  "TLSA", "SSHFP", "NAPTR", "HINFO", "LOC", "RP",
];
const DIRECTIVES = ["$ORIGIN", "$TTL", "$INCLUDE", "$GENERATE"];
const CLASSES = ["IN"];

function dnsCompletion(ctx: CompletionContext): CompletionResult | null {
  const word = ctx.matchBefore(/[$\w][\w$]*/);
  if (!word || (word.from === word.to && !ctx.explicit)) return null;
  const q = word.text.toUpperCase();

  const options = [
    ...RECORD_TYPES.filter((t) => t.startsWith(q)).map((label) => ({
      label,
      type: "type",
      detail: HINTS[label],
      boost: 1,
    })),
    ...DIRECTIVES.filter((d) => d.startsWith(q) || d.slice(1).startsWith(q)).map((label) => ({
      label,
      type: "keyword",
      detail: HINTS[label],
    })),
    ...CLASSES.filter((c) => c.startsWith(q)).map((label) => ({
      label,
      type: "keyword",
      detail: "Класс записи",
    })),
    ...(q === "" || "@".startsWith(q)
      ? [{ label: "@", type: "variable", detail: "Апекс зоны" }]
      : []),
  ];

  if (options.length === 0 && !ctx.explicit) return null;
  return { from: word.from, options };
}

// ── Hover tooltips ────────────────────────────────────────────────────────────

const HINTS: Record<string, string> = {
  A: "IPv4-адрес: 1.2.3.4",
  AAAA: "IPv6-адрес: 2001:db8::1",
  MX: "Почтовый сервер: <приоритет> <хост>",
  TXT: "Текст: SPF, DKIM, верификация домена",
  CNAME: "Псевдоним: <целевое имя>",
  NS: "Сервер имён зоны",
  SOA: "Start of Authority — параметры зоны",
  PTR: "Обратный DNS: <хост>",
  SRV: "Сервис: <приор.> <вес> <порт> <хост>",
  CAA: "Разрешённые УЦ: <флаги> <тег> <значение>",
  DNSKEY: "DNSSEC ключ зоны",
  DS: "DNSSEC: Delegation Signer",
  RRSIG: "DNSSEC: подпись RRset",
  NSEC: "DNSSEC: следующая запись (цепочка)",
  NSEC3: "DNSSEC: следующая запись (хэш)",
  TLSA: "DANE: TLS-сертификат / ключ",
  SSHFP: "Отпечаток SSH-хоста",
  NAPTR: "Именование / телефония (E.164)",
  HINFO: "Тип хоста и ОС",
  LOC: "Географические координаты",
  RP: "Ответственное лицо",
  $ORIGIN: "Суффикс по умолчанию для имён в зоне",
  $TTL: "TTL по умолчанию (секунды или 1h/1d/…)",
  $INCLUDE: "Включить другой zone-файл",
  $GENERATE: "Генерировать серию записей по шаблону",
  IN: "Internet — стандартный класс DNS-записи",
  "@": "Апекс зоны (само имя зоны без поддомена)",
};

const dnsHover = hoverTooltip((view, pos) => {
  const word = view.state.wordAt(pos);
  if (!word) return null;
  const text = view.state.sliceDoc(word.from, word.to).toUpperCase();

  // also check $WORD
  const dollarFrom = word.from > 0 ? word.from - 1 : word.from;
  const withDollar =
    view.state.sliceDoc(dollarFrom, word.to).startsWith("$")
      ? view.state.sliceDoc(dollarFrom, word.to).toUpperCase()
      : null;

  const hint = HINTS[withDollar ?? ""] ?? HINTS[text];
  if (!hint) return null;

  return {
    pos: withDollar ? dollarFrom : word.from,
    end: word.to,
    above: true,
    create() {
      const dom = document.createElement("div");
      dom.className = "cm-dns-tooltip";
      const label = document.createElement("strong");
      label.textContent = withDollar ?? text;
      const desc = document.createElement("span");
      desc.textContent = " — " + hint;
      dom.append(label, desc);
      return { dom };
    },
  };
});

// ── Theme ─────────────────────────────────────────────────────────────────────

const dnsTheme = EditorView.theme({
  "&": {
    fontSize: "13px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    border: "1px solid var(--el-border-color)",
    borderRadius: "4px",
    background: "var(--el-fill-color-blank, #fff)",
  },
  "&.cm-focused": {
    outline: "none",
    borderColor: "var(--el-color-primary)",
    boxShadow: "0 0 0 1px var(--el-color-primary)",
  },
  ".cm-scroller": { overflow: "auto", minHeight: "360px" },
  ".cm-content": { padding: "8px 0" },
  ".cm-line": { padding: "0 12px" },
  ".cm-gutters": {
    background: "var(--el-fill-color-light, #f5f7fa)",
    border: "none",
    borderRight: "1px solid var(--el-border-color-lighter)",
    color: "var(--el-text-color-placeholder)",
  },
  ".cm-activeLineGutter": { background: "var(--el-fill-color, #f0f2f5)" },
  ".cm-activeLine": { background: "var(--el-fill-color, #f0f2f5)" },
  ".cm-dns-tooltip": {
    padding: "4px 8px",
    fontSize: "12px",
    background: "var(--el-bg-color-overlay, #fff)",
    border: "1px solid var(--el-border-color-light)",
    borderRadius: "4px",
    boxShadow: "var(--el-box-shadow-light)",
    maxWidth: "320px",
    lineHeight: "1.5",
  },
  ".cm-tooltip": {
    border: "none",
    boxShadow: "none",
    background: "transparent",
  },
  ".cm-tooltip-autocomplete": {
    "& > ul > li": { padding: "2px 8px" },
    "& > ul": {
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      fontSize: "13px",
    },
  },
});

// ── Editor lifecycle ──────────────────────────────────────────────────────────

function buildExtensions() {
  return [
    basicSetup,
    dnsLang,
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    autocompletion({ override: [dnsCompletion] }),
    dnsHover,
    dnsTheme,
    EditorView.lineWrapping,
    EditorView.updateListener.of((update) => {
      if (update.docChanged) emit("update:modelValue", update.state.doc.toString());
    }),
  ];
}

onMounted(() => {
  view = new EditorView({
    state: EditorState.create({ doc: props.modelValue, extensions: buildExtensions() }),
    parent: containerRef.value!,
  });
});

onBeforeUnmount(() => {
  view?.destroy();
  view = null;
});

watch(
  () => props.modelValue,
  (next) => {
    if (!view) return;
    const cur = view.state.doc.toString();
    if (cur !== next) {
      view.dispatch({ changes: { from: 0, to: cur.length, insert: next } });
    }
  },
);
</script>

<style scoped>
.zone-cm-wrap {
  width: 100%;
}
</style>
