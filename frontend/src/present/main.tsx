import React from "react";
import ReactDOM from "react-dom/client";

import { PresentPage } from "./PresentPage";
import "../index.css";

const session = Number(
  new URLSearchParams(window.location.search).get("session"),
);

ReactDOM.createRoot(document.getElementById("present-root")!).render(
  <React.StrictMode>
    {Number.isFinite(session) && session > 0 ? (
      <PresentPage sessionId={session} />
    ) : (
      <div style={{ color: "#fff", fontFamily: "sans-serif", padding: 24 }}>
        Missing or invalid <code>?session</code> parameter.
      </div>
    )}
  </React.StrictMode>,
);
