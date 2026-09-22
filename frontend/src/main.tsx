import { useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { Chart, type ChartType } from "./Chart";
import "./styles.css";

type WatchItem = { symbol: string; price: string; change: string };

const watchlist: WatchItem[] = [
  { symbol: "RELIANCE", price: "1,482.30", change: "+1.21%" },
  { symbol: "TCS", price: "4,021.50", change: "+0.64%" },
  { symbol: "INFY", price: "1,612.80", change: "-0.31%" },
  { symbol: "HDFCBANK", price: "1,008.40", change: "+0.82%" },
  { symbol: "TRENT", price: "5,214.20", change: "+2.14%" },
  { symbol: "ITC", price: "412.75", change: "-0.18%" },
  { symbol: "SBIN", price: "812.60", change: "+0.47%" },
  { symbol: "BHARTIARTL", price: "1,756.90", change: "+1.08%" },
  { symbol: "LT", price: "3,487.10", change: "+1.32%" },
  { symbol: "ICICIBANK", price: "1,214.90", change: "+0.63%" },
  { symbol: "KOTAKBANK", price: "1,981.65", change: "-0.22%" },
  { symbol: "AXISBANK", price: "1,104.30", change: "+0.56%" },
  { symbol: "M&M", price: "2,896.15", change: "+0.56%" },
  { symbol: "TATAMOTORS", price: "987.25", change: "-0.45%" },
];

function App() {
  const [chartType, setChartType] = useState<ChartType>("candles");
  const [timeframe, setTimeframe] = useState("1D");
  const [symbol, setSymbol] = useState("BHARTIARTL");
  const [search, setSearch] = useState("BHARTIARTL");

  const selected = useMemo(
    () => watchlist.find((item) => item.symbol === symbol) ?? watchlist[0],
    [symbol],
  );

  const price = Number(selected.price.replace(/,/g, ""));
  const changePercent = Number(selected.change.replace("%", ""));
  const changeAmount = price * (changePercent / 100);
  const open = price - changeAmount * 0.35;
  const high = Math.max(price, open) + Math.abs(changeAmount) * 0.22 + 2.15;
  const low = Math.min(price, open) - Math.abs(changeAmount) * 0.55 - 2.4;

  const selectSymbol = (next: string) => {
    setSymbol(next);
    setSearch(next);
  };

  const submitSearch = () => {
    const query = search.trim().toUpperCase();
    const match = watchlist.find((item) => item.symbol === query);
    if (match) selectSymbol(match.symbol);
  };

  return (
    <main className="app">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">▮▮▮</span><span>ChartLab</span></div>

        <div className="top-search">
          <span className="search-icon">⌕</span>
          <input value={search} aria-label="Search symbol"
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") submitSearch(); }} />
          <span className="search-chevron">⌄</span>
        </div>

        <div className="toolbar-group">
          {["1D", "1W", "1M"].map((item) => (
            <button key={item} className={`toolbar-button ${timeframe === item ? "active" : ""}`}
              onClick={() => setTimeframe(item)}>{item}</button>
          ))}
        </div>

        <div className="toolbar-group chart-types">
          {([
            ["candles", "▥", "Candles"],
            ["bars", "▤", "Bars"],
            ["line", "⌁", "Line"],
          ]).map(([type, icon, label]) => (
            <button key={type}
              className={`toolbar-button chart-type-button ${chartType === type ? "active" : ""}`}
              onClick={() => setChartType(type)}><span>{icon}</span>{label}</button>
          ))}
        </div>

        <button className="top-action"><span>⌁</span> Indicators</button>
        <button className="top-action"><span>⌘</span> Builder</button>
        <div className="top-spacer" />
        <button className="icon-button" aria-label="Settings">⚙</button>
        <button className="icon-button" aria-label="Fullscreen">⛶</button>
        <span className="data-status"><i /> Sample data</span>
      </header>

      <section className="workspace">
        <aside className="watchlist">
          <div className="watchlist-heading">
            <div><strong>Watchlist</strong><span>{watchlist.length} / 250</span></div>
            <button aria-label="Add symbol">＋</button>
          </div>
          <input className="watch-search" placeholder="Search symbols..." />
          <div className="watch-items">
            {watchlist.map((item) => (
              <button key={item.symbol}
                className={`watch-row ${item.symbol === symbol ? "selected" : ""}`}
                onClick={() => selectSymbol(item.symbol)}>
                <span className="watch-symbol">{item.symbol}</span>
                <span className="watch-price">{item.price}</span>
                <span className={`watch-change ${item.change.startsWith("-") ? "negative" : ""}`}>{item.change}</span>
              </button>
            ))}
          </div>
          <div className="watchlist-footer">＋ Add Symbol</div>
        </aside>

        <section className="chart-workspace">
          <div className="chart-container">
            <Chart chartType={chartType} />
            <div className="chart-overlay">
              <div className="instrument-line"><strong>{selected.symbol}</strong><span>NSE</span></div>
              <div className="ohlc-line">
                <span>O <b>{open.toFixed(2)}</b></span>
                <span>H <b>{high.toFixed(2)}</b></span>
                <span>L <b>{low.toFixed(2)}</b></span>
                <span>C <b>{price.toFixed(2)}</b></span>
                <span className={changePercent < 0 ? "negative" : "positive"}>
                  {changeAmount >= 0 ? "+" : ""}{changeAmount.toFixed(2)} ({selected.change})
                </span>
              </div>
              <div className="plotted-indicators">
                <span className="ma20">MA 20 <b>1,701.42</b></span>
                <span className="ma50">MA 50 <b>1,658.27</b></span>
                <span className="ma200">MA 200 <b>1,584.61</b></span>
              </div>
            </div>
            <div className="volume-label"><strong>Volume</strong><span>2.34M</span></div>
          </div>
        </section>
      </section>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
