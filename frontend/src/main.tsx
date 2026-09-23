import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import ReactDOM from "react-dom/client";
import { Chart, type ChartType, type Timeframe } from "./Chart";
import "./styles.css";

type WatchItem = { symbol: string; price: string; change: string };
export type ChartTheme = "pipsgox" | "classic" | "light";

type ChartSettings = {
  theme: ChartTheme;
  showGrid: boolean;
  showCrosshair: boolean;
  showVolume: boolean;
};

const DEFAULT_CHART_SETTINGS: ChartSettings = {
  theme: "pipsgox",
  showGrid: true,
  showCrosshair: true,
  showVolume: true,
};

function loadChartSettings(): ChartSettings {
  try {
    const raw = localStorage.getItem("pipsgox-chart-settings");
    if (!raw) return DEFAULT_CHART_SETTINGS;
    return { ...DEFAULT_CHART_SETTINGS, ...(JSON.parse(raw) as Partial<ChartSettings>) };
  } catch {
    return DEFAULT_CHART_SETTINGS;
  }
}

const MAX_WATCHLIST_SIZE = 1000;
const WATCHLIST_STORAGE_KEY = "pipsgox-watchlist";

const DEFAULT_WATCHLIST: WatchItem[] = [
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

function loadWatchlist(): WatchItem[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY);
    if (!raw) return DEFAULT_WATCHLIST;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return DEFAULT_WATCHLIST;

    const symbols = parsed
      .map((item) => (typeof item === "string" ? item : (item as Partial<WatchItem>)?.symbol))
      .map((item) => String(item ?? "").trim().toUpperCase())
      .filter(Boolean);

    const unique = [...new Set(symbols)].slice(0, MAX_WATCHLIST_SIZE);
    return unique.map((item) => ({ symbol: item, price: "—", change: "—" }));
  } catch {
    return DEFAULT_WATCHLIST;
  }
}

