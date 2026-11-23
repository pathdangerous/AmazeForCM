import "@mantine/core/styles.css";
import "../../ui-shared/phase711.css";
import { MantineProvider } from "@mantine/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { cssVariablesResolver, theme } from "./theme";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

// The one look of both UIs (phase 7.5 plan, assumptions 2 to 4): light or dark as the system says.
createRoot(root).render(
  <StrictMode>
    <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} defaultColorScheme="light" forceColorScheme="light">
      <App />
    </MantineProvider>
  </StrictMode>,
);
