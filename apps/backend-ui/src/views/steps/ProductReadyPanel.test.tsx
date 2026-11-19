import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme } from "../../test/fixtures";
import ProductReadyPanel from "./ProductReadyPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("it links to the product in the consumer app and offers another file", () => {
  renderWithTheme(<ProductReadyPanel feedCode="security_master" />);
  expect(screen.getByText("The product is ready for consumers.")).toBeTruthy();
  expect(screen.getByRole("link", { name: /Open the product/ }).getAttribute("href")).toBe("http://127.0.0.1:5174/#/product/security_master");
  expect(screen.getByRole("link", { name: "Onboard another file" }).getAttribute("href")).toBe("#/onboard");
});
