// Approved phase 7.11 tokens. Keep this file byte-identical in both apps.
// Amber/orange are fills, not text on white. Every normal-text pairing below meets AA.
import { createTheme, type CSSVariablesResolver, type MantineColorsTuple } from "@mantine/core";

export const SHARED = {
  header: "#2E343E",
  onFill: "#FFFFFF",
  primary: "#FAB800",
  primaryHover: "#E5A800",
  focus: "#1D4ED8",
  danger: "#B42318",
  warning: "#FAB800",
  info: "#DBEAFE",
  marker: "#FAB800",
  onBright: "#1F2329",
  disabled: "#9CA3AF",
  success: "#2563EB",
  attention: "#EA580C",
  goodChip: "#DBEAFE",
  goodChipText: "#1E3A8A",
  attentionChip: "#FFEDD5",
  attentionChipText: "#9A3412",
  gridHeader: "#FEF1CC",
  gridFilter: "#FFF8E5",
  navMuted: "#C9CED6",
  // Phase 7.20: a rule's rows. Fills with dark text; the word in the cell is the other signal, never the colour alone.
  passFill: "#CDEBD3",
  passText: "#14391F",
  failFill: "#F8CFCB",
  failText: "#6B130C",
  waivedFill: "#FFE08A",
};
export const LIGHT = {
  page: "#F4F5F7",
  panel: "#FFFFFF",
  subtle: "#F7F8FA",
  line: "#E5E7EB",
  muted: "#5B6472",
  text: "#1F2329",
  heading: "#1F2329",
  link: "#1D4ED8",
  dangerText: "#9A3412",
  successText: "#1E3A8A",
};
// Retained for callers that explicitly choose dark; the owner-approved default is light.
export const DARK = {
  page: "#242A33",
  panel: "#2E343E",
  subtle: "#3A414C",
  line: "#647080",
  muted: "#C9CED6",
  text: "#F4F5F7",
  heading: "#FFFFFF",
  link: "#BFDBFE",
  dangerText: "#FED7AA",
  successText: "#BFDBFE",
};
export const CONTRAST_PAIRS = [
  ["light: body on page", "#1F2329", "#F4F5F7"],
  ["light: body on panel", "#1F2329", "#FFFFFF"],
  ["light: body on subtle", "#1F2329", "#F7F8FA"],
  ["light: muted on page", "#5B6472", "#F4F5F7"],
  ["light: muted on panel", "#5B6472", "#FFFFFF"],
  ["light: muted on subtle", "#5B6472", "#F7F8FA"],
  ["light: heading on page", "#1F2329", "#F4F5F7"],
  ["light: heading on panel", "#1F2329", "#FFFFFF"],
  ["light: link on panel", "#1D4ED8", "#FFFFFF"],
  ["light: link on page", "#1D4ED8", "#F4F5F7"],
  ["light: link on subtle", "#1D4ED8", "#F7F8FA"],
  ["light: danger on panel", "#9A3412", "#FFFFFF"],
  ["light: success on panel", "#1E3A8A", "#FFFFFF"],
  ["light: header", "#FFFFFF", "#2E343E"],
  ["light: muted header", "#C9CED6", "#2E343E"],
  ["light: primary", "#1F2329", "#FAB800"],
  ["light: primary hover", "#1F2329", "#E5A800"],
  ["light: danger fill", "#FFFFFF", "#B42318"],
  ["light: success fill", "#FFFFFF", "#2563EB"],
  ["light: blue chip", "#1E3A8A", "#DBEAFE"],
  ["light: orange chip", "#9A3412", "#FFEDD5"],
  ["light: grid header", "#1F2329", "#FEF1CC"],
  ["light: grid filter", "#1F2329", "#FFF8E5"],
  ["light: muted grid", "#5B6472", "#FFF8E5"],
  ["dark: body on page", "#F4F5F7", "#242A33"],
  ["dark: body on panel", "#F4F5F7", "#2E343E"],
  ["dark: body on subtle", "#F4F5F7", "#3A414C"],
  ["dark: muted on page", "#C9CED6", "#242A33"],
  ["dark: muted on panel", "#C9CED6", "#2E343E"],
  ["dark: muted on subtle", "#C9CED6", "#3A414C"],
  ["dark: heading on panel", "#FFFFFF", "#2E343E"],
  ["dark: link on panel", "#BFDBFE", "#2E343E"],
  ["dark: link on page", "#BFDBFE", "#242A33"],
  ["dark: link on subtle", "#BFDBFE", "#3A414C"],
  ["dark: danger text", "#FED7AA", "#2E343E"],
  ["dark: success text", "#BFDBFE", "#2E343E"],
  ["dark: primary", "#1F2329", "#FAB800"],
  ["dark: info fill", "#1F2329", "#DBEAFE"],
  ["dark: success fill", "#FFFFFF", "#2563EB"],
  ["light: rule row passes", "#14391F", "#CDEBD3"],
  ["light: rule row fails", "#6B130C", "#F8CFCB"],
  ["light: rule row waived", "#1F2329", "#FFE08A"],
  ["dark: rule row passes", "#14391F", "#CDEBD3"],
  ["dark: rule row fails", "#6B130C", "#F8CFCB"],
  ["dark: rule row waived", "#1F2329", "#FFE08A"],
] as const;

