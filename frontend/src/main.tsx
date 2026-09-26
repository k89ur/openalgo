import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type SetStateAction } from "react";
import ReactDOM from "react-dom/client";
import { Chart, type ChartType, type Timeframe, type ChartRange, type PipscriptOutput, type ChartColors } from "./Chart";
import "./styles.css";
import { DevConsole } from "./DevConsole";

type WatchItem = { symbol: string; price: string; change: string; apiSymbol?: string };
export type ChartTheme = "pipsgox" | "classic" | "light";
type PipscriptLanguage = "python" | "javascript";
type PipscriptOutputType = "indicator" | "table";
type PipscriptScope = "symbol" | "multi-symbol" | "static";

type ActivePipscript = {
  language: PipscriptLanguage;
  outputType: PipscriptOutputType;
  code: string;
  scope: PipscriptScope;
};

type CachedPipscriptResult = {
  output: PipscriptOutput;
  cachedAt: number;
};

type PyodideRuntime = {
  runPythonAsync: (code: string) => Promise<unknown>;
};

declare global {
  interface Window {
    loadPyodide?: (options?: { indexURL?: string }) => Promise<PyodideRuntime>;
  }
}

let pyodidePromise: Promise<PyodideRuntime> | null = null;
let pyodideRuntime: PyodideRuntime | null = null;

function loadPyodideRuntime(): Promise<PyodideRuntime> {
  if (pyodideRuntime) return Promise.resolve(pyodideRuntime);
  if (pyodidePromise) return pyodidePromise;

  if (window.loadPyodide) {
    pyodidePromise = window.loadPyodide({
      indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/",
    }).then((runtime) => {
      pyodideRuntime = runtime;
      return runtime;
    });
    return pyodidePromise;
  }

  pyodidePromise = new Promise<PyodideRuntime>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>("script[data-pipsgox-pyodide]");
    if (existing) {
      existing.addEventListener("load", () => {
        if (!window.loadPyodide) reject(new Error("Pyodide loaded without loadPyodide()."));
        else window.loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/" }).then((runtime) => {
          pyodideRuntime = runtime;
          resolve(runtime);
        }).catch(reject);
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
      else window.loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/" }).then((runtime) => {
        pyodideRuntime = runtime;
        resolve(runtime);
      }).catch(reject);
    };
    script.onerror = () => reject(new Error("Could not load the Python runtime."));
    document.head.appendChild(script);
  });

  return pyodidePromise;
}

function inferPipscriptScope(code: string): PipscriptScope {
  // A script without data_requests() receives the selected chart candles,
  // so it is inherently dependent on the current symbol.
  if (!/\bdata_requests\s*\(/.test(code)) return "symbol";

  // SYMBOL is injected by the runtime and is the standard way for a script
  // to declare that its data follows the currently selected chart symbol.
  if (/\bSYMBOL\b/.test(code)) return "symbol";

  // A data_requests() script with fixed symbols is multi-symbol. It should
  // not be re-executed just because the chart selection changes.
  return "multi-symbol";
}

function pipscriptCacheKey(
  language: PipscriptLanguage,
  outputType: PipscriptOutputType,
  code: string,
  symbolValue: string,
  timeframeValue: Timeframe,
): string {
  return JSON.stringify([language, outputType, code, symbolValue, timeframeValue]);
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

    const rawLines = Array.isArray(value.lines) ? value.lines : [];
    const lines = rawLines
      .filter((line): line is Record<string, unknown> => Boolean(line) && typeof line === "object")
      .slice(0, 20)
      .map((line) => {
        const rawPoints = Array.isArray(line.points) ? line.points : [];
        const points = rawPoints
          .filter((point): point is Record<string, unknown> => Boolean(point) && typeof point === "object")
          .map((point) => ({
            time: Number(point.time),
            value: Number(point.value),
          }))
          .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value));

        return {
          name: String(line.name || "PIPScript"),
          points,
        };
      })
      .filter((line) => line.points.length > 0);

    return {
      type: "table",
      title: String(value.title || "PIPScript Table"),
      columns,
      rows,
      lines,
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
  colors: ChartColors;
  showGrid: boolean;
  showCrosshair: boolean;
  showVolume: boolean;
  showVwap: boolean;
  show52WeekHigh: boolean;
  show52WeekLow: boolean;
  showPreviousClose: boolean;
};

const DEFAULT_CHART_COLORS: ChartColors = {
  background: "#090909", grid: "#202020", axis: "#626b75",
  candleUp: "#12d98b", candleDown: "#ff4d5a",
  volumeUp: "#d9dde3", volumeDown: "#d9dde3",
  ma50: "#f6c85f", ma200: "#b07cff",
};

const CHART_COLOR_PRESETS: Record<ChartTheme, ChartColors> = {
  pipsgox: DEFAULT_CHART_COLORS,
  classic: { background:"#101317", grid:"#28303a", axis:"#697482", candleUp:"#26a69a", candleDown:"#ef5350", volumeUp:"#7d8792", volumeDown:"#7d8792", ma50:"#f6c85f", ma200:"#b07cff" },
  light: { background:"#ffffff", grid:"#e5e7eb", axis:"#9ca3af", candleUp:"#168a59", candleDown:"#c93643", volumeUp:"#8b95a1", volumeDown:"#8b95a1", ma50:"#c27a00", ma200:"#7654a8" },
};

