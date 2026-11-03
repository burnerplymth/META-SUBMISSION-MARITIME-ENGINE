import React from "react";
import { createRoot } from "react-dom/client";
import "./theme/fonts.css";
import { Harness } from "./harness";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Harness />
  </React.StrictMode>,
);