const BRAND: MantineColorsTuple = [
  "#FFF8E5", "#FEF1CC", "#FDE49A", "#FCD665", "#FBC82F", "#FAC015", "#FAB800", "#E5A800", "#B07C00", "#805A00",
];
export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: 6,
  colors: { brand: BRAND },
  white: SHARED.onFill,
  black: LIGHT.text,
  fontFamily: "Inter, system-ui, sans-serif",
  fontFamilyMonospace: "ui-monospace, Consolas, monospace",
  fontSizes: { xs: "12px", sm: "13px", md: "14px", lg: "15px", xl: "22px" },
  lineHeights: { xs: "1.5", sm: "1.5", md: "1.5", lg: "1.5", xl: "1.35" },
  spacing: { xs: "4px", sm: "8px", md: "12px", lg: "16px", xl: "24px" },
  radius: { xs: "4px", sm: "6px", md: "8px", lg: "8px", xl: "12px" },
  defaultRadius: "sm",
  focusRing: "auto",
  headings: {
    fontWeight: "700",
    sizes: {
      h1: { fontSize: "26px", lineHeight: "1.35" },
      h2: { fontSize: "22px", lineHeight: "1.4" },
      h3: { fontSize: "15px", lineHeight: "1.5" },
      h4: { fontSize: "14px", lineHeight: "1.5" },
    },
  },
  components: {
    Table: { defaultProps: { borderColor: "var(--amz-line)", highlightOnHoverColor: "var(--amz-subtle)" } },
    Badge: { defaultProps: { component: "span" } },
    Button: { defaultProps: { autoContrast: true } },
  },
});
function scheme(c: typeof LIGHT): Record<string, string> {
  return {
    "--mantine-color-body": c.page, "--mantine-color-text": c.text, "--mantine-color-bright": c.heading,
    "--mantine-color-dimmed": c.muted, "--mantine-color-placeholder": c.muted, "--mantine-color-anchor": c.link,
    "--mantine-color-error": c.dangerText, "--mantine-color-success": c.successText,
    "--mantine-color-default": c.panel, "--mantine-color-default-hover": c.subtle,
    "--mantine-color-default-color": c.text, "--mantine-color-default-border": c.line,
    "--mantine-color-disabled": c.subtle, "--mantine-color-disabled-color": c.muted,
    "--mantine-color-disabled-border": SHARED.disabled,
    "--amz-page": c.page, "--amz-panel": c.panel, "--amz-subtle": c.subtle, "--amz-line": c.line,
    "--amz-muted": c.muted, "--amz-text": c.text, "--amz-heading": c.heading, "--amz-link": c.link,
    "--amz-danger-text": c.dangerText, "--amz-success-text": c.successText,
  };
}
export const cssVariablesResolver = (_theme?: unknown): ReturnType<CSSVariablesResolver> => ({
  variables: {
    "--amz-header": SHARED.header, "--amz-on-fill": SHARED.onFill, "--amz-primary": SHARED.primary,
    "--amz-focus": SHARED.focus, "--amz-danger": SHARED.danger, "--amz-warning": SHARED.warning,
    "--amz-info": SHARED.info, "--amz-marker": SHARED.marker, "--amz-on-bright": SHARED.onBright,
    "--amz-disabled": SHARED.disabled, "--amz-success": SHARED.success,
    "--amz-grid-header": SHARED.gridHeader, "--amz-grid-filter": SHARED.gridFilter,
    "--amz-pass": SHARED.passFill, "--amz-on-pass": SHARED.passText, "--amz-fail": SHARED.failFill,
    "--amz-on-fail": SHARED.failText, "--amz-waived": SHARED.waivedFill,
    "--mantine-primary-color-contrast": SHARED.onBright,
    "--mantine-color-brand-contrast": SHARED.onBright,
  },
  light: scheme(LIGHT),
  dark: scheme(DARK),
});

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function formatWhen(value: string | Date, options: { time?: boolean } = {}): string {
  const day = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  const at = day ? new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)))
    : new Date(value);
  if (Number.isNaN(at.getTime())) return String(value);
  const date = `${at.getDate()} ${MONTHS[at.getMonth()]} ${at.getFullYear()}`;
  if (day || options.time === false) return date;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date}, ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}
