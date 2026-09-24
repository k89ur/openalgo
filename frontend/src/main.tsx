import { useEffect, useMemo, useRef, useState, type PointerEvent, type SetStateAction } from "react";
import ReactDOM from "react-dom/client";
import { Chart, type ChartType, type Timeframe, type PipscriptOutput } from "./Chart";
import "./styles.css";

type WatchItem = { symbol: string; price: string; change: string };
export type ChartTheme = "pipsgox" | "classic" | "light";
type PipscriptLanguage = "python" | "javascript";
type PipscriptOutputType = "indicator" | "table";

type PyodideRuntime = {
  runPythonAsync: (code: string) => Promise<unknown>;
};

declare global {
  interface Window {
    loadPyodide?: (options?: { indexURL?: string }) => Promise<PyodideRuntime>;
  }
}

let pyodidePromise: Promise<PyodideRuntime> | null = null;

function loadPyodideRuntime(): Promise<PyodideRuntime> {
  if (window.loadPyodide) {
    return window.loadPyodide({
      indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/",
    });
  }

  if (pyodidePromise) return pyodidePromise;

  pyodidePromise = new Promise<PyodideRuntime>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>("script[data-pipsgox-pyodide]");
    if (existing) {
      existing.addEventListener("load", () => {
        if (!window.loadPyodide) reject(new Error("Pyodide loaded without loadPyodide()."));
        else resolve(window.loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/" }));
      }, { once: true });
      existing.addEventListener("error", () => reject(new Error("Could not load the Python runtime.")), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.js";
    script.async = true;
    script.dataset.pipsgoxPyodide = "true";
    script.onload = () => {
      if (!window.loadPyodide) reject(new Error("Pyodide loaded without loadPyodide()."));
      else resolve(window.loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/" }));
    };
    script.onerror = () => reject(new Error("Could not load the Python runtime."));
    document.head.appendChild(script);
  });

  return pyodidePromise;
}

function normalizePipscriptOutput(raw: unknown, preferredType: PipscriptOutputType): PipscriptOutput {
  if (!raw || typeof raw !== "object") throw new Error("Script must return an object.");

  const value = raw as Record<string, unknown>;
  const outputType = value.type === "table" || value.type === "line" ? value.type : preferredType === "table" ? "table" : "line";

  if (outputType === "table") {
    const columns = Array.isArray(value.columns)
      ? value.columns.map((item) => String(item)).slice(0, 12)
      : [];
    const rows = Array.isArray(value.rows)
      ? value.rows
          .filter((row): row is unknown[] => Array.isArray(row))
          .slice(0, 50)
          .map((row) => row.slice(0, columns.length || 12).map((item) => String(item ?? "")))
      : [];

    if (!columns.length) throw new Error("Table output needs a non-empty columns array.");
    return {
      type: "table",
      title: String(value.title || "PIPScript Table"),
      columns,
      rows,
    };
  }

  const rawPoints = Array.isArray(value.points)
    ? value.points
    : Array.isArray(value.values)
      ? value.values
      : [];

  const points = rawPoints
    .filter((point): point is Record<string, unknown> => Boolean(point) && typeof point === "object")
    .map((point) => ({
      time: Number(point.time),
      value: Number(point.value),
    }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value));

  if (!points.length) throw new Error("Indicator output needs points: [{ time, value }].");

  return {
    type: "line",
    name: String(value.name || "PIPScript"),
    points,
  };
}

type ChartSettings = {
  theme: ChartTheme;
  showGrid: boolean;
  showCrosshair: boolean;
  showVolume: boolean;
  showVwap: boolean;
  show52WeekHigh: boolean;
  show52WeekLow: boolean;
  showPreviousClose: boolean;
};

const DEFAULT_CHART_SETTINGS: ChartSettings = {
  theme: "pipsgox",
  showGrid: true,
  showCrosshair: true,
  showVolume: true,
  showVwap: false,
  show52WeekHigh: false,
  show52WeekLow: false,
  showPreviousClose: false,
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
const WATCHLIST_STORAGE_KEY = "pipsgox-watchlists";
const LEGACY_WATCHLIST_STORAGE_KEY = "pipsgox-watchlist";
const DEFAULT_WATCHLIST_NAME = "Main";
const MAX_WATCHLISTS = 10;

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

function normalizeWatchItems(value: unknown): WatchItem[] {
  if (!Array.isArray(value)) return [];

  const symbols = value
    .map((item) => (typeof item === "string" ? item : (item as Partial<WatchItem>)?.symbol))
    .map((item) => String(item ?? "").trim().toUpperCase())
    .filter(Boolean);

  const unique = [...new Set(symbols)].slice(0, MAX_WATCHLIST_SIZE);
  return unique.map((item) => ({ symbol: item, price: "—", change: "—" }));
}

function loadWatchlists(): Record<string, WatchItem[]> {
  try {
    const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const result: Record<string, WatchItem[]> = {};

      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        for (const [name, value] of Object.entries(parsed)) {
          const cleanName = name.trim().slice(0, 24);
          if (cleanName) result[cleanName] = normalizeWatchItems(value);
        }
      }

      if (Object.keys(result).length) return result;
    }

    const legacy = localStorage.getItem(LEGACY_WATCHLIST_STORAGE_KEY);
    const migrated = legacy ? normalizeWatchItems(JSON.parse(legacy)) : DEFAULT_WATCHLIST;
    return { [DEFAULT_WATCHLIST_NAME]: migrated };
  } catch {
    return { [DEFAULT_WATCHLIST_NAME]: DEFAULT_WATCHLIST };
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

  return [...new Set(symbols)];
}

const coreIndicators = ["MA 50", "MA 200"];

function App() {
  const [chartType, setChartType] = useState<ChartType>("candles");
  const [timeframe, setTimeframe] = useState<Timeframe>("D");
  const [watchlists, setWatchlists] = useState<Record<string, WatchItem[]>>(loadWatchlists);
  const [activeWatchlistName, setActiveWatchlistName] = useState(() => Object.keys(loadWatchlists())[0] ?? DEFAULT_WATCHLIST_NAME);
  const watchlist = watchlists[activeWatchlistName] ?? [];
  const setWatchlist = (update: SetStateAction<WatchItem[]>) => {
    setWatchlists((current) => {
      const currentList = current[activeWatchlistName] ?? [];
      const nextList = typeof update === "function" ? update(currentList) : update;
      return { ...current, [activeWatchlistName]: nextList };
    });
  };
  const [symbol, setSymbol] = useState(() => {
    const initialLists = loadWatchlists();
    const initial = Object.values(initialLists)[0] ?? DEFAULT_WATCHLIST;
    return initial[0]?.symbol ?? "BHARTIARTL";
  });
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<Array<{
    symbol: string;
    name: string;
    exchange: string;
    api_symbol: string;
  }>>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
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
  const [draggedSymbol, setDraggedSymbol] = useState<string | null>(null);
  const [fyersChecking, setFyersChecking] = useState(true);
  const watchImportRef = useRef<HTMLInputElement>(null);
  const updateChartSettings = (patch: Partial<ChartSettings>) => {
    setChartSettings((current) => {
      const next = { ...current, ...patch };
      localStorage.setItem("pipsgox-chart-settings", JSON.stringify(next));
      return next;
    });
  };

  useEffect(() => {
    localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlists));
  }, [watchlists]);

  useEffect(() => {
    let cancelled = false;

    const ensureFyersConnection = async () => {
      try {
        const response = await fetch("/api/fyers/status", { cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json() as { configured?: boolean; connected?: boolean };

        if (cancelled) return;
        if (data.configured && !data.connected) {
          window.location.replace("/auth/fyers/login");
          return;
        }
      } catch {
        // Keep the terminal usable when the backend is temporarily unavailable.
      } finally {
        if (!cancelled) setFyersChecking(false);
      }
    };

    void ensureFyersConnection();
    return () => {
      cancelled = true;
    };
  }, []);


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

  const createWatchlist = () => {
    if (Object.keys(watchlists).length >= MAX_WATCHLISTS) {
      setWatchImportMessage(`Maximum ${MAX_WATCHLISTS} watchlists`);
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    const value = window.prompt("New watchlist name");
    const name = String(value ?? "").trim().replace(/\s+/g, " ").slice(0, 24);
    if (!name) return;

    if (watchlists[name]) {
      setActiveWatchlistName(name);
      return;
    }

    setWatchlists((current) => ({ ...current, [name]: [] }));
    setActiveWatchlistName(name);
    setSymbol("BHARTIARTL");
    setSearch("BHARTIARTL");
    setSearchOpen(false);
  };

  const deleteWatchlist = () => {
    const names = Object.keys(watchlists);
    if (names.length <= 1) {
      setWatchImportMessage("Keep at least one watchlist");
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    if (!window.confirm(`Delete watchlist "${activeWatchlistName}"?`)) return;

    const next = { ...watchlists };
    delete next[activeWatchlistName];
    const nextName = Object.keys(next)[0];
    setWatchlists(next);
    setActiveWatchlistName(nextName);
    setSymbol(next[nextName]?.[0]?.symbol ?? "BHARTIARTL");
    setSearch(next[nextName]?.[0]?.symbol ?? "");
  };

  const moveWatchSymbol = (currentIndex: number, direction: -1 | 1) => {
    setWatchlist((current) => {
      const nextIndex = currentIndex + direction;
      if (currentIndex < 0 || nextIndex < 0 || nextIndex >= current.length) return current;

      const next = [...current];
      [next[currentIndex], next[nextIndex]] = [next[nextIndex], next[currentIndex]];
      return next;
    });
  };

  const removeWatchSymbol = (itemSymbol: string) => {
    setWatchlist((current) => current.filter((item) => item.symbol !== itemSymbol));
    if (symbol === itemSymbol) {
      const remaining = watchlist.filter((item) => item.symbol !== itemSymbol);
      const next = remaining[0]?.symbol ?? "BHARTIARTL";
      setSymbol(next);
      setSearch(next);
    }
  };

  const dropWatchSymbol = (targetSymbol: string) => {
    if (!draggedSymbol || draggedSymbol === targetSymbol) return;

    setWatchlist((current) => {
      const from = current.findIndex((item) => item.symbol === draggedSymbol);
      const to = current.findIndex((item) => item.symbol === targetSymbol);
      if (from < 0 || to < 0) return current;

      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });

    setDraggedSymbol(null);
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

  const [pipscriptLanguage, setPipscriptLanguage] = useState<PipscriptLanguage>("python");
  const [pipscriptOutputType, setPipscriptOutputType] = useState<PipscriptOutputType>("indicator");
  const [pipscript, setPipscript] = useState(
    "def calculate(data):\n    values = []\n    for row in data:\n        values.append({\"time\": row[\"time\"], \"value\": row[\"close\"]})\n    return {\"type\": \"line\", \"name\": \"Close Script\", \"values\": values}",
  );
  const [pipscriptOutput, setPipscriptOutput] = useState<PipscriptOutput | null>(null);
  const [pipscriptStatus, setPipscriptStatus] = useState("Ready");
  const [pipscriptRunning, setPipscriptRunning] = useState(false);

  const runPipscript = async () => {
    setPipscriptRunning(true);
    setPipscriptStatus("Loading chart data...");

    try {
      const response = await fetch(
        "/api/history?symbol=" + encodeURIComponent(symbol) + "&timeframe=" + encodeURIComponent(timeframe) + "&limit=800",
        { cache: "no-store" },
      );
      if (!response.ok) throw new Error("Could not load chart data.");

      const raw = await response.json() as Array<{
        time: number;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
      }>;

      const data = raw.map((row) => ({
        time: Number(row.time),
        open: Number(row.open),
        high: Number(row.high),
        low: Number(row.low),
        close: Number(row.close),
        volume: Number(row.volume || 0),
      }));

      let rawOutput: unknown;

      if (pipscriptLanguage === "javascript") {
        setPipscriptStatus("Running JavaScript...");
        const runner = new Function(
          "data",
          `"use strict";
${pipscript}
if (typeof calculate !== "function") {
  throw new Error("Define calculate(data) in your script.");
}
return calculate(data);`,
        );
        rawOutput = runner(data);
      } else {
        setPipscriptStatus("Loading Python runtime...");
        const pyodide = await loadPyodideRuntime();
        setPipscriptStatus("Running Python...");
        const serializedData = JSON.stringify(data);
        const pythonCode = `import json
data = json.loads(${JSON.stringify(serializedData)})
${pipscript}
if "calculate" not in globals():
    raise RuntimeError("Define calculate(data) in your script.")
_result = calculate(data)
json.dumps(_result)`;
        rawOutput = JSON.parse(String(await pyodide.runPythonAsync(pythonCode)));
      }

      const normalized = normalizePipscriptOutput(rawOutput, pipscriptOutputType);
      setPipscriptOutput(normalized);
      setPipscriptStatus(
        normalized.type === "table"
          ? `Table ready · ${normalized.rows.length} rows`
          : `Indicator ready · ${normalized.points.length} points`,
      );
    } catch (error) {
      setPipscriptOutput(null);
      setPipscriptStatus(error instanceof Error ? error.message : "PIPScript failed.");
    } finally {
      setPipscriptRunning(false);
    }
  };

  const savePipscript = () => {
    localStorage.setItem("pipsgox-pipscript", JSON.stringify({
      language: pipscriptLanguage,
      outputType: pipscriptOutputType,
      code: pipscript,
    }));
    setPipscriptStatus("Saved in this browser.");
  };

  const loadPipscript = () => {
    try {
      const raw = localStorage.getItem("pipsgox-pipscript");
      if (!raw) {
        setPipscriptStatus("No saved PIPScript found.");
        return;
      }
      const saved = JSON.parse(raw) as {
        language?: PipscriptLanguage;
        outputType?: PipscriptOutputType;
        code?: string;
      };
      if (saved.language === "python" || saved.language === "javascript") setPipscriptLanguage(saved.language);
      if (saved.outputType === "indicator" || saved.outputType === "table") setPipscriptOutputType(saved.outputType);
      if (typeof saved.code === "string") setPipscript(saved.code);
      setPipscriptStatus("Loaded from this browser.");
    } catch {
      setPipscriptStatus("Saved PIPScript is invalid.");
    }
  };

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
    setLiveQuotes({});
    if (!watchlist.length) return;

    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (stopped) return;

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/api/ws/quotes`);

      socket.onopen = () => {
        socket?.send(JSON.stringify({
          action: "subscribe",
          symbols: watchlist.map((item) => item.symbol),
        }));
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as {
            type?: string;
            symbol?: string;
            last?: number;
            change?: number;
            change_percent?: number;
          };

          if (message.type !== "quote" || !message.symbol || message.last == null) return;

          const item = {
            symbol: message.symbol,
            last: Number(message.last),
            change: Number(message.change ?? 0),
            change_percent: Number(message.change_percent ?? 0),
          };

          setLiveQuotes((current) => ({ ...current, [item.symbol]: item }));
        } catch {
          // Ignore malformed WebSocket messages.
        }
      };

      socket.onclose = () => {
        if (!stopped) reconnectTimer = window.setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [watchlist]);

  useEffect(() => {
    if (!watchlist.length) {
      setLiveQuotes({});
      return;
    }

    let cancelled = false;

    const loadWatchlistQuotes = async () => {
      try {
        const symbols = watchlist.map((item) => item.symbol).join(",");
        const response = await fetch(
          "/api/quotes?symbols=" + encodeURIComponent(symbols),
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("watchlist quote request failed");

        const data = await response.json() as Array<{
          symbol: string;
          last: number;
          change: number;
          change_percent: number;
        }>;

        if (cancelled) return;

        setLiveQuotes((current) => {
          const next = { ...current };
          for (const item of data) {
            if (!item.symbol) continue;
            next[item.symbol] = {
              last: Number(item.last),
              change: Number(item.change ?? 0),
              change_percent: Number(item.change_percent ?? 0),
            };
          }
          return next;
        });
      } catch {
        // Keep existing live values; WebSocket remains the primary live stream.
      }
    };

    void loadWatchlistQuotes();
    const timer = window.setInterval(() => void loadWatchlistQuotes(), 5000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [watchlist]);

  const selected = useMemo(
    () =>
      watchlist.find((item) => item.symbol === symbol) ??
      watchlist[0] ??
      { symbol: symbol || "BHARTIARTL", price: "—", change: "—" },
    [symbol, watchlist],
  );

  useEffect(() => {
    const query = search.trim();

    if (!query) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }

    let cancelled = false;
    setSearchLoading(true);

    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(
          "/api/symbols/search?q=" + encodeURIComponent(query) + "&limit=12",
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("symbol search failed");

        const data = await response.json() as Array<{
          symbol: string;
          name: string;
          exchange: string;
          api_symbol: string;
        }>;

        if (!cancelled) setSearchResults(data);
      } catch {
        if (!cancelled) setSearchResults([]);
      } finally {
        if (!cancelled) setSearchLoading(false);
      }
    }, 220);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search]);

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
  const previousClose = quote ? quote.last - quote.change : undefined;

  const selectSymbol = (next: string) => {
    setSymbol(next);
    setSearch(next);
  };

  const submitSearch = () => {
    const query = search.trim().toUpperCase();
    const watchMatch = watchlist.find((item) => item.symbol === query);

    if (watchMatch) {
      selectSymbol(watchMatch.symbol);
      setSearchOpen(false);
      return;
    }

    const result = searchResults[0];
    if (result) {
      selectSymbol(result.symbol);
      setSearchOpen(false);
    }
  };

  const openSearchResult = (result: {
    symbol: string;
    name: string;
    exchange: string;
    api_symbol: string;
  }) => {
    selectSymbol(result.symbol);
    setSearchOpen(false);
    setWatchImportMessage(
      watchlist.some((item) => item.symbol === result.symbol)
        ? "Opened " + result.symbol
        : result.symbol + " opened — use ADD to put it in " + activeWatchlistName,
    );
    window.setTimeout(() => setWatchImportMessage(""), 3000);
  };

  const addSearchResult = (result: {
    symbol: string;
    name: string;
    exchange: string;
    api_symbol: string;
  }) => {
    if (watchlist.some((item) => item.symbol === result.symbol)) {
      openSearchResult(result);
      return;
    }

    if (watchlist.length >= MAX_WATCHLIST_SIZE) {
      setWatchImportMessage("Watchlist limit reached: 1000 symbols");
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    setWatchlist((current) => [
      ...current,
      { symbol: result.symbol, price: "—", change: "—" },
    ]);
    selectSymbol(result.symbol);
    setSearchOpen(false);
    setWatchImportMessage("Added " + result.symbol + " to " + activeWatchlistName);
    window.setTimeout(() => setWatchImportMessage(""), 2500);
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
        <div className="menu-right"><span className="status-dot" /> {fyersChecking ? "Connecting FYERS..." : "Data: FYERS API V3"}</div>
      </div>

      <header className="topbar">
        <button className="top-command">NEW WINDOW</button>
        <button className="top-command">MARKET</button>
        <button className="top-command">WATCHLIST</button>
        <button className="top-command">ORDERS</button>

        <div className="top-search-wrap">
          <div className="top-search">
            <input
              value={search}
              placeholder={selected.symbol}
              aria-label="Search NSE symbol"
              onFocus={() => setSearchOpen(true)}
              onChange={(event) => {
                setSearch(event.target.value);
                setSearchOpen(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitSearch();
                if (event.key === "Escape") setSearchOpen(false);
              }}
            />
            <button onClick={submitSearch}>GO</button>
          </div>

          {searchOpen && search.trim() && (
            <div className="symbol-search-results">
              {searchLoading ? (
                <div className="symbol-search-empty">Searching NSE symbols...</div>
              ) : searchResults.length ? (
                searchResults.map((result) => (
                  <div key={result.api_symbol} className="symbol-search-row">
                    <button className="symbol-search-main" onClick={() => openSearchResult(result)}>
                      <strong>{result.symbol}</strong>
                      <span>{result.name}</span>
                    </button>
                    <button className="symbol-search-add" onClick={() => addSearchResult(result)} aria-label={"Add " + result.symbol + " to watchlist"}>
                      +
                    </button>
                  </div>
                ))
              ) : (
                <div className="symbol-search-empty">No NSE symbols found</div>
              )}
            </div>
          )}
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
              showVwap={chartSettings.showVwap}
              show52WeekHigh={chartSettings.show52WeekHigh}
              show52WeekLow={chartSettings.show52WeekLow}
              showPreviousClose={chartSettings.showPreviousClose}
              previousClose={previousClose}
              pipscriptOutput={pipscriptOutput}
            />

            <div className="chart-header-overlay">
              <span className="chart-header-symbol">
                {selected.symbol} · {timeframe} · NSE
              </span>
              <span className={`chart-header-price ${changePercent < 0 ? "negative" : "positive"}`}>
                {hasLiveData ? price.toFixed(2) : "—"}
              </span>
              <span className={`chart-header-change ${changePercent < 0 ? "negative" : "positive"}`}>
                {hasLiveData
                  ? `${changeAmount >= 0 ? "+" : ""}${changeAmount.toFixed(2)} (${changePercent.toFixed(2)}%)`
                  : "No data"}
              </span>
              {hasLiveData && (
                <span className="chart-header-ohlc">
                  O <b>{open != null ? open.toFixed(2) : "—"}</b>
                  H <b>{high != null ? high.toFixed(2) : "—"}</b>
                  L <b>{low != null ? low.toFixed(2) : "—"}</b>
                  C <b>{price.toFixed(2)}</b>
                </span>
              )}
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
              <select
                className="watchlist-selector"
                value={activeWatchlistName}
                onChange={(event) => {
                  const name = event.target.value;
                  setActiveWatchlistName(name);
                  const first = watchlists[name]?.[0]?.symbol;
                  if (first) {
                    setSymbol(first);
                    setSearch(first);
                  }
                }}
                aria-label="Select watchlist"
              >
                {Object.keys(watchlists).map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
              <button className="active">WATCHLIST</button>
              <button>MARKET</button>
              <button>MOVERS</button>
            </div>
            <div className="watch-header">
              <strong>{activeWatchlistName}</strong>
              <span>{watchlist.length} / {MAX_WATCHLIST_SIZE}</span>
              <div className="watch-spacer" />
              <button onClick={() => setWatchOpen(false)}>HIDE</button>
              <button onClick={createWatchlist}>NEW</button>
              <button onClick={deleteWatchlist}>DEL</button>
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
            <div className="watch-columns"><span>SYMBOL</span><span>LAST</span><span>CHANGE %</span><span></span></div>
            <div className="watch-items">
              {filteredWatchlist.map((item) => (
                <div
                  key={item.symbol}
                  className={`watch-row ${item.symbol === symbol ? "selected" : ""}`}
                  draggable
                  onDragStart={() => setDraggedSymbol(item.symbol)}
                  onDragEnd={() => setDraggedSymbol(null)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => dropWatchSymbol(item.symbol)}
                >
                  <button className="watch-row-main" onClick={() => selectSymbol(item.symbol)}>
                    <span className="watch-symbol">{item.symbol}</span>
                    <span className="watch-price">{liveQuotes[item.symbol] ? liveQuotes[item.symbol].last.toFixed(2) : "—"}</span>
                    <span className={`watch-change ${(liveQuotes[item.symbol]?.change_percent ?? Number(item.change.replace("%", ""))) < 0 ? "negative" : "positive"}`}>
                      {liveQuotes[item.symbol] ? `${liveQuotes[item.symbol].change_percent >= 0 ? "+" : ""}${liveQuotes[item.symbol].change_percent.toFixed(2)}%` : "—"}
                    </span>
                  </button>
                  <button className="watch-move" onClick={() => moveWatchSymbol(watchlist.indexOf(item), -1)} aria-label={`Move ${item.symbol} up`}>▲</button>
                  <button className="watch-move" onClick={() => moveWatchSymbol(watchlist.indexOf(item), 1)} aria-label={`Move ${item.symbol} down`}>▼</button>
                  <button className="watch-remove" onClick={() => removeWatchSymbol(item.symbol)} aria-label={`Remove ${item.symbol}`}>×</button>
                </div>
              ))}
            </div>
            <div className="watch-footer">
              {watchlist.length ? "Click a symbol to load chart" : "Watchlist empty — use symbol search or ADD"}
            </div>
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
                <p>Select technical studies and optional overlays for the active chart.</p>

                <div className="indicator-section-title">CORE CHART INDICATORS</div>
                {coreIndicators.map((item) => (
                  <div key={item} className="indicator-row indicator-row-static">
                    <span>{item}</span><span>ON</span>
                  </div>
                ))}
                <label className="indicator-toggle-row">
                  <span>Volume</span>
                  <input
                    type="checkbox"
                    checked={chartSettings.showVolume}
                    onChange={(event) => updateChartSettings({ showVolume: event.target.checked })}
                  />
                  <i />
                </label>

                <div className="indicator-section-title optional">OPTIONAL CHART OVERLAYS</div>
                <p className="indicator-help">These stay hidden unless you select them.</p>

                <label className="indicator-toggle-row">
                  <span>VWAP <small>intraday</small></span>
                  <input
                    type="checkbox"
                    checked={chartSettings.showVwap}
                    onChange={(event) => updateChartSettings({ showVwap: event.target.checked })}
                  />
                  <i />
                </label>

                <label className="indicator-toggle-row">
                  <span>52W High</span>
                  <input
                    type="checkbox"
                    checked={chartSettings.show52WeekHigh}
                    onChange={(event) => updateChartSettings({ show52WeekHigh: event.target.checked })}
                  />
                  <i />
                </label>

                <label className="indicator-toggle-row">
                  <span>52W Low</span>
                  <input
                    type="checkbox"
                    checked={chartSettings.show52WeekLow}
                    onChange={(event) => updateChartSettings({ show52WeekLow: event.target.checked })}
                  />
                  <i />
                </label>

                <label className="indicator-toggle-row">
                  <span>Previous Close</span>
                  <input
                    type="checkbox"
                    checked={chartSettings.showPreviousClose}
                    onChange={(event) => updateChartSettings({ showPreviousClose: event.target.checked })}
                  />
                  <i />
                </label>

              </div>
            ) : panel === "pipscript" ? (
              <div className="builder-panel">
                <div className="builder-toolbar">
                  <select
                    value={pipscriptLanguage}
                    onChange={(event) => {
                      const language = event.target.value as PipscriptLanguage;
                      setPipscriptLanguage(language);
                      if (language === "python") {
                        setPipscript(
                          "def calculate(data):\n    values = []\n    for row in data:\n        values.append({\"time\": row[\"time\"], \"value\": row[\"close\"]})\n    return {\"type\": \"line\", \"name\": \"Close Script\", \"values\": values}",
                        );
                      } else {
                        setPipscript(
                          "function calculate(data) {\n  return {\n    type: \"line\",\n    name: \"Close Script\",\n    values: data.map(row => ({ time: row.time, value: row.close }))\n  };\n}",
                        );
                      }
                    }}
                  >
                    <option value="python">Python</option>
                    <option value="javascript">JavaScript</option>
                  </select>
                  <select value={pipscriptOutputType} onChange={(event) => setPipscriptOutputType(event.target.value as PipscriptOutputType)}>
                    <option value="indicator">Indicator</option>
                    <option value="table">Table</option>
                  </select>
                  <span className="builder-spacer" />
                  <button onClick={loadPipscript}>LOAD</button>
                  <button onClick={savePipscript}>SAVE</button>
                  <button className="builder-run" onClick={() => void runPipscript()} disabled={pipscriptRunning}>
                    {pipscriptRunning ? "RUNNING..." : "RUN"}
                  </button>
                </div>
                <div className="builder-hint">
                  Input: <code>data</code> = current chart OHLCV candles. Return an indicator with <code>values</code>, or a table with <code>columns</code>/<code>rows</code>.
                </div>
                <textarea value={pipscript} onChange={(event) => setPipscript(event.target.value)} spellCheck={false} />
                <div className="builder-output">
                  <strong>Output</strong>
                  <span>{pipscriptStatus}</span>
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
