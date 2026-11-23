import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "vitest";
import { CONTRAST_PAIRS, cssVariablesResolver, LIGHT, SHARED, theme } from "./theme";

function luminance(hex: string) {
  const parts = hex.replace("#", "").match(/../g)!.map((part) => {
    const c = parseInt(part, 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return parts[0] * 0.2126 + parts[1] * 0.7152 + parts[2] * 0.0722;
}

test("both Mantine themes remain byte-identical", () => {
  expect(readFileSync(resolve(process.cwd(), "src/theme.ts"), "utf8")).toBe(
    readFileSync(resolve(process.cwd(), "../consumer-ui/src/theme.ts"), "utf8"),
  );
});

test("approved phase 7.11 named tokens replace the old navy/violet identity", () => {
  expect(SHARED.header.toUpperCase()).toBe("#2E343E");
  expect(SHARED.primary.toUpperCase()).toBe("#FAB800");
  expect(SHARED.onBright.toUpperCase()).toBe("#1F2329");
  expect(LIGHT.page.toUpperCase()).toBe("#F4F5F7");
  expect(LIGHT.panel.toUpperCase()).toBe("#FFFFFF");
  expect(LIGHT.line.toUpperCase()).toBe("#E5E7EB");
  expect(LIGHT.text.toUpperCase()).toBe("#1F2329");
  expect(LIGHT.muted.toUpperCase()).toBe("#5B6472");
  expect(theme.fontFamily).toMatch(/Inter/);
});

test("grid fills are named CSS tokens and amber buttons use dark text, not white", () => {
  const resolved = cssVariablesResolver(theme);
  const vars = { ...resolved.variables, ...resolved.light };
  expect(vars["--amz-grid-header"]?.toUpperCase()).toBe("#FEF1CC");
  expect(vars["--amz-grid-filter"]?.toUpperCase()).toBe("#FFF8E5");
  expect(vars["--mantine-primary-color-contrast"]?.toUpperCase()).toBe("#1F2329");
});

test("every declared text/background combination meets WCAG AA normal-text contrast", () => {
  expect(CONTRAST_PAIRS.length).toBeGreaterThan(10);
  for (const [name, fg, bg] of CONTRAST_PAIRS) {
    const a = luminance(fg);
    const b = luminance(bg);
    expect((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), name).toBeGreaterThanOrEqual(4.5);
  }
});
