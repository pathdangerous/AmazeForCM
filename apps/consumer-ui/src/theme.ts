// The one look of both UIs (phase 7.5 plan, assumptions 2 to 4). This file is byte-identical in backend-ui and
// consumer-ui; tests/test_ui_shared_look.py checks that, the pinned versions, that main.tsx uses it, and that every
// pair in CONTRAST_PAIRS meets 4.5:1. Change it in both apps at once.
//
// Colours: the Hexaware palette (LIGHT, SHARED). Outside it, and marked: success (#1A7F37), every DARK value
// (derived at task 0, decision 3) and the tints of BRAND other than shade 6 (Mantine needs ten shades).
// Rules of use: content sits on panels; warning, info and the current-state marker are fills with ON_BRIGHT text,
// never text; the focus colour is never text; disabled is a border, never text (disabled text is muted on subtle).
import { createTheme, type CSSVariablesResolver, type MantineColorsTuple } from "@mantine/core";

export const SHARED = {
  header: "#07125E", // header bar, both schemes
  onFill: "#FFFFFF", // text on header, primary, danger and success fills
  primary: "#3C2CDA", // primary fill, both schemes
  primaryHover: "#3123B8", // derived (BRAND shade 7)
  focus: "#1D86FF", // focus ring and icons, never text
  danger: "#DA2D2C", // danger fill
  warning: "#EA9D00", // fill only
  info: "#14CBDE", // fill only
  marker: "#F4CB4E", // current-state marker, fill only
  onBright: "#040D43", // text on warning, info and marker fills
  disabled: "#8088A7", // disabled controls: border only
  success: "#1A7F37", // success fill; outside the Hexaware palette
};

export const LIGHT = {
  page: "#F8F8F9",
  panel: "#FFFFFF",
  subtle: "#EEEFF2",
  line: "#CBD0E5",
  muted: "#535983", // strong lines and muted text
  text: "#040D43",
  heading: "#07125E",
  link: "#3C2CDA",
  dangerText: "#DA2D2C", // on panels only
  successText: "#1A7F37", // outside the Hexaware palette
};

// Derived at task 0 (decision 3): every value here is outside the Hexaware palette.
export const DARK = {
  page: "#0B1030",
  panel: "#131A3D",
  subtle: "#1D2550",
  line: "#323C70",
  muted: "#A9AECF",
  text: "#E6E8F5",
  heading: "#FFFFFF",
  link: "#ABA3FF",
  dangerText: "#FF8F8E",
  successText: "#5CC97B",
};

// [scheme: what, text, background]. Every pair the look puts text on; the pytest checks each at 4.5:1.
export const CONTRAST_PAIRS = [
  ["light: body text on page", "#040D43", "#F8F8F9"],
  ["light: body text on panel", "#040D43", "#FFFFFF"],
  ["light: body text on subtle", "#040D43", "#EEEFF2"],
  ["light: muted on page", "#535983", "#F8F8F9"],
  ["light: muted on panel", "#535983", "#FFFFFF"],
  ["light: muted on subtle (also disabled text)", "#535983", "#EEEFF2"],
  ["light: heading on page", "#07125E", "#F8F8F9"],
  ["light: heading on panel", "#07125E", "#FFFFFF"],
  ["light: link on page", "#3C2CDA", "#F8F8F9"],
  ["light: link on panel", "#3C2CDA", "#FFFFFF"],
  ["light: link on subtle", "#3C2CDA", "#EEEFF2"],
  ["light: danger text on panel", "#DA2D2C", "#FFFFFF"],
  ["light: success text on panel", "#1A7F37", "#FFFFFF"],
  ["light: success text on page", "#1A7F37", "#F8F8F9"],
  ["light: white on header", "#FFFFFF", "#07125E"],
  ["light: white on primary", "#FFFFFF", "#3C2CDA"],
  ["light: white on primary hover", "#FFFFFF", "#3123B8"],
  ["light: white on danger", "#FFFFFF", "#DA2D2C"],
  ["light: white on success", "#FFFFFF", "#1A7F37"],
  ["light: dark text on warning", "#040D43", "#EA9D00"],
  ["light: dark text on info", "#040D43", "#14CBDE"],
  ["light: dark text on marker", "#040D43", "#F4CB4E"],
  ["dark: body text on page", "#E6E8F5", "#0B1030"],
  ["dark: body text on panel", "#E6E8F5", "#131A3D"],
  ["dark: body text on subtle", "#E6E8F5", "#1D2550"],
  ["dark: muted on page", "#A9AECF", "#0B1030"],
  ["dark: muted on panel", "#A9AECF", "#131A3D"],
  ["dark: muted on subtle (also disabled text)", "#A9AECF", "#1D2550"],
  ["dark: heading on page", "#FFFFFF", "#0B1030"],
  ["dark: heading on panel", "#FFFFFF", "#131A3D"],
  ["dark: link on page", "#ABA3FF", "#0B1030"],
  ["dark: link on panel", "#ABA3FF", "#131A3D"],
  ["dark: link on subtle", "#ABA3FF", "#1D2550"],
  ["dark: danger text on page", "#FF8F8E", "#0B1030"],
  ["dark: danger text on panel", "#FF8F8E", "#131A3D"],
  ["dark: success text on page", "#5CC97B", "#0B1030"],
  ["dark: success text on panel", "#5CC97B", "#131A3D"],
  ["dark: white on header", "#FFFFFF", "#07125E"],
  ["dark: white on primary", "#FFFFFF", "#3C2CDA"],
  ["dark: white on primary hover", "#FFFFFF", "#3123B8"],
  ["dark: white on danger", "#FFFFFF", "#DA2D2C"],
  ["dark: white on success", "#FFFFFF", "#1A7F37"],
  ["dark: dark text on warning", "#040D43", "#EA9D00"],
  ["dark: dark text on info", "#040D43", "#14CBDE"],
  ["dark: dark text on marker", "#040D43", "#F4CB4E"],
] as const;

