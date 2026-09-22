import { useState } from "react";
import ReactDOM from "react-dom/client";
import { Chart, type ChartType } from "./Chart";
import "./styles.css";

const watchlist = [
  { symbol: "RELIANCE", price: "1,482.30", change: "+1.21%" },
  { symbol: "TCS", price: "4,021.50", change: "+0.64%" },
  { symbol: "INFY", price: "1,612.80", change: "-0.31%" },
  { symbol: "HDFCBANK", price: "1,008.40", change: "+0.82%" },
  { symbol: "TRENT", price: "5,214.20", change: "+2.14%" },
  { symbol: "ITC", price: "412.75", change: "-0.18%" },
  { symbol: "SBIN", price: "812.60", change: "+0.47%" },
  { symbol: "BHARTIARTL", price: "1,756.90", change: "+1.08%" },
];

function App() {
  const [chartType, setChartType] = useState<ChartType>("candles");
  const [symbol, setSymbol] = useState("RELIANCE");

  const selected = watchlist.find((item) => item.symbol === symbol) ?? watchlist[0];

  return (
    <main className="app">
      <header className="topbar">
        <div className="brand">ChartLab</div>

        <div className="symbol-search">
          <span className="search-icon">⌕</span>
          <span>{selected.symbol}</span>
          <span className="exchange">NSE</span>
        </div>

        <div className="toolbar-group">
          <button className="toolbar-button active">1D</button>
          <button className="toolbar-button">1W</button>
          <button className="toolbar-button">1M</button>
        </div>

        <div className="toolbar-group chart-types">
          <button
            className={`toolbar-button ${chartType === "candles" ? "active" : ""}`}
            onClick={() => setChartType("candles")}
          >
            Candles
          </button>
          <button
            className={`toolbar-button ${chartType === "bars" ? "active" : ""}`}
            onClick={() => setChartType("bars")}
          >
            Bars
          </button>
          <button
            className={`toolbar-button ${chartType === "line" ? "active" : ""}`}
            onClick={() => setChartType("line")}
          >
            Line
          </button>
        </div>

        <div className="top-spacer" />
        <span className="data-status"><i /> Sample data</span>
      </header>

      <section className="workspace">
        <aside className="watchlist">
          <div className="watchlist-heading">
            <div>
              <strong>Watchlist</strong>
              <span>8 / 250</span>
            </div>
            <button aria-label="Add symbol">＋</button>
          </div>

          <input className="watch-search" placeholder="Search symbols" />

          <div className="watch-items">
            {watchlist.map((item) => (
              <button
                key={item.symbol}
                className={`watch-row ${item.symbol === symbol ? "selected" : ""}`}
                onClick={() => setSymbol(item.symbol)}
              >
                <span className="watch-symbol">{item.symbol}</span>
                <span className="watch-price">{item.price}</span>
                <span className={`watch-change ${item.change.startsWith("-") ? "negative" : ""}`}>
                  {item.change}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="chart-workspace">
          <div className="chart-header">
            <div>
              <div className="instrument-line">
                <strong>{selected.symbol}</strong>
                <span>NSE</span>
              </div>
              <div className="quote-line">
                <strong>₹{selected.price}</strong>
                <span className={selected.change.startsWith("-") ? "negative" : ""}>
                  {selected.change}
                </span>
              </div>
            </div>

            <div className="chart-actions">
              <button>Indicators</button>
              <button>Builder</button>
              <button aria-label="Settings">⚙</button>
            </div>
          </div>

          <div className="chart-container">
            <Chart chartType={chartType} />
          </div>

          <div className="indicator-strip">
            <span className="indicator-chip">Volume <b>×</b></span>
            <button className="add-indicator">＋ Add indicator</button>
          </div>
        </section>
      </section>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
