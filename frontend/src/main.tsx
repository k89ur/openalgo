import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";

function App() {
  return (
    <main className="app">
      <header className="topbar">
        <strong>ChartLab</strong>
        <span>Core workspace initialized</span>
      </header>
      <section className="workspace">
        <aside>Watchlist</aside>
        <div className="chart-placeholder">
          <h1>Chart workspace</h1>
          <p>Chart engine and market-data pipeline come next.</p>
        </div>
      </section>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>
);