function normalizeImportedSymbol(value: string): string {
  return value
    .replace(/^\uFEFF/, "")
    .replace(/^["']|["']$/g, "")
    .trim()
    .toUpperCase();
}

function parseWatchlistImport(text: string): string[] {
  const rows = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const symbols: string[] = [];

  for (const row of rows) {
    const cells = row.split(/[,;\t]/).map((cell) => normalizeImportedSymbol(cell));
    if (!cells.length) continue;

    const header = cells[0].replace(/\s+/g, "").toLowerCase();
    if (header === "symbol" || header === "ticker" || header === "tradingsymbol") continue;

    const candidate = cells.find((cell) => cell && !/^(SYMBOL|TICKER|TRADINGSYMBOL)$/i.test(cell));
    if (candidate) symbols.push(candidate);
  }

  return [...new Set(symbols)].slice(0, MAX_WATCHLIST_SIZE);
}

const indicators = ["MA 20", "MA 50", "MA 200", "Volume"];

function App() {
  const [chartType, setChartType] = useState<ChartType>("candles");
  const [timeframe, setTimeframe] = useState<Timeframe>("D");
  const [watchlist, setWatchlist] = useState<WatchItem[]>(loadWatchlist);
  const [symbol, setSymbol] = useState(() => loadWatchlist()[0]?.symbol ?? "BHARTIARTL");
  const [search, setSearch] = useState("");
  const [watchSearch, setWatchSearch] = useState("");
  const [watchOpen, setWatchOpen] = useState(true);
  const [watchWidth, setWatchWidth] = useState(315);
  const [dark, setDark] = useState(true);
  const [panel, setPanel] = useState<"indicators" | "pipscript" | "settings" | null>(null);
  const [quote, setQuote] = useState<{
    last: number; change: number; change_percent: number;
    open?: number; high?: number; low?: number; volume?: number;
    bid?: number; ask?: number;
  } | null>(null);
  const [liveQuotes, setLiveQuotes] = useState<Record<string, {
    last: number; change: number; change_percent: number;
  }>>({});
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState(false);
  const [chartSettings, setChartSettings] = useState<ChartSettings>(loadChartSettings);
  const [watchImportMessage, setWatchImportMessage] = useState("");
  const watchImportRef = useRef<HTMLInputElement>(null);
  const updateChartSettings = (patch: Partial<ChartSettings>) => {
    setChartSettings((current) => {
      const next = { ...current, ...patch };
      localStorage.setItem("pipsgox-chart-settings", JSON.stringify(next));
      return next;
    });
  };

  useEffect(() => {
    localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist));
  }, [watchlist]);

  const exportWatchlist = () => {
    const csv = ["symbol", ...watchlist.map((item) => item.symbol)].join("\n") + "\n";
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "pipsgox-watchlist.csv";
    link.click();
    URL.revokeObjectURL(url);
    setWatchImportMessage(`Exported ${watchlist.length} symbols`);
    window.setTimeout(() => setWatchImportMessage(""), 2500);
  };

  const importWatchlist = async (file: File) => {
    try {
      const text = await file.text();
      const imported = parseWatchlistImport(text);

      if (!imported.length) {
        setWatchImportMessage("No symbols found in the file");
        return;
      }

      const limited = imported.slice(0, MAX_WATCHLIST_SIZE);
      const next = limited.map((item) => ({ symbol: item, price: "—", change: "—" }));
      setWatchlist(next);

      if (!next.some((item) => item.symbol === symbol)) {
        selectSymbol(next[0].symbol);
      }

      setWatchImportMessage(
        imported.length > MAX_WATCHLIST_SIZE
          ? `Imported ${MAX_WATCHLIST_SIZE} symbols (1000 maximum)`
          : `Imported ${next.length} symbols`,
      );
    } catch {
      setWatchImportMessage("Could not read the watchlist file");
    } finally {
      if (watchImportRef.current) watchImportRef.current.value = "";
      window.setTimeout(() => setWatchImportMessage(""), 3500);
    }
  };

  const addWatchSymbol = () => {
    const value = window.prompt("Enter symbol to add");
    const nextSymbol = normalizeImportedSymbol(value ?? "");
    if (!nextSymbol) return;

    if (watchlist.some((item) => item.symbol === nextSymbol)) {
      selectSymbol(nextSymbol);
      setWatchImportMessage(`${nextSymbol} is already in the watchlist`);
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    if (watchlist.length >= MAX_WATCHLIST_SIZE) {
      setWatchImportMessage("Watchlist limit reached: 1000 symbols");
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    setWatchlist((current) => [...current, { symbol: nextSymbol, price: "—", change: "—" }]);
    selectSymbol(nextSymbol);
    setWatchImportMessage(`Added ${nextSymbol}`);
    window.setTimeout(() => setWatchImportMessage(""), 2500);
  };

  const [pipscript, setPipscript] = useState(
    "def calculate(data):\n    close = data.close\n    ma20 = close.sma(20)\n    return {\n        \"MA20\": ma20\n    }",
  );

  useEffect(() => {
    let cancelled = false;
    let firstLoad = true;

    const loadQuote = async () => {
      if (firstLoad) {
        setQuoteLoading(true);
        setQuoteError(false);
      }

      try {
        const response = await fetch(
          "/api/quote?symbol=" + encodeURIComponent(symbol),
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("quote request failed");

        const data = await response.json() as {
          last: number; change: number; change_percent: number;
          open?: number; high?: number; low?: number; volume?: number;
          bid?: number; ask?: number;
        };

        if (!cancelled) {
          setQuote(data);
          setQuoteError(false);
        }
      } catch {
        if (!cancelled) setQuoteError(true);
      } finally {
        if (!cancelled && firstLoad) setQuoteLoading(false);
        firstLoad = false;
      }
    };

    void loadQuote();
    const timer = window.setInterval(() => void loadQuote(), 5000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [symbol]);

  useEffect(() => {
    let cancelled = false;
    const symbols = watchlist.map((item) => item.symbol).join(",");
    if (!symbols) {
      setLiveQuotes({});
      return () => { cancelled = true; };
    }
    fetch("/api/quotes?symbols=" + encodeURIComponent(symbols))
      .then((response) => {
        if (!response.ok) throw new Error("quotes request failed");
        return response.json() as Promise<Array<{ symbol: string; last: number; change: number; change_percent: number }>>;
      })
      .then((data) => {
        if (cancelled) return;
        const next: Record<string, { last: number; change: number; change_percent: number }> = {};
        data.forEach((item) => { next[item.symbol] = item; });
        setLiveQuotes(next);
      })
      .catch(() => { if (!cancelled) setLiveQuotes({}); });
    return () => { cancelled = true; };
  }, [watchlist]);

  useEffect(() => {
    let cancelled = false;

    const refreshWatchlist = async () => {
      try {
        const symbols = watchlist.map((item) => item.symbol).join(",");
        const response = await fetch(
          "/api/quotes?symbols=" + encodeURIComponent(symbols),
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("watchlist quotes request failed");

        const data = await response.json() as Array<{
          symbol: string; last: number; change: number; change_percent: number;
        }>;

        if (cancelled) return;
        const next: Record<string, { last: number; change: number; change_percent: number }> = {};
        data.forEach((item) => { next[item.symbol] = item; });
        setLiveQuotes(next);
      } catch {
        // Keep the last successful watchlist snapshot visible.
      }
    };

    const timer = window.setInterval(() => void refreshWatchlist(), 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [watchlist]);

  const selected = useMemo(
    () => watchlist.find((item) => item.symbol === symbol) ?? watchlist[0],
    [symbol, watchlist],
  );

  const filteredWatchlist = watchlist.filter((item) =>
    item.symbol.toLowerCase().includes(watchSearch.trim().toLowerCase()),
  );

  const liveSelected = liveQuotes[selected.symbol];
  const hasLiveData = Boolean(quote || liveSelected);
  const price = quote?.last ?? liveSelected?.last ?? 0;
  const changePercent = quote?.change_percent ?? liveSelected?.change_percent ?? 0;
  const changeAmount = quote?.change ?? liveSelected?.change ?? 0;
  const open = quote?.open;
  const high = quote?.high;
  const low = quote?.low;
  const volume = quote?.volume;

  const selectSymbol = (next: string) => {
    setSymbol(next);
    setSearch(next);
  };

  const submitSearch = () => {
    const query = search.trim().toUpperCase();
    const match = watchlist.find((item) => item.symbol === query);
    if (match) selectSymbol(match.symbol);
  };

  const startResize = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = watchWidth;
    const move = (moveEvent: PointerEvent) => {
      setWatchWidth(Math.min(480, Math.max(240, startWidth + startX - moveEvent.clientX)));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  return (
    <main className={`app ${dark ? "theme-dark" : "theme-light"}`}>
      <div className="menu-bar">
        <div className="menu-left">
          <span className="brand">PIPSGOX</span>
          <button>File</button>
          <button>Account</button>
          <button>Help</button>
        </div>
        <div className="menu-center">PIPSGOX WEB TERMINAL</div>
        <div className="menu-right"><span className="status-dot" /> Data: FYERS API V3</div>
      </div>

      <header className="topbar">
        <button className="top-command">NEW WINDOW</button>
        <button className="top-command">MARKET</button>
        <button className="top-command">WATCHLIST</button>
        <button className="top-command">ORDERS</button>

        <div className="top-search">
          <input value={search} placeholder={selected.symbol} aria-label="Search symbol"
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") submitSearch(); }} />
          <button onClick={submitSearch}>GO</button>
        </div>

        <div className="top-spacer" />

        <button className="top-command" onClick={() => setDark((value) => !value)}>
          {dark ? "LIGHT" : "DARK"}
        </button>
        <button className="top-command" onClick={() => setPanel("settings")}>SETTINGS</button>
      </header>

      <section className="workspace">
        <section className="chart-workspace">
          <div className="instrument-bar">
            <div className="instrument-main">
              <strong>{selected.symbol === "BHARTIARTL" ? "BHARTI AIRTEL LTD" : selected.symbol}</strong>
              <span>NSE</span>
              <b className={changePercent < 0 ? "negative" : "positive"}>{quoteLoading ? "..." : hasLiveData ? price.toFixed(2) : "—"}</b>
              <span className={changePercent < 0 ? "negative" : "positive"}>{quoteLoading ? "..." : hasLiveData ? `${changeAmount >= 0 ? "+" : ""}${changeAmount.toFixed(2)} (${changePercent.toFixed(2)}%)` : "No data"}</span>
            </div>

            <div className="chart-controls">
              {(["1m", "3m", "5m", "15m", "30m", "1h", "D", "W", "M"] as Timeframe[]).map((item) => (
                <button key={item} className={timeframe === item ? "active" : ""} onClick={() => setTimeframe(item)}>
                  {item}
                </button>
              ))}
              <span className="control-divider" />
              {([
                ["candles", "CANDLE"],
                ["bars", "BAR"],
                ["line", "LINE"],
              ] as Array<[ChartType, string]>).map(([type, label]) => (
                <button key={type} className={chartType === type ? "active" : ""} onClick={() => setChartType(type)}>
                  {label}
                </button>
              ))}
              <span className="control-divider" />
              <button onClick={() => setPanel("indicators")}>INDICATORS</button>
              <button onClick={() => setPanel("pipscript")}>PIPSCRIPT</button>
            </div>
          </div>

          <div className="chart-container">
            <Chart
              chartType={chartType}
              dark={dark}
              symbol={symbol}
              timeframe={timeframe}
              chartTheme={chartSettings.theme}
              showGrid={chartSettings.showGrid}
              showCrosshair={chartSettings.showCrosshair}
              showVolume={chartSettings.showVolume}
            />

            <div className="quote-panel">
              <div className="quote-title">{selected.symbol}</div>
              <div className="quote-price">{quote ? quote.last.toFixed(2) : liveSelected ? liveSelected.last.toFixed(2) : "—"}</div>
              <div className={`quote-change ${changePercent < 0 ? "negative" : "positive"}`}>
                {hasLiveData ? `${changeAmount >= 0 ? "+" : ""}${changeAmount.toFixed(2)} (${changePercent.toFixed(2)}%)` : "No live quote"}
              </div>
              <dl>
                <div><dt>Bid</dt><dd>{quote?.bid != null ? quote.bid.toFixed(2) : "—"}</dd></div>
                <div><dt>Ask</dt><dd>{quote?.ask != null ? quote.ask.toFixed(2) : "—"}</dd></div>
                <div><dt>Open</dt><dd>{open != null ? open.toFixed(2) : "—"}</dd></div>
                <div><dt>High</dt><dd>{high != null ? high.toFixed(2) : "—"}</dd></div>
                <div><dt>Low</dt><dd>{low != null ? low.toFixed(2) : "—"}</dd></div>
                <div><dt>Volume</dt><dd>{volume != null ? volume.toLocaleString() : "—"}</dd></div>
              </dl>
            </div>

            <div className="chart-overlay">
              <div className="ohlc-line">
                <span>O <b>{open != null ? open.toFixed(2) : "—"}</b></span>
                <span>H <b>{high != null ? high.toFixed(2) : "—"}</b></span>
                <span>L <b>{low != null ? low.toFixed(2) : "—"}</b></span>
                <span>C <b>{quote ? quote.last.toFixed(2) : liveSelected ? liveSelected.last.toFixed(2) : "—"}</b></span>
                <span className={changePercent < 0 ? "negative" : "positive"}>
                  {changeAmount >= 0 ? "+" : ""}{changeAmount.toFixed(2)} ({changePercent.toFixed(2)}%)
                </span>
              </div>
            </div>
          </div>

          <div className="bottom-bar">
            <button className="active">1D</button><button>5D</button><button>1M</button><button>3M</button><button>6M</button><button>YTD</button><button>1Y</button><button>5Y</button><button>ALL</button>
            <span className="bottom-spacer" />
            <button onClick={() => setPanel("indicators")}>INDICATORS</button>
            <button onClick={() => setPanel("pipscript")}>PIPSCRIPT</button>
          </div>
        </section>

        {watchOpen ? (
          <aside className="watchlist" style={{ width: watchWidth }}>
            <div className="watch-resize-handle" onPointerDown={startResize} />
            <div className="watch-tabs">
              <button className="active">WATCHLIST</button>
              <button>MARKET</button>
              <button>MOVERS</button>
            </div>
            <div className="watch-header">
              <strong>My Watchlist</strong>
              <span>{watchlist.length} / {MAX_WATCHLIST_SIZE}</span>
              <div className="watch-spacer" />
              <button onClick={() => setWatchOpen(false)}>HIDE</button>
              <button onClick={() => watchImportRef.current?.click()}>IMPORT</button>
              <button onClick={exportWatchlist}>EXPORT</button>
              <button onClick={addWatchSymbol}>ADD</button>
              <input
                ref={watchImportRef}
                className="watch-import-input"
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void importWatchlist(file);
                }}
              />
            </div>
            <input className="watch-search" placeholder="Search symbols..." value={watchSearch}
              onChange={(event) => setWatchSearch(event.target.value)} />
            {watchImportMessage && <div className="watch-message">{watchImportMessage}</div>}
            <div className="watch-columns"><span>SYMBOL</span><span>LAST</span><span>CHANGE %</span></div>
            <div className="watch-items">
              {filteredWatchlist.map((item) => (
                <button key={item.symbol} className={`watch-row ${item.symbol === symbol ? "selected" : ""}`}
                  onClick={() => selectSymbol(item.symbol)}>
                  <span className="watch-symbol">{item.symbol}</span>
                  <span className="watch-price">{liveQuotes[item.symbol] ? liveQuotes[item.symbol].last.toFixed(2) : "—"}</span>
                  <span className={`watch-change ${(liveQuotes[item.symbol]?.change_percent ?? Number(item.change.replace("%", ""))) < 0 ? "negative" : "positive"}`}>
                    {liveQuotes[item.symbol] ? `${liveQuotes[item.symbol].change_percent >= 0 ? "+" : ""}${liveQuotes[item.symbol].change_percent.toFixed(2)}%` : "—"}
                  </span>
                </button>
              ))}
            </div>
            <div className="watch-footer">Click a symbol to load chart</div>
          </aside>
        ) : (
          <button className="watch-open" onClick={() => setWatchOpen(true)}>SHOW WATCHLIST</button>
        )}
      </section>

      {panel && (
        <div className="modal-backdrop" onClick={() => setPanel(null)}>
          <section className={`modal ${panel === "pipscript" ? "pipscript-modal" : ""}`} onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <strong>{panel === "pipscript" ? "PIPSGOX PIPSCRIPT BUILDER" : panel === "settings" ? "CHART SETTINGS" : "INDICATORS"}</strong>
              <button onClick={() => setPanel(null)}>CLOSE</button>
            </div>

            {panel === "indicators" ? (
              <div className="indicator-panel">
                <p>Select indicators to display on the active chart.</p>
                {indicators.map((item) => (
                  <button key={item} className="indicator-row">
                    <span>{item}</span><span>ADD</span>
                  </button>
                ))}
              </div>
            ) : panel === "pipscript" ? (
              <div className="builder-panel">
                <div className="builder-toolbar">
                  <span>Python</span><button>JavaScript</button><span className="builder-spacer" /><button>LOAD</button><button>SAVE</button><button>RUN</button>
                </div>
                <textarea value={pipscript} onChange={(event) => setPipscript(event.target.value)} spellCheck={false} />
                <div className="builder-output">
                  <strong>Output</strong>
                  <span>Lines, values, markers and tables will appear here.</span>
                </div>
              </div>
            ) : (
              <div className="settings-panel">
                <div className="settings-section">
                  <div className="settings-section-title">APPEARANCE</div>
                  <label className="settings-field">
                    <span>Chart theme</span>
                    <select value={chartSettings.theme} onChange={(event) => updateChartSettings({ theme: event.target.value as ChartTheme })}>
                      <option value="pipsgox">PIPSGOX Dark</option>
                      <option value="classic">Classic Dark</option>
                      <option value="light">Clean Light</option>
                    </select>
                  </label>
                </div>
                <div className="settings-section">
                  <div className="settings-section-title">CHART ELEMENTS</div>
                  <label className="settings-toggle"><span>Grid</span><input type="checkbox" checked={chartSettings.showGrid} onChange={(event) => updateChartSettings({ showGrid: event.target.checked })} /><i /></label>
                  <label className="settings-toggle"><span>Crosshair</span><input type="checkbox" checked={chartSettings.showCrosshair} onChange={(event) => updateChartSettings({ showCrosshair: event.target.checked })} /><i /></label>
                  <label className="settings-toggle"><span>Volume pane</span><input type="checkbox" checked={chartSettings.showVolume} onChange={(event) => updateChartSettings({ showVolume: event.target.checked })} /><i /></label>
                </div>
                <div className="settings-note">Changes apply immediately and are saved in this browser.</div>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