// Mantine's primary colour: shade 6 is the palette's primary; the other shades are derived tints and shades.
const BRAND: MantineColorsTuple = [
  "#EEECFC", "#DCD8F9", "#BBB3F2", "#9A8EEB", "#7A6AE4", "#5B4BDF", "#3C2CDA", "#3123B8", "#271C93", "#1D156E",
];

// R1's type scale (12 / 14 body and tables / 16 section / 20 page / 24 app title), radius 6, 4 px grid.
export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: 6,
  colors: { brand: BRAND },
  white: SHARED.onFill,
  black: LIGHT.text,
  fontFamily: "system-ui, 'Segoe UI', sans-serif",
  fontFamilyMonospace: "ui-monospace, Consolas, monospace",
  fontSizes: { xs: "12px", sm: "14px", md: "14px", lg: "16px", xl: "20px" },
  lineHeights: { xs: "1.45", sm: "1.45", md: "1.45", lg: "1.45", xl: "1.45" },
  spacing: { xs: "4px", sm: "8px", md: "12px", lg: "16px", xl: "24px" },
  radius: { xs: "4px", sm: "6px", md: "6px", lg: "8px", xl: "12px" },
  defaultRadius: "sm",
  focusRing: "auto",
  headings: {
    fontWeight: "600",
    sizes: {
      h1: { fontSize: "24px", lineHeight: "1.45" },
      h2: { fontSize: "20px", lineHeight: "1.45" },
      h3: { fontSize: "16px", lineHeight: "1.45" },
      h4: { fontSize: "14px", lineHeight: "1.45" },
    },
  },
  components: {
    Table: { defaultProps: { borderColor: "var(--amz-line)", highlightOnHoverColor: "var(--amz-subtle)" } },
  },
});

// The palette as CSS variables: Mantine's own (body, text, dimmed, anchor, default, disabled, error) and --amz-*
// for the rules in index.html. Light and dark differ only where DARK says so.
function scheme(c: typeof LIGHT): Record<string, string> {
  return {
    "--mantine-color-body": c.page,
    "--mantine-color-text": c.text,
    "--mantine-color-bright": c.heading,
    "--mantine-color-dimmed": c.muted,
    "--mantine-color-placeholder": c.muted,
    "--mantine-color-anchor": c.link,
    "--mantine-color-error": c.dangerText,
    "--mantine-color-success": c.successText,
    "--mantine-color-default": c.panel,
    "--mantine-color-default-hover": c.subtle,
    "--mantine-color-default-color": c.text,
    "--mantine-color-default-border": c.line,
    "--mantine-color-disabled": c.subtle,
    "--mantine-color-disabled-color": c.muted,
    "--mantine-color-disabled-border": SHARED.disabled,
    "--amz-page": c.page,
    "--amz-panel": c.panel,
    "--amz-subtle": c.subtle,
    "--amz-line": c.line,
    "--amz-muted": c.muted,
    "--amz-text": c.text,
    "--amz-heading": c.heading,
    "--amz-link": c.link,
    "--amz-danger-text": c.dangerText,
    "--amz-success-text": c.successText,
  };
}

export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {
    "--amz-header": SHARED.header,
    "--amz-on-fill": SHARED.onFill,
    "--amz-primary": SHARED.primary,
    "--amz-focus": SHARED.focus,
    "--amz-danger": SHARED.danger,
    "--amz-warning": SHARED.warning,
    "--amz-info": SHARED.info,
    "--amz-marker": SHARED.marker,
    "--amz-on-bright": SHARED.onBright,
    "--amz-disabled": SHARED.disabled,
    "--amz-success": SHARED.success,
    "--mantine-primary-color-contrast": SHARED.onFill,
  },
  light: scheme(LIGHT),
  dark: scheme(DARK),
});