const DEFAULT_CHART_SETTINGS: ChartSettings = {
  theme: "pipsgox",
  colors: DEFAULT_CHART_COLORS,
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
    const parsed = JSON.parse(raw) as Partial<ChartSettings>;
    return { ...DEFAULT_CHART_SETTINGS, ...parsed, colors: { ...DEFAULT_CHART_COLORS, ...(parsed.colors || {}) } };
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

function formatWatchVolume(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (absolute >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (absolute >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return Math.round(value).toLocaleString("en-IN");
}

function formatWatchQuoteValue(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) && value > 0 ? value.toFixed(2) : "—";
}

function normalizeWatchItems(value: unknown): WatchItem[] {
  if (!Array.isArray(value)) return [];

  return value
    .map((item) => {
      if (typeof item === "string") {
        return { symbol: item, price: "—", change: "—" };
      }
      const source = item as Partial<WatchItem>;
      return {
        symbol: String(source.symbol ?? "").trim().toUpperCase(),
        price: String(source.price ?? "—"),
        change: String(source.change ?? "—"),
        ...(source.apiSymbol ? { apiSymbol: String(source.apiSymbol).trim().toUpperCase() } : {}),
      };
    })
    .filter((item) => item.symbol)
    .slice(0, MAX_WATCHLIST_SIZE);
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
  let symbol = value
    .replace(/^\uFEFF/, "")
    .replace(/^["']|["']$/g, "")
    .trim()
    .toUpperCase();

  // Accept broker/FYERS CSV formats such as NSE:MBECL-EQ and MBECL-BE.
  // Keep an explicit supported series so the backend can request the exact
  // instrument instead of incorrectly appending -EQ again.
  if (symbol.startsWith("NSE:")) {
    symbol = symbol.slice(4);
  }
  const match = symbol.match(/^(.+)-(EQ|BE)$/);
  return match ? match[1] + "-" + match[2] : symbol;
}

function parseWatchlistImport(text: string): string[] {
  const rows = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const symbols: string[] = [];

  for (const row of rows) {
    const cells = row.split(/[,;\t]/).map((cell) => normalizeImportedSymbol(cell));
    if (!cells.length) continue;

    const header = cells[0].replace(/\s+/g, "").toLowerCase();
    if (header === "symbol" || header === "ticker" || header === "tradingsymbol") continue;

    // Broker exports often store the ticker and NSE series in separate
    // columns, e.g. "LOTUSDEV,BE". Preserve that series so BE-only stocks
    // reach the backend as LOTUSDEV-BE instead of being forced to -EQ.
    const tickerIndex = cells.findIndex(
      (cell) => cell && !/^(SYMBOL|TICKER|TRADINGSYMBOL)$/i.test(cell),
    );
    const candidate = tickerIndex >= 0 ? cells[tickerIndex] : "";
    const series = cells.slice(tickerIndex + 1).find((cell) => /^(EQ|BE)$/i.test(cell));
    if (candidate) {
      symbols.push(series ? `${candidate}-${series}` : candidate);
    }
  }

  return [...new Set(symbols)];
}

type SavedPipscript = {
  id: string;
  name: string;
  language: PipscriptLanguage;
  outputType: PipscriptOutputType;
  code: string;
  updatedAt: number;
};

const PIPSCRIPT_STORAGE_KEY = "pipsgox-pipscripts";
const LEGACY_PIPSCRIPT_STORAGE_KEY = "pipsgox-pipscript";

function loadSavedPipscripts(): SavedPipscript[] {
  try {
    const raw = localStorage.getItem(PIPSCRIPT_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.filter((item): item is SavedPipscript =>
          Boolean(item) &&
          typeof item === "object" &&
          typeof (item as SavedPipscript).id === "string" &&
          typeof (item as SavedPipscript).name === "string" &&
          ((item as SavedPipscript).language === "python" || (item as SavedPipscript).language === "javascript") &&
          ((item as SavedPipscript).outputType === "indicator" || (item as SavedPipscript).outputType === "table") &&
          typeof (item as SavedPipscript).code === "string"
        );
      }
    }

    // Migrate the previous single-script storage format.
    const legacyRaw = localStorage.getItem(LEGACY_PIPSCRIPT_STORAGE_KEY);
    if (legacyRaw) {
      const saved = JSON.parse(legacyRaw) as {
        language?: PipscriptLanguage;
        outputType?: PipscriptOutputType;
        code?: string;
      };
      if (
        (saved.language === "python" || saved.language === "javascript") &&
        (saved.outputType === "indicator" || saved.outputType === "table") &&
        typeof saved.code === "string"
      ) {
        const migrated: SavedPipscript = {
          id: "legacy-" + Date.now(),
          name: "My PIPScript",
          language: saved.language,
          outputType: saved.outputType,
          code: saved.code,
          updatedAt: Date.now(),
        };
        const list = [migrated];
        localStorage.setItem(PIPSCRIPT_STORAGE_KEY, JSON.stringify(list));
        return list;
      }
    }
  } catch {
    // Ignore invalid local storage and start clean.
  }
  return [];
}

const coreIndicators = ["MA 50", "MA 200"];

type UiIconName = "candle" | "bar" | "line" | "indicator" | "pipscript" | "sun" | "settings" | "add" | "import" | "export" | "new" | "delete" | "hide";

function UiIcon({ name }: { name: UiIconName }) {
  const common = {
    width: 14,
    height: 14,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (name === "candle") {
    return (
      <svg {...common}>
        <path d="M7 3v4M7 17v4M7 7h0M5 7h4v10H5z" />
        <path d="M17 3v7M17 19v2M15 10h4v9h-4z" />
      </svg>
    );
  }

  if (name === "bar") {
    return (
      <svg {...common}>
        <path d="M6 4v16M4 7h4M4 17h4M15 7v10M13 9h4M13 15h4" />
      </svg>
    );
  }

  if (name === "line") {
    return (
      <svg {...common}>
        <path d="M4 17l5-5 4 3 7-8" />
        <path d="M17 7h3v3" />
      </svg>
    );
  }

  if (name === "indicator") {
    return (
      <svg {...common}>
        <path d="M5 19V9M12 19V5M19 19v-7" />
        <path d="M3 19h18" />
      </svg>
    );
  }

  if (name === "pipscript") {
    return (
      <svg {...common}>
        <path d="M8 6l-5 6 5 6M16 6l5 6-5 6M14 4l-4 16" />
      </svg>
    );
  }

  if (name === "add") {
    return (
      <svg {...common}>
        <path d="M12 5v14M5 12h14" />
      </svg>
    );
  }

  if (name === "import") {
    return (
      <svg {...common}>
        <path d="M12 4v11M8 11l4 4 4-4" />
        <path d="M5 19h14" />
      </svg>
    );
  }

  if (name === "export") {
    return (
      <svg {...common}>
        <path d="M12 20V9M8 13l4-4 4 4" />
        <path d="M5 5h14" />
      </svg>
    );
  }

  if (name === "new") {
    return (
      <svg {...common}>
        <path d="M6 4h9l3 3v13H6z" />
        <path d="M14 4v4h4M12 11v6M9 14h6" />
      </svg>
    );
  }

  if (name === "delete") {
    return (
      <svg {...common}>
        <path d="M5 7h14M9 7V4h6v3M8 10v8M12 10v8M16 10v8" />
        <path d="M6 7l1 14h10l1-14" />
      </svg>
    );
  }

  if (name === "hide") {
    return (
      <svg {...common}>
        <path d="M3.5 12s3.2-5 8.5-5 8.5 5 8.5 5-3.2 5-8.5 5-8.5-5-8.5-5z" />
        <path d="M5 5l14 14" />
      </svg>
    );
  }

  if (name === "sun") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="3.5" />
        <path d="M12 2.5v2M12 19.5v2M4.5 4.5l1.4 1.4M18.1 18.1l1.4 1.4M2.5 12h2M19.5 12h2M4.5 19.5l1.4-1.4M18.1 5.9l1.4-1.4" />
      </svg>
    );
  }

  return (
    <svg {...common}>
      <path d="M12 3.5l1.1 1.9 2.2.4.4 2.2 1.9 1.1-1.9 1.1-.4 2.2-2.2.4-1.1 1.9-1.1-1.9-2.2-.4-.4-2.2-1.9-1.1 1.9-1.1.4-2.2 2.2-.4z" />
      <circle cx="12" cy="9.1" r="2.1" />
      <path d="M5.5 20.5h13" />
    </svg>
  );
}

function App() {
  const [chartType, setChartType] = useState<ChartType>("candles");
  const [timeframe, setTimeframe] = useState<Timeframe>("D");
  const [chartRange, setChartRange] = useState<ChartRange>("6M");
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
  const [chartApiSymbol, setChartApiSymbol] = useState<string | null>(null);
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
    last: number;
    change: number;
    change_percent: number;
    volume?: number;
    bid?: number;
    ask?: number;
  }>>({});
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState(false);
  const [chartSettings, setChartSettings] = useState<ChartSettings>(loadChartSettings);
  const [savedChartThemes, setSavedChartThemes] = useState<Record<string, ChartColors>>(() => {
    try { return JSON.parse(localStorage.getItem("pipsgox-chart-themes") || "{}") as Record<string, ChartColors>; } catch { return {}; }
  });
  const [chartThemeName, setChartThemeName] = useState("");
  const [watchImportMessage, setWatchImportMessage] = useState("");
  const [watchDialog, setWatchDialog] = useState<{ type: "add" | "new" | "delete" | "export" | "import"; value: string } | null>(null);
  const [draggedSymbol, setDraggedSymbol] = useState<string | null>(null);
  const [fyersChecking, setFyersChecking] = useState(true);
  const watchImportRef = useRef<HTMLInputElement>(null);
  const applyChartPreset = (theme: ChartTheme) => updateChartSettings({ theme, colors: { ...CHART_COLOR_PRESETS[theme] } });
  const updateChartColor = (key: keyof ChartColors, value: string) => updateChartSettings({ colors: { ...chartSettings.colors, [key]: value } });
  const saveChartTheme = () => {
    const name = chartThemeName.trim().slice(0, 24);
    if (!name) return;
    const next = { ...savedChartThemes, [name]: { ...chartSettings.colors } };
    setSavedChartThemes(next);
    localStorage.setItem("pipsgox-chart-themes", JSON.stringify(next));
    setChartThemeName("");
  };
  const loadChartTheme = (name: string) => {
    if (!name) return;
    const colors = savedChartThemes[name];
    if (colors) updateChartSettings({ colors: { ...DEFAULT_CHART_COLORS, ...colors } });
  };

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
    setWatchDialog(null);
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

  const createWatchlist = (rawName?: string) => {
    if (Object.keys(watchlists).length >= MAX_WATCHLISTS) {
      setWatchDialog(null);
      setWatchImportMessage(`Maximum ${MAX_WATCHLISTS} watchlists`);
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    const name = String(rawName ?? "").trim().replace(/\s+/g, " ").slice(0, 24);
    if (!name) return;

    if (watchlists[name]) {
      setActiveWatchlistName(name);
      setWatchDialog(null);
      return;
    }

    setWatchlists((current) => ({ ...current, [name]: [] }));
    setActiveWatchlistName(name);
    setChartApiSymbol(null);
    setSymbol("BHARTIARTL");
    setSearch("BHARTIARTL");
    setSearchOpen(false);
    setWatchDialog(null);
  };

  const deleteWatchlist = () => {
    const names = Object.keys(watchlists);
    if (names.length <= 1) {
      setWatchDialog(null);
      setWatchImportMessage("Keep at least one watchlist");
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    const next = { ...watchlists };
    delete next[activeWatchlistName];
    const nextName = Object.keys(next)[0];
    setWatchlists(next);
    setActiveWatchlistName(nextName);
    const nextItem = next[nextName]?.[0];
    if (nextItem) selectSymbol(nextItem.symbol, nextItem.apiSymbol);
    else {
      setChartApiSymbol(null);
      setSymbol("BHARTIARTL");
      setSearch("");
    }
    setWatchDialog(null);
  };

  const removeWatchSymbol = (itemSymbol: string) => {
    setWatchlist((current) => current.filter((item) => item.symbol !== itemSymbol));
    if (symbol === itemSymbol) {
      const remaining = watchlist.filter((item) => item.symbol !== itemSymbol);
      const next = remaining[0]?.symbol ?? "BHARTIARTL";
      const nextItem = remaining[0];
      if (nextItem) selectSymbol(nextItem.symbol, nextItem.apiSymbol);
      else {
        setChartApiSymbol(null);
        setSymbol("BHARTIARTL");
        setSearch("BHARTIARTL");
      }
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

  const addWatchSymbol = (rawSymbol?: string) => {
    const nextSymbol = normalizeImportedSymbol(rawSymbol ?? "");
    if (!nextSymbol) return;

    if (watchlist.some((item) => item.symbol === nextSymbol)) {
      selectSymbol(nextSymbol);
      setWatchDialog(null);
      setWatchImportMessage(`${nextSymbol} is already in the watchlist`);
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    if (watchlist.length >= MAX_WATCHLIST_SIZE) {
      setWatchDialog(null);
      setWatchImportMessage("Watchlist limit reached: 1000 symbols");
      window.setTimeout(() => setWatchImportMessage(""), 2500);
      return;
    }

    setWatchlist((current) => [...current, { symbol: nextSymbol, price: "—", change: "—" }]);
    selectSymbol(nextSymbol);
    setWatchDialog(null);
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
  const [savedPipscripts, setSavedPipscripts] = useState<SavedPipscript[]>(loadSavedPipscripts);
  const [selectedSavedPipscriptId, setSelectedSavedPipscriptId] = useState("");
  const pipscriptHasOutputRef = useRef(false);
  const activePipscriptRef = useRef<ActivePipscript | null>(null);
  const pipscriptRunIdRef = useRef(0);
  const pipscriptResultCacheRef = useRef<Map<string, CachedPipscriptResult>>(new Map());
  const PIPSCRIPT_CACHE_TTL_MS = 60_000;

  const runPipscript = async (override?: {
    language?: PipscriptLanguage;
    outputType?: PipscriptOutputType;
    code?: string;
    symbol?: string;
  }) => {
    const executionSymbol = override?.symbol ?? symbol;
    const language = override?.language ?? pipscriptLanguage;
    const outputType = override?.outputType ?? pipscriptOutputType;
    const code = override?.code ?? pipscript;

    // Register the script as the active chart script. The engine derives its
    // dependency scope so future symbol changes can be handled generically.
    const scope = inferPipscriptScope(code);
    activePipscriptRef.current = { language, outputType, code, scope };

    const runId = ++pipscriptRunIdRef.current;
    const cacheKey = pipscriptCacheKey(language, outputType, code, executionSymbol, timeframe);
    const cached = pipscriptResultCacheRef.current.get(cacheKey);
    const cacheIsFresh = cached && Date.now() - cached.cachedAt < PIPSCRIPT_CACHE_TTL_MS;

    if (cacheIsFresh) {
      setPipscriptOutput(cached.output);
      pipscriptHasOutputRef.current = true;
      setPipscriptStatus(
        cached.output.type === "table"
          ? `Table ready · ${cached.output.rows.length} rows · refreshing`
          : `Indicator ready · ${cached.output.points.length} points · refreshing`,
      );
    }

    setPipscriptRunning(true);
    if (!cacheIsFresh) setPipscriptStatus("Preparing Pipscript...");

    try {
      type ChartBar = {
        time: number;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
      };

      const loadCurrentChartData = async (): Promise<ChartBar[]> => {
        const response = await fetch(
          "/api/history?symbol=" + encodeURIComponent(executionSymbol) + "&timeframe=" + encodeURIComponent(timeframe) + "&limit=800",
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("Could not load chart data.");
        const raw = await response.json() as ChartBar[];
        return raw.map((row) => ({
          time: Number(row.time),
          open: Number(row.open),
          high: Number(row.high),
          low: Number(row.low),
          close: Number(row.close),
          volume: Number(row.volume || 0),
        }));
      };

      const fetchGatewayData = async (requests: unknown[]): Promise<unknown> => {
        if (!Array.isArray(requests)) {
          throw new Error("data_requests() must return an array.");
        }
        if (requests.length > 40) {
          throw new Error("A maximum of 40 Pipscript data requests is supported.");
        }

        setPipscriptStatus("Loading requested market data...");
        const response = await fetch("/api/pipscript/data", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify({ requests }),
        });
        const payload = await response.json().catch(() => null) as {
          history?: Record<string, unknown>;
          quotes?: Record<string, unknown>;
          errors?: Array<{ name: string; type: string; message: string }>;
        } | null;
        if (!response.ok) {
          throw new Error(String((payload as { detail?: string } | null)?.detail || "Pipscript data request failed."));
        }
        if (payload?.errors?.length) {
          const names = payload.errors
            .map((error) => error.name)
            .filter(Boolean)
            .join(", ");
          setPipscriptStatus(
            `Market data loaded with ${payload.errors.length} skipped request(s)${names ? `: ${names}` : ""}.`,
          );
        }
        return payload;
      };

      let rawOutput: unknown;

      if (language === "javascript") {
        setPipscriptStatus("Loading JavaScript engine...");
        const runner = new Function(
          "data",
          "SYMBOL",
          `"use strict";
${code}
return {
  requests: typeof data_requests === "function" ? data_requests() : null,
  calculate: typeof calculate === "function" ? calculate : null
};`,
        ) as (data: unknown, symbolValue: string) => {
          requests: unknown[] | null;
          calculate: ((value: unknown) => unknown) | null;
        };

        const program = runner(null, executionSymbol);
        let calculationData: unknown;
        if (program.requests !== null) {
          calculationData = await fetchGatewayData(program.requests);
        } else {
          setPipscriptStatus("Loading chart data...");
          calculationData = await loadCurrentChartData();
        }

        if (typeof program.calculate !== "function") {
          throw new Error("Define calculate(data) in your script.");
        }
        setPipscriptStatus("Running JavaScript...");
        rawOutput = program.calculate(calculationData);
      } else {
        setPipscriptStatus("Loading Python runtime...");
        const pyodide = await loadPyodideRuntime();
        const declarationCode = `import json
SYMBOL = ${JSON.stringify(executionSymbol)}
${code}
_requests = data_requests() if "data_requests" in globals() else None
json.dumps(_requests)`;
        const requestJson = String(await pyodide.runPythonAsync(declarationCode));
        const declaredRequests = JSON.parse(requestJson) as unknown[] | null;

        let calculationData: unknown;
        if (declaredRequests !== null) {
          calculationData = await fetchGatewayData(declaredRequests);
        } else {
          setPipscriptStatus("Loading chart data...");
          calculationData = await loadCurrentChartData();
        }

        setPipscriptStatus("Running Python...");
        const serializedData = JSON.stringify(calculationData);
        const pythonCode = `import json
data = json.loads(${JSON.stringify(serializedData)})
if "calculate" not in globals():
    raise RuntimeError("Define calculate(data) in your script.")
_result = calculate(data)
json.dumps(_result)`;
        rawOutput = JSON.parse(String(await pyodide.runPythonAsync(pythonCode)));
      }

      const normalized = normalizePipscriptOutput(rawOutput, outputType);
      if (runId !== pipscriptRunIdRef.current) return;
      pipscriptResultCacheRef.current.set(cacheKey, {
        output: normalized,
        cachedAt: Date.now(),
      });

      if (runId !== pipscriptRunIdRef.current) return;
      setPipscriptOutput(normalized);
      pipscriptHasOutputRef.current = true;
      setPipscriptStatus(
        normalized.type === "table"
          ? `Table ready · ${normalized.rows.length} rows`
          : `Indicator ready · ${normalized.points.length} points`,
      );
    } catch (error) {
      // A stale request must never erase a newer symbol's result.
      if (runId !== pipscriptRunIdRef.current) return;
      setPipscriptOutput(null);
      setPipscriptStatus(error instanceof Error ? error.message : "PIPScript failed.");
    } finally {
      if (runId === pipscriptRunIdRef.current) {
        setPipscriptRunning(false);
      }
    }
  };

  useEffect(() => {
    const active = activePipscriptRef.current;
    if (!active || active.scope !== "symbol") return;

    void runPipscript({
      language: active.language,
      outputType: active.outputType,
      code: active.code,
      symbol,
    });
  }, [symbol]);

  const savePipscript = () => {
    const value = window.prompt(
      "Save PIPScript as:",
      savedPipscripts.find((item) => item.id === selectedSavedPipscriptId)?.name ?? "",
    );
    const name = String(value ?? "").trim().replace(/\\s+/g, " ").slice(0, 40);
    if (!name) return;

    const existing = savedPipscripts.find((item) => item.name.toLowerCase() === name.toLowerCase());
    if (existing && existing.id !== selectedSavedPipscriptId) {
      if (!window.confirm(`"${name}" already exists. Overwrite it?`)) return;
    }

    const id = (existing?.id ?? selectedSavedPipscriptId) || `pipscript-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const saved: SavedPipscript = {
      id,
      name,
      language: pipscriptLanguage,
      outputType: pipscriptOutputType,
      code: pipscript,
      updatedAt: Date.now(),
    };

    setSavedPipscripts((current) => {
      const next = current.filter((item) => item.id !== id);
      const result = [saved, ...next];
      localStorage.setItem(PIPSCRIPT_STORAGE_KEY, JSON.stringify(result));
      return result;
    });
    setSelectedSavedPipscriptId(id);
    setPipscriptStatus(`Saved "${name}".`);
  };

  const applySavedPipscript = async () => {
    const saved = savedPipscripts.find((item) => item.id === selectedSavedPipscriptId);
    if (!saved) {
      setPipscriptStatus("Select a saved PIPScript first.");
      return;
    }

    setPipscriptLanguage(saved.language);
    setPipscriptOutputType(saved.outputType);
    setPipscript(saved.code);
    setPipscriptStatus(`Applying "${saved.name}"...`);
    await runPipscript({
      language: saved.language,
      outputType: saved.outputType,
      code: saved.code,
    });
  };

  const deleteSavedPipscript = () => {
    const saved = savedPipscripts.find((item) => item.id === selectedSavedPipscriptId);
    if (!saved) {
      setPipscriptStatus("Select a saved PIPScript first.");
      return;
    }
    if (!window.confirm(`Delete saved PIPScript "${saved.name}"?`)) return;

    setSavedPipscripts((current) => {
      const next = current.filter((item) => item.id !== saved.id);
      localStorage.setItem(PIPSCRIPT_STORAGE_KEY, JSON.stringify(next));
      return next;
    });
    setSelectedSavedPipscriptId("");
    setPipscriptStatus(`Deleted "${saved.name}".`);
  };

  const selectSavedPipscript = (id: string) => {
    setSelectedSavedPipscriptId(id);
    const saved = savedPipscripts.find((item) => item.id === id);
    if (saved) {
      setPipscriptStatus(`Selected "${saved.name}". Press APPLY to run it.`);
    }
  };

  const loadPipscript = () => {
    const saved = savedPipscripts.find((item) => item.id === selectedSavedPipscriptId);
    if (saved) {
      setPipscriptLanguage(saved.language);
      setPipscriptOutputType(saved.outputType);
      setPipscript(saved.code);
      setPipscriptStatus(`Loaded "${saved.name}" into the editor.`);
      return;
    }
    setPipscriptStatus("Select a saved PIPScript first.");
  };

  useEffect(() => {
    let cancelled = false;
    let firstLoad = true;

    // Never carry the previous symbol's quote into the new symbol header.
    setQuote(null);
    setQuoteLoading(true);
    setQuoteError(false);

    const loadQuote = async () => {
      try {
        const response = await fetch(
          "/api/quote?symbol=" + encodeURIComponent(chartApiSymbol || symbol),
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
        if (!cancelled && firstLoad) {
          setQuoteLoading(false);
          firstLoad = false;
        }
      }
    };

    void loadQuote();
    const timer = window.setInterval(() => void loadQuote(), 5000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [symbol, chartApiSymbol]);

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
          symbols: watchlist.map((item) => item.apiSymbol || item.symbol),
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

          setLiveQuotes((current) => ({
            ...current,
            [item.symbol]: {
              ...current[item.symbol],
              ...item,
            },
          }));
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
          volume?: number | null;
          bid?: number | null;
          ask?: number | null;
        }>;

        if (cancelled) return;

        setLiveQuotes((current) => {
          const next = { ...current };
          for (const item of data) {
            if (!item.symbol) continue;
            next[item.symbol] = {
              ...next[item.symbol],
              last: Number(item.last),
              change: Number(item.change ?? 0),
              change_percent: Number(item.change_percent ?? 0),
              volume: item.volume == null ? next[item.symbol]?.volume : Number(item.volume),
              bid: item.bid == null ? next[item.symbol]?.bid : Number(item.bid),
              ask: item.ask == null ? next[item.symbol]?.ask : Number(item.ask),
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

  const selectSymbol = (next: string, apiSymbol?: string) => {
    const cleanSymbol = next.trim().toUpperCase();
    if (!cleanSymbol) return;
    setSymbol(cleanSymbol);
    setSearch(cleanSymbol);
    setChartApiSymbol(apiSymbol?.trim().toUpperCase() || null);
    const active = activePipscriptRef.current;
    if (active) {
      void runPipscript({
        language: active.language,
        outputType: active.outputType,
        code: active.code,
        symbol: apiSymbol?.trim().toUpperCase() || cleanSymbol,
      });
    }
  };

  const submitSearch = () => {
    const query = search.trim().toUpperCase();
    const watchMatch = watchlist.find((item) => item.symbol === query);

    if (watchMatch) {
      selectSymbol(watchMatch.symbol, watchMatch.apiSymbol);
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
      { symbol: result.symbol, price: "—", change: "—", apiSymbol: result.api_symbol },
    ]);
    selectSymbol(result.symbol);
    setSearchOpen(false);
    setWatchImportMessage("Added " + result.symbol + " to " + activeWatchlistName);
    window.setTimeout(() => setWatchImportMessage(""), 2500);
  };

  const selectWatchItem = async (item: WatchItem) => {
    if (item.apiSymbol) {
      selectSymbol(item.symbol, item.apiSymbol);
      return;
    }

    selectSymbol(item.symbol);
    try {
      const response = await fetch(
        `/api/symbols/resolve?symbol=${encodeURIComponent(item.symbol)}&timeframe=${encodeURIComponent(timeframe)}&test_history=true&test_quote=false`,
        { cache: "no-store" },
      );
      if (!response.ok) return;
      const payload = await response.json() as { selected?: string | null };
      if (payload.selected) {
        setWatchlists((current) => ({
          ...current,
          [activeWatchlistName]: (current[activeWatchlistName] ?? []).map((watch) =>
            watch.symbol === item.symbol ? { ...watch, apiSymbol: payload.selected!.toUpperCase() } : watch,
          ),
        }));
        selectSymbol(item.symbol, payload.selected);
      }
    } catch {
      // Chart backend fallback remains available when resolution is unavailable.
    }
  };

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = watchWidth;
    const move = (moveEvent: globalThis.PointerEvent) => {
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
        </div>
        <div className="menu-center">PIPSGOX WEB TERMINAL</div>
        <div className="menu-right">
          <button>Account</button>
          <button>Help</button>
          <span className="status-dot" /> {fyersChecking ? "Connecting FYERS..." : "Data: FYERS API V3"}
        </div>
      </div>

      <header className="topbar">
        <button className="top-command">MARKET</button>
        <button className="top-command">WATCHLIST</button>

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

        <div className="top-control-hub" aria-label="Chart and workspace controls">
          <div className="top-control-group top-timeframe-group">
            {(["1m", "3m", "5m", "15m", "30m", "1h", "D", "W", "M"] as Timeframe[]).map((item) => (
              <button
                key={item}
                className={"top-hub-button top-timeframe-button " + (timeframe === item ? "active" : "")}
                onClick={() => setTimeframe(item)}
                aria-label={"Timeframe " + item}
              >
                {item}
              </button>
            ))}
          </div>

          <span className="top-hub-divider" />

          <div className="top-control-group top-chart-type-group">
            {([
              ["candles", "candle", "Candle"],
              ["bars", "bar", "Bar"],
              ["line", "line", "Line"],
            ] as Array<[ChartType, UiIconName, string]>).map(([type, icon, label]) => (
              <button
                key={type}
                className={"top-hub-button top-icon-button " + (chartType === type ? "active" : "")}
                onClick={() => setChartType(type)}
                title={label}
                aria-label={label}
              >
                <UiIcon name={icon} />
              </button>
            ))}
          </div>

          <span className="top-hub-divider" />

          <button
            className={"top-hub-button top-label-button " + (panel === "indicators" ? "active" : "")}
            onClick={() => setPanel("indicators")}
            title="Indicators"
            aria-label="Indicators"
          >
            <UiIcon name="indicator" />
            <span>Indicators</span>
          </button>

          <button
            className={"top-hub-button top-label-button " + (panel === "pipscript" ? "active" : "")}
            onClick={() => setPanel("pipscript")}
            title="Pipscript"
            aria-label="Pipscript"
          >
            <UiIcon name="pipscript" />
            <span>Pipscript</span>
          </button>

          <span className="top-hub-divider" />

          <button
            className="top-hub-button top-label-button"
            onClick={() => setDark((value) => !value)}
            title={dark ? "Switch to light theme" : "Switch to dark theme"}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
          >
            <UiIcon name="sun" />
            <span>{dark ? "Light" : "Dark"}</span>
          </button>

          <button
            className={"top-hub-button top-label-button " + (panel === "settings" ? "active" : "")}
            onClick={() => setPanel("settings")}
            title="Settings"
            aria-label="Settings"
          >
            <UiIcon name="settings" />
            <span>Settings</span>
          </button>
        </div>
      </header>

      <section className="workspace">
        <section className="chart-workspace">
          <div className="chart-container">
            <Chart
              chartType={chartType}
              dark={dark}
              symbol={symbol}
              timeframe={timeframe}
              range={chartRange}
              chartTheme={chartSettings.theme}
              chartColors={chartSettings.colors}
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
                {symbol} · {timeframe} · NSE
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
            {(["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "ALL"] as ChartRange[]).map((preset) => (
              <button
                key={preset}
                className={chartRange === preset ? "active" : ""}
                onClick={() => setChartRange(preset)}
              >
                {preset}
              </button>
            ))}
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
                  const firstItem = watchlists[name]?.[0];
                  if (firstItem) selectSymbol(firstItem.symbol, firstItem.apiSymbol);
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
              <div className="watch-actions" aria-label="Watchlist actions">
                <button onClick={() => setWatchDialog({ type: "add", value: "" })} title="Add symbol" aria-label="Add symbol"><UiIcon name="add" /></button>
                <button onClick={() => setWatchDialog({ type: "import", value: "" })} title="Import watchlist" aria-label="Import watchlist"><UiIcon name="import" /></button>
                <button onClick={() => setWatchDialog({ type: "export", value: "" })} title="Export watchlist" aria-label="Export watchlist"><UiIcon name="export" /></button>
                <button onClick={() => setWatchDialog({ type: "new", value: "" })} title="New watchlist" aria-label="New watchlist"><UiIcon name="new" /></button>
                <button onClick={() => setWatchDialog({ type: "delete", value: "" })} title="Delete watchlist" aria-label="Delete watchlist"><UiIcon name="delete" /></button>
                <button onClick={() => setWatchOpen(false)} title="Hide watchlist" aria-label="Hide watchlist"><UiIcon name="hide" /></button>
              </div>
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
            <div className="watch-columns"><span>SYMBOL</span><span>LAST</span><span>CHANGE %</span><span>VOL</span><span>BID / ASK</span><span></span></div>
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
                  <button className="watch-row-main" onClick={() => void selectWatchItem(item)}>
                    <span className="watch-symbol">{item.symbol}</span>
                    <span className="watch-price">{liveQuotes[item.symbol] ? liveQuotes[item.symbol].last.toFixed(2) : "—"}</span>
                    <span className={`watch-change ${(liveQuotes[item.symbol]?.change_percent ?? Number(item.change.replace("%", ""))) < 0 ? "negative" : "positive"}`}>
                      {liveQuotes[item.symbol] ? `${liveQuotes[item.symbol].change_percent >= 0 ? "+" : ""}${liveQuotes[item.symbol].change_percent.toFixed(2)}%` : "—"}
                    </span>
                    <span className={`watch-volume ${liveQuotes[item.symbol]?.volume != null && liveQuotes[item.symbol].volume <= 10000 ? "low-volume" : ""}`}>
                      {formatWatchVolume(liveQuotes[item.symbol]?.volume)}
                    </span>
                    <span className="watch-bid-ask">
                      <span className="watch-bid-value">{formatWatchQuoteValue(liveQuotes[item.symbol]?.bid)}</span>
                      <span className="watch-bid-ask-separator"> / </span>
                      <span className="watch-ask-value">{formatWatchQuoteValue(liveQuotes[item.symbol]?.ask)}</span>
                    </span>
                  </button>
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

      {watchDialog && (
        <div className="watch-dialog-backdrop" onClick={() => setWatchDialog(null)}>
          <section className="watch-dialog" onClick={(event) => event.stopPropagation()}>
            <strong className="watch-dialog-title">
              {watchDialog.type === "add" ? "Add symbol" : watchDialog.type === "new" ? "New watchlist" : watchDialog.type === "delete" ? "Delete watchlist" : watchDialog.type === "export" ? "Export watchlist" : "Import watchlist"}
            </strong>
            <p className="watch-dialog-subtitle">
              {watchDialog.type === "add" ? "Enter an NSE symbol to add." : watchDialog.type === "new" ? "Create a new watchlist." : watchDialog.type === "delete" ? `Delete “${activeWatchlistName}” and its symbols?` : watchDialog.type === "export" ? `Export ${watchlist.length} symbols as CSV.` : "Choose a CSV or text watchlist file."}
            </p>
            {(watchDialog.type === "add" || watchDialog.type === "new") && (
              <input
                autoFocus
                className="watch-dialog-input"
                value={watchDialog.value}
                placeholder={watchDialog.type === "add" ? "e.g. RELIANCE" : "Watchlist name"}
                onChange={(event) => setWatchDialog((current) => current ? { ...current, value: event.target.value } : current)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setWatchDialog(null);
                  if (event.key === "Enter") watchDialog.type === "add" ? addWatchSymbol(watchDialog.value) : createWatchlist(watchDialog.value);
                }}
              />
            )}
            <div className="watch-dialog-actions">
              <button className="watch-dialog-cancel" onClick={() => setWatchDialog(null)}>Cancel</button>
              {watchDialog.type === "delete" ? (
                <button className="watch-dialog-danger" onClick={deleteWatchlist}>Delete</button>
              ) : watchDialog.type === "export" ? (
                <button className="watch-dialog-primary" onClick={exportWatchlist}>Export</button>
              ) : watchDialog.type === "import" ? (
                <button className="watch-dialog-primary" onClick={() => watchImportRef.current?.click()}>Choose file</button>
              ) : (
                <button className="watch-dialog-primary" onClick={() => watchDialog.type === "add" ? addWatchSymbol(watchDialog.value) : createWatchlist(watchDialog.value)}>
                  {watchDialog.type === "add" ? "Add" : "Create"}
                </button>
              )}
            </div>
          </section>
        </div>
      )}

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
                    className="pipscript-saved-select"
                    value={selectedSavedPipscriptId}
                    onChange={(event) => selectSavedPipscript(event.target.value)}
                    aria-label="Saved PIPScripts"
                  >
                    <option value="">Saved PIPScripts...</option>
                    {savedPipscripts.map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </select>
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
                  <button onClick={loadPipscript} disabled={!selectedSavedPipscriptId}>LOAD</button>
                  <button onClick={savePipscript}>SAVE</button>
                  <button onClick={() => void applySavedPipscript()} disabled={!selectedSavedPipscriptId || pipscriptRunning}>
                    APPLY
                  </button>
                  <button onClick={deleteSavedPipscript} disabled={!selectedSavedPipscriptId}>DELETE</button>
                  <button className="builder-run" onClick={() => void runPipscript()} disabled={pipscriptRunning}>
                    {pipscriptRunning ? "RUNNING..." : "RUN"}
                  </button>
                </div>
                <div className="builder-hint">
                  <code>calculate(data)</code> receives the current chart candles by default. Add <code>data_requests()</code> to fetch multiple symbols/timeframes through the PIPSGOX Data Gateway. Saved scripts are stored in this browser. <b>APPLY</b> loads and runs the selected script; <b>DELETE</b> removes it.
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
                  <div className="settings-section-title">CHART THEME</div>
                  <label className="settings-field">
                    <span>Preset</span>
                    <select value={chartSettings.theme} onChange={(event) => applyChartPreset(event.target.value as ChartTheme)}>
                      <option value="pipsgox">PIPSGOX Dark</option>
                      <option value="classic">Classic Dark</option>
                      <option value="light">Clean Light</option>
                    </select>
                  </label>
                  <div className="settings-theme-save">
                    <select value="" onChange={(event) => loadChartTheme(event.target.value)}>
                      <option value="">Load saved theme</option>
                      {Object.keys(savedChartThemes).map((name) => <option key={name} value={name}>{name}</option>)}
                    </select>
                    <input value={chartThemeName} onChange={(event) => setChartThemeName(event.target.value)} placeholder="Theme name" maxLength={24} />
                    <button onClick={saveChartTheme}>SAVE</button>
                  </div>
                </div>
                <div className="settings-section">
                  <div className="settings-section-title">COLORS</div>
                  <div className="settings-color-grid">
                    {([
                      ["background", "Background"], ["grid", "Grid"], ["axis", "Price / date axis"],
                      ["candleUp", "Bullish candle"], ["candleDown", "Bearish candle"],
                      ["volumeUp", "Volume up"], ["volumeDown", "Volume down"],
                      ["ma50", "MA 50"], ["ma200", "MA 200"],
                    ] as Array<[keyof ChartColors, string]>).map(([key, label]) => (
                      <label className="settings-color-field" key={key}>
                        <span>{label}</span>
                        <span className="settings-color-control">
                          <input type="color" value={chartSettings.colors[key]} onChange={(event) => updateChartColor(key, event.target.value)} />
                          <code>{chartSettings.colors[key].toUpperCase()}</code>
                        </span>
                      </label>
                    ))}
                  </div>
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

ReactDOM.createRoot(document.getElementById("root")!).render(
  window.location.pathname === "/dev" ? <DevConsole /> : <App />,
);