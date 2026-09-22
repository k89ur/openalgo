import { useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { Chart, type ChartType } from "./Chart";
import "./styles.css";

type WatchItem = { symbol: string; price: string; change: string };

const watchlist: WatchItem[] = [
  { symbol: "BHARTIARTL", price: "1,756.90", change: "+1.08%" },
  { symbol: "RELIANCE", price: "1,482.30", change: "+1.21%" },
  { symbol: "TCS", price: "4,021.50", change: "+0.64%" },
  { symbol: "INFY", price: "1,612.80", change: "-0.31%" },
  { symbol: "HDFCBANK", price: "1,008.40", change: "+0.82%" },
  { symbol: "TRENT", price: "5,214.20", change: "+2.14%" },
  { symbol: "ITC", price: "412.75", change: "-0.18%" },
  { symbol: "SBIN", price: "812.60", change: "+0.47%" },
  { symbol: "LT", price: "3,487.10", change: "+1.32%" },
  { symbol: "ICICIBANK", price: "1,214.90", change: "+0.63%" },
  { symbol: "KOTAKBANK", price: "1,981.65", change: "-0.22%" },
  { symbol: "AXISBANK", price: "1,104.30", change: "+0.56%" },
  { symbol: "M&M", price: "2,896.15", change: "+0.56%" },
  { symbol: "TATAMOTORS", price: "987.25", change: "-0.45%" },
  { symbol: "ADANIENT", price: "2,842.60", change: "+1.18%" },
  { symbol: "SUNPHARMA", price: "1,543.20", change: "-0.29%" },
  { symbol: "HINDUNILVR", price: "2,356.70", change: "+0.92%" },
  { symbol: "NIFTY", price: "24,198.85", change: "-0.12%" },
];

function App() {
  const [chartType, setChartType] = useState<ChartType>("candles");
  const [timeframe, setTimeframe] = useState("D");
  const [symbol, setSymbol] = useState("BHARTIARTL");
  const [search, setSearch] = useState("");
  const [watchSearch, setWatchSearch] = useState("");
  const [watchOpen, setWatchOpen] = useState(true);
  const [watchWidth, setWatchWidth] = useState(300);
  const [dark, setDark] = useState(true);

  const selected = useMemo(
    () => watchlist.find((item) => item.symbol === symbol) ?? watchlist[0],
    [symbol],
  );

  const filteredWatchlist = watchlist.filter((item) =>
    item.symbol.toLowerCase().includes(watchSearch.trim().toLowerCase()),
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

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = watchWidth;
    const move = (moveEvent: PointerEvent) => {
      const next = Math.min(460, Math.max(220, startWidth + (startX - moveEvent.clientX)));
      setWatchWidth(next);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  return (
    <main className={`app ${dark ? "theme-dark" : "theme-light"} `}>
      <header className="topbar">
        <div className="brand"><span className="brand-mark">▮▮</span><span>PIPSGOX</span></div>

        <div className="top-search">
          <span className="search-icon">⌕</span>
          <input value={search} placeholder={selected.symbol} aria-label="Search symbol"
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") submitSearch(); }} />
          <span className="search-chevron">⌄</span>
        </div>

        <button className="square-add" aria-label="Add symbol">＋</button>

        <div className="timeframe-row">
          {["1m", "3m", "5m", "15m", "30m", "1h", "D", "W", "M"].map((item) => (
            <button key={item} className={timeframe === item ? "active" : ""} onClick={() => setTimeframe(item)}>
              {item}
            </button>
          ))}
        </div>

        <div className="toolbar-group chart-types">
          {([
            ["candles", "▥"],
            ["bars", "▤"],
            ["line", "⌁"],
          ] as Array<[ChartType, string]>).map(([type, icon]) => (
            <button key={type} className={chartType === type ? "active" : ""} onClick={() => setChartType(type)}>
              {icon}
            </button>
          ))}
        </div>

        <button className="top-action">⌁ Indicators</button>
        <button className="top-action">⌘ Builder</button>
        <button className="top-action compact">◷ Alert</button>
        <button className="top-action compact">‹‹ Replay</button>
        <div className="top-spacer" />
        <button className="icon-button" aria-label="Toggle theme" onClick={() => setDark((value) => !value)}>
          {dark ? "☀" : "☾"}
        </button>
        <button className="icon-button" aria-label="Fullscreen">⛶</button>
        <button className="icon-button" aria-label="Settings">⚙</button>
      </header>

      <section className="workspace">
        <div className="drawing-toolbar" aria-label="Chart tools">
          {["＋", "╱", "☷", "⌁", "⌘", "⌒", "T", "◉", "◇", "⌕", "⌂", "♧", "▣", "◉", "⌫"].map((tool, index) => (
            <button key={index} title="Chart tool">{tool}</button>
          ))}
        </div>

        <section className="chart-workspace">
          <div className="chart-container">
            <Chart chartType={chartType} dark={dark} />

            <div className="chart-overlay">
              <div className="instrument-line">
                <strong>{selected.symbol === "BHARTIARTL" ? "BHARTI AIRTEL LTD" : selected.symbol}</strong>
                <span>{timeframe} · NSE</span>
              </div>
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

          <div className="bottom-toolbar">
            <button>1D</button><button>5D</button><button>1M</button><button>3M</button><button>6M</button><button>YTD</button><button>1Y</button><button>5Y</button><button>All</button>
            <span className="bottom-spacer" />
            <span>auto</span>
          </div>
        </section>

        {watchOpen && (
          <aside className="watchlist" style={{ width: watchWidth }}>
            <div className="watch-resize-handle" onPointerDown={startResize} />
            <div className="watchlist-heading">
              <button className="watch-collapse" onClick={() => setWatchOpen(false)} aria-label="Hide watchlist">›</button>
              <strong>Watchlist</strong>
              <span>⌄</span>
              <div className="watch-heading-spacer" />
              <button aria-label="Add symbol">＋</button>
              <button aria-label="More">⋮</button>
            </div>

            <input className="watch-search" placeholder="Search symbols..." value={watchSearch}
              onChange={(event) => setWatchSearch(event.target.value)} />

            <div className="watch-columns"><span>Symbol</span><span>LTP</span><span>Chg%</span></div>

            <div className="watch-items">
              {filteredWatchlist.map((item) => (
                <button key={item.symbol}
                  className={`watch-row ${item.symbol === symbol ? "selected" : ""}`}
                  onClick={() => selectSymbol(item.symbol)}>
                  <span className="watch-symbol">{item.symbol}</span>
                  <span className="watch-price">{item.price}</span>
                  <span className={`watch-change ${item.change.startsWith("-") ? "negative" : ""}`}>{item.change}</span>
                  <span className="row-more">⋮</span>
                </button>
              ))}
            </div>

            <div className="watchlist-footer"><span>＋ Add Symbol</span><b>{watchlist.length} / 250</b></div>
          </aside>
        )}

        {!watchOpen && (
          <button className="watch-open" onClick={() => setWatchOpen(true)} aria-label="Show watchlist">‹</button>
        )}
      </section>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
