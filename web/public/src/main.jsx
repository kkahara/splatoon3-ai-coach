import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App.jsx";
import { LocaleProvider } from "./i18n/LocaleContext.jsx";
import { removeObsoleteStorage } from "./pages/submitForm.js";
import "./styles.css";

removeObsoleteStorage(() => window.localStorage);

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <LocaleProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </LocaleProvider>
  </StrictMode>,
);
