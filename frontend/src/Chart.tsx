import { useEffect, useRef, useState } from "react";
import { dispose, init, registerIndicator, type Chart as KLineChartInstance, type KLineData } from "klinecharts";

export type ChartType = "candles" | "bars" | "line";
export type Timeframe = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "D" | "W" | "M";

export type ChartTheme = "pipsgox" | "classic" | "light";

export type PipscriptOutput =
  | {
      type: "line";
      name: string;
      points: Array<{ time: number; value: number }>;
    }
  | {
      type: "table";
      title: string;
      columns: string[];
      rows: string[][];
      lines?: Array<{
        name: string;
        points: Array<{ time: number; value: number }>;
      }>;
    };

type Props = {
  chartType: ChartType;
  dark: boolean;
  symbol: string;
  timeframe: Timeframe;
  chartTheme: ChartTheme;
  showGrid: boolean;
  showCrosshair: boolean;
  showVolume: boolean;
  showVwap: boolean;
  show52WeekHigh: boolean;
  show52WeekLow: boolean;
  showPreviousClose: boolean;
  previousClose?: number;
  pipscriptOutput?: PipscriptOutput | null;
};

type HistoryCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type HistoryCacheEntry = {
  storedAt: number;
  bars: KLineData[];
};

const historyCache = new Map<string, HistoryCacheEntry>();
const historyInflight = new Map<string, Promise<KLineData[]>>();

const PIPSGOX_VOLUME_BARS = "PIPSGOX_VOLUME_BARS";
const PIPSGOX_PIPSCRIPT_EMA = "PIPSGOX_PIPSCRIPT_EMA";

registerIndicator({
  name: PIPSGOX_PIPSCRIPT_EMA,
  shortName: "PIPSGOX EMA",
  series: "price",
  calcParams: [9, 20, 50, 100, 200],
  precision: 2,
  shouldOhlc: false,
  figures: [
    { key: "ema1", title: "EMA9: ", type: "line" },
    { key: "ema2", title: "EMA20: ", type: "line" },
    { key: "ema3", title: "EMA50: ", type: "line" },
    { key: "ema4", title: "EMA100: ", type: "line" },
    { key: "ema5", title: "EMA200: ", type: "line" },
  ],
  regenerateFigures: (params) =>
    params.map((period, index) => ({
      key: "ema" + (index + 1),
      title: "EMA" + period + ": ",
      type: "line",
    })),
  calc: (dataList: KLineData[], indicator: any) => {
    const params = (indicator.calcParams || [9, 20, 50, 100, 200]).map(Number);
    const states = params.map(() => ({ value: 0, initialized: false }));
    const result: Record<number, Record<string, number | null>> = {};

    for (let i = 0; i < dataList.length; i += 1) {
      const row: Record<string, number | null> = {};

      params.forEach((period, index) => {
        if (i < period - 1) {
          row["ema" + (index + 1)] = null;
          return;
        }

        const state = states[index];
        const close = Number(dataList[i].close);

        if (!state.initialized) {
          let sum = 0;
          for (let j = i - period + 1; j <= i; j += 1) {
            sum += Number(dataList[j].close);
          }
          state.value = sum / period;
          state.initialized = true;
        } else {
          const multiplier = 2 / (period + 1);
          state.value = (close - state.value) * multiplier + state.value;
        }

        row["ema" + (index + 1)] = Number.isFinite(state.value) ? state.value : null;
      });

      result[dataList[i].timestamp] = row;
    }

    return result;
  },
});

function historyCacheTtl(timeframe: Timeframe) {
  return timeframe === "D" || timeframe === "W" || timeframe === "M" ? 5 * 60_000 : 15_000;
}

type CrosshairData = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change: number;
  changePercent: number;
  ma50?: number;
  ma200?: number;
};

function movingAverage(data: Array<{ close: number }>, endIndex: number, period: number) {
  const start = endIndex - period + 1;
  if (start < 0) return undefined;
  let sum = 0;
  for (let index = start; index <= endIndex; index += 1) sum += Number(data[index].close);
  return Number.isFinite(sum) ? sum / period : undefined;
}

function formatCrosshairDate(timestamp: number, timeframe: Timeframe) {
  const date = new Date(timestamp);
  if (timeframe === "D" || timeframe === "W" || timeframe === "M") {
    return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(date);
  }
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function formatNumber(value: number | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function parseCrosshairEvent(event: unknown) {
  if (!event || typeof event !== "object") return {};
  const value = event as Record<string, unknown>;
  const nested = value.data && typeof value.data === "object" ? value.data as Record<string, unknown> : {};
  const point = value.point && typeof value.point === "object" ? value.point as Record<string, unknown> : {};
  const readNumber = (...keys: string[]) => {
    for (const key of keys) {
      const raw = value[key] ?? nested[key] ?? point[key];
      if (raw == null) continue;
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) return parsed;
    }
    return undefined;
  };
  return {
    dataIndex: readNumber("dataIndex", "index"),
    timestamp: readNumber("timestamp", "time"),
    open: readNumber("open"),
    high: readNumber("high"),
    low: readNumber("low"),
    close: readNumber("close"),
    volume: readNumber("volume"),
  };
}

function getPeriod(timeframe: Timeframe) {
  switch (timeframe) {
    case "1m": return { span: 1, type: "minute" as const };
    case "3m": return { span: 3, type: "minute" as const };
    case "5m": return { span: 5, type: "minute" as const };
    case "15m": return { span: 15, type: "minute" as const };
    case "30m": return { span: 30, type: "minute" as const };
    case "1h": return { span: 1, type: "hour" as const };
    case "D": return { span: 1, type: "day" as const };
    case "W": return { span: 1, type: "week" as const };
    case "M": return { span: 1, type: "month" as const };
  }
}

function getLimit(timeframe: Timeframe) {
  if (timeframe === "D") return 800;
  if (timeframe === "W") return 400;
  if (timeframe === "M") return 240;
  return 500;
}

function dateBeforeTimestamp(timestamp: number) {
  const date = new Date(timestamp);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

export function Chart({
  chartType,
  dark,
  symbol,
  timeframe,
  chartTheme,
  showGrid,
  showCrosshair,
  showVolume,
  showVwap,
  show52WeekHigh,
  show52WeekLow,
  showPreviousClose,
  previousClose,
  pipscriptOutput,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [crosshairData, setCrosshairData] = useState<CrosshairData | null>(null);
  const chartRef = useRef<KLineChartInstance | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let disposed = false;

    try {
      const chart = init(container);
      if (!chart) {
        throw new Error("KLineChart failed to initialize.");
      }
      chartRef.current = chart;

      chart.setSymbol({
        ticker: symbol,
        pricePrecision: 2,
        volumePrecision: 0,
      });
      chart.setPeriod(getPeriod(timeframe));
      setCrosshairData(null);

      const palette = chartTheme === "light"
        ? {
            background: "#ffffff",
            grid: "#e5e7eb",
            text: "#4b5563",
            axis: "#9ca3af",
            crosshair: "#94a3b8",
            up: "#168a59",
            down: "#c93643",
          }
        : chartTheme === "classic"
          ? {
              background: "#101317",
              grid: "#28303a",
              text: "#aeb7c2",
              axis: "#697482",
              crosshair: "#7c8794",
              up: "#26a69a",
              down: "#ef5350",
            }
          : {
              background: "#090909",
              grid: "#202020",
              text: "#9aa3ad",
              axis: "#626b75",
              crosshair: "#707b87",
              up: "#12d98b",
              down: "#ff4d5a",
            };

      chart.setStyles({
        grid: {
          show: showGrid,
          horizontal: { show: showGrid, color: palette.grid, size: 1, style: "dashed", dashedValue: [2, 2] },
          vertical: { show: showGrid, color: palette.grid, size: 1, style: "dashed", dashedValue: [2, 2] },
        },
        candle: {
          tooltip: {
            showRule: "none",
          },
        },
        indicator: {
          tooltip: {
            showRule: "none",
          },
        },
        crosshair: {
          show: showCrosshair,
          horizontal: { show: showCrosshair, line: { show: showCrosshair, color: palette.crosshair, size: 1, style: "dashed", dashedValue: [4, 2] } },
          vertical: { show: showCrosshair, line: { show: showCrosshair, color: palette.crosshair, size: 1, style: "dashed", dashedValue: [4, 2] } },
        },
        xAxis: {
          tickText: { color: palette.text, size: 9 },
          axisLine: { color: palette.axis },
          tickLine: { color: palette.axis },
        },
        yAxis: {
          tickText: { color: palette.text, size: 9 },
          axisLine: { color: palette.axis },
          tickLine: { color: palette.axis },
        },
      });

      container.style.background = palette.background;

      const crosshairHandler = (event: unknown) => {
        const parsed = parseCrosshairEvent(event);
        const dataList = chart.getDataList();
        if (!dataList.length) {
          setCrosshairData(null);
          return;
        }
        let index = parsed.dataIndex != null ? Math.round(parsed.dataIndex) : -1;
        if (index < 0 && parsed.timestamp != null) {
          const eventTimestamp = parsed.timestamp < 100000000000 ? parsed.timestamp * 1000 : parsed.timestamp;
          let bestIndex = -1;
          let bestDistance = Number.POSITIVE_INFINITY;
          dataList.forEach((item, itemIndex) => {
            const distance = Math.abs(item.timestamp - eventTimestamp);
            if (distance < bestDistance) { bestDistance = distance; bestIndex = itemIndex; }
          });
          if (bestIndex >= 0 && bestDistance <= 3 * 24 * 60 * 60 * 1000) index = bestIndex;
        }
        if (index < 0 || index >= dataList.length) {
          setCrosshairData(null);
          return;
        }
        const candle = dataList[index];
        const previous = index > 0 ? dataList[index - 1] : undefined;
        const open = parsed.open ?? candle.open;
        const high = parsed.high ?? candle.high;
        const low = parsed.low ?? candle.low;
        const close = parsed.close ?? candle.close;
        const volume = parsed.volume ?? Number(candle.volume ?? 0);
        if (![open, high, low, close].every((value) => Number.isFinite(value))) {
          setCrosshairData(null);
          return;
        }
        const change = previous ? close - previous.close : 0;
        const changePercent = previous && previous.close !== 0 ? (change / previous.close) * 100 : 0;
        setCrosshairData({
          timestamp: candle.timestamp,
          open, high, low, close, volume,
          change, changePercent,
          ma50: movingAverage(dataList, index, 50),
          ma200: movingAverage(dataList, index, 200),
        });
      };

      chart.subscribeAction("onCrosshairChange", crosshairHandler);

      chart.setDataLoader({
        getBars: async ({ type, timestamp, callback }) => {
          try {
            const pageSize = getLimit(timeframe);
            const params = new URLSearchParams({
              symbol,
              timeframe,
              limit: String(pageSize),
            });

            // KLineChart calls "forward" when the user reaches the left
            // boundary. In that direction we ask FYERS for candles strictly
            // older than the current leftmost candle.
            if (type === "forward" && timestamp) {
              params.set("to_date", dateBeforeTimestamp(Number(timestamp)));
            }

            const url = `/api/history?${params.toString()}`;
            const cacheKey = url;
            const cached = historyCache.get(cacheKey);
            const now = Date.now();

            const cacheFresh = cached && now - cached.storedAt < historyCacheTtl(timeframe);
            if (cached && !disposed) {
              callback(cached.bars, {
                forward: type === "backward" ? false : cached.bars.length >= pageSize,
                backward: false,
              });
            }
            if (cacheFresh) return;

            let barsPromise = historyInflight.get(cacheKey);
            if (!barsPromise) {
              console.log("PIPSGOX history request:", url);
              barsPromise = fetch(url, { cache: "no-store" }).then(async (response) => {
                if (!response.ok) throw new Error("History HTTP " + response.status);
                return response.json() as Promise<HistoryCandle[]>;
              }).then((raw) => {
                const bars = raw
                  .map((item) => ({
                    timestamp: Number(item.time) * 1000,
                    open: Number(item.open),
                    high: Number(item.high),
                    low: Number(item.low),
                    close: Number(item.close),
                    volume: Number(item.volume || 0),
                  }))
                  .filter((item) =>
                    Number.isFinite(item.timestamp) &&
                    Number.isFinite(item.open) &&
                    Number.isFinite(item.high) &&
                    Number.isFinite(item.low) &&
                    Number.isFinite(item.close),
                  )
                  .sort((a, b) => a.timestamp - b.timestamp);
                historyCache.set(cacheKey, { storedAt: Date.now(), bars });
                return bars;
              }).finally(() => historyInflight.delete(cacheKey));
              historyInflight.set(cacheKey, barsPromise);
            }

            const bars = await barsPromise;

            if (disposed) return;

            console.log(
              "PIPSGOX history bars:",
              bars.length,
              "direction:",
              type,
              "oldest:",
              bars.length ? new Date(bars[0].timestamp).toISOString() : "none",
            );

            callback(bars, {
              forward: type === "backward" ? false : bars.length >= pageSize,
              backward: false,
            });
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") return;
            console.error("PIPSGOX history error:", error);
            if (!disposed) {
              callback([], {
                forward: false,
                backward: false,
              });
            }
          }
        },
      });

      if (chartType === "bars") {
        chart.setStyles({
          candle: {
            type: "ohlc",
          },
        });
      } else if (chartType === "line") {
        chart.setStyles({
          candle: {
            type: "area",
            area: {
              lineColor: chartTheme === "light" ? "#1976d2" : chartTheme === "classic" ? "#42a5f5" : "#38bdf8",
              lineSize: 2,
              backgroundColor: [
                { offset: 0, color: chartTheme === "light" ? "rgba(25,118,210,0.10)" : chartTheme === "classic" ? "rgba(66,165,245,0.10)" : "rgba(56,189,248,0.12)" },
                { offset: 1, color: "rgba(0,0,0,0)" },
              ],
            },
          },
        });
      } else {
        chart.setStyles({
          candle: {
            type: "candle_solid",
            bar: {
              upColor: palette.up,
              downColor: palette.down,
              noChangeColor: "#8d9aaa",
              upBorderColor: palette.up,
              downBorderColor: palette.down,
              noChangeBorderColor: "#8d9aaa",
              upWickColor: palette.up,
              downWickColor: palette.down,
              noChangeWickColor: "#8d9aaa",
            },
          },
        });
      }

      // Base indicators: native MA stays on the candle pane.
      chart.createIndicator(
        {
          name: "MA",
          id: "pipsgox-default-ma",
          paneId: "candle_pane",
          series: "price",
          calcParams: [50, 200],
          visible: true,
          styles: {
            lines: [
              { style: "solid", color: "#f6c85f", size: 1 },
              { style: "solid", color: "#b07cff", size: 1 },
            ],
          },
        },
        true,
      );

      // Volume is a bars-only indicator. No volume MA lines are calculated.
      if (showVolume) {
        chart.createIndicator(
          {
            name: "VOL",
            id: "pipsgox-default-volume",
            paneId: "volume_pane",
            series: "volume",
            calcParams: [],
            visible: true,
            styles: {
              bars: [{
                style: "fill",
                borderStyle: "solid",
                borderSize: 0,
                upColor: "#d9dde3",
                downColor: "#d9dde3",
                noChangeColor: "#d9dde3",
              }],
              lines: [],
            },
          },
          false,
        );
        chart.setPaneOptions({
          id: "volume_pane",
          height: 72,
          minHeight: 60,
          dragEnabled: false,
          order: 20,
        });
      }

      // Optional chart overlays. They are deliberately opt-in so the
      // locked base chart stays clean until the user selects an overlay.
      const createPriceLine = (id: string, value: number, color: string) => {
        if (!Number.isFinite(value)) return;
        chart.createOverlay({
          name: "priceLine",
          id,
          points: [{ timestamp: Date.now(), value }],
          lock: true,
          needDefaultPointFigure: false,
          needDefaultXAxisFigure: false,
          needDefaultYAxisFigure: true,
          styles: {
            line: {
              color,
              size: 1,
              style: "dashed",
              dashedValue: [4, 3],
            },
          },
        });
      };

      if (showPreviousClose && previousClose != null && Number.isFinite(previousClose)) {
        createPriceLine("pipsgox-previous-close", previousClose, "#7d8792");
      }

      if (showVwap && timeframe !== "D" && timeframe !== "W" && timeframe !== "M") {
        chart.createIndicator({
          name: "PIPSGOX_VWAP",
          shortName: "VWAP",
          paneId: "candle_pane",
          series: "price",
          shouldOhlc: false,
          figures: [{ key: "vwap", title: "VWAP: ", type: "line" }],
          styles: {
            lines: [{
              style: "solid",
              color: "#d6a84f",
              size: 1,
            }],
          },
          calc: (dataList: KLineData[]) => {
            let sessionKey = "";
            let cumulativeVolume = 0;
            let cumulativeTurnover = 0;
            const result: Record<number, { vwap: number | null }> = {};

            for (const candle of dataList) {
              const key = new Date(candle.timestamp).toDateString();
              if (key !== sessionKey) {
                sessionKey = key;
                cumulativeVolume = 0;
                cumulativeTurnover = 0;
              }

              const volume = Number(candle.volume ?? 0);
              const typicalPrice = (candle.high + candle.low + candle.close) / 3;
              cumulativeVolume += volume;
              cumulativeTurnover += typicalPrice * volume;

              result[candle.timestamp] = {
                vwap: cumulativeVolume > 0 ? cumulativeTurnover / cumulativeVolume : null,
              };
            }

            return result;
          },
        } as any);
      }

      if (show52WeekHigh || show52WeekLow) {
        void (async () => {
          try {
            const response = await fetch(
              "/api/history?symbol=" + encodeURIComponent(symbol) + "&timeframe=D&limit=400",
              { cache: "no-store" },
            );
            if (!response.ok) return;

            const raw = (await response.json()) as HistoryCandle[];
            if (disposed) return;

            const daily = raw.filter((item) =>
              Number.isFinite(Number(item.high)) &&
              Number.isFinite(Number(item.low)),
            );
            if (!daily.length) return;

            const recent = daily.slice(-260);
            const high52 = Math.max(...recent.map((item) => Number(item.high)));
            const low52 = Math.min(...recent.map((item) => Number(item.low)));

            if (show52WeekHigh && Number.isFinite(high52)) {
              createPriceLine("pipsgox-52w-high", high52, "#b26cff");
            }
            if (show52WeekLow && Number.isFinite(low52)) {
              createPriceLine("pipsgox-52w-low", low52, "#5ca8ff");
            }
          } catch (error) {
            console.warn("PIPSGOX optional overlay data error:", error);
          }
        })();
      }



      const resizeObserver = new ResizeObserver(() => {
        chart.resize();
      });
      resizeObserver.observe(container);

      return () => {
        disposed = true;
        resizeObserver.disconnect();
        chart.unsubscribeAction("onCrosshairChange", crosshairHandler);
        chartRef.current = null;
        dispose(chart);
      };
    } catch (error) {
      console.error("PIPSGOX chart initialization error:", error);
      return () => {
        chartRef.current = null;
      };
    }
  }, [
    chartType,
    dark,
    symbol,
    timeframe,
    chartTheme,
    showGrid,
    showCrosshair,
    showVolume,
    showVwap,
    show52WeekHigh,
    show52WeekLow,
    showPreviousClose,
    previousClose,
  ]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !pipscriptOutput) return;

    const lines = pipscriptOutput.type === "line"
      ? [{
          name: pipscriptOutput.name || "PIPScript",
          points: pipscriptOutput.points,
        }]
      : (pipscriptOutput.lines || []);

    if (!lines.length) return;

    const validLines = lines
      .map((line) => ({
        name: String(line.name || "PIPScript"),
        points: line.points.filter(
          (point) => Number.isFinite(Number(point.time)) && Number.isFinite(Number(point.value)),
        ),
      }))
      .filter((line) => line.points.length);

    if (!validLines.length) return;

    // EMA lines are rendered by KLineCharts' native EMA engine. This is
    // deliberately independent of the other Pipscript lines: a table
    // script may return EMA + 52W High/Low together.
    const emaLengths = validLines
      .map((line) => {
        const match = line.name.match(/^EMA\s+(\d+)$/i);
        return match ? Number(match[1]) : null;
      })
      .filter((value): value is number => value != null)
      .filter((value, index, values) => values.indexOf(value) === index);

    const indicatorIds: string[] = [];

    if (emaLengths.length) {
      const emaColors = ["#f6c85f", "#12d98b", "#6d9eeb", "#ef5350", "#c9daf8"];

      for (let index = 0; index < emaLengths.length; index += 1) {
        const period = emaLengths[index];
        const emaIndicatorId = chart.createIndicator(
          {
            name: "EMA",
            id: "pipsgox-pipscript-ema-" + period,
            paneId: "candle_pane",
            series: "price",
            calcParams: [period],
            visible: true,
            styles: {
              lines: [{
                style: "solid",
                color: emaColors[index % emaColors.length],
                size: 1,
              }],
            },
          },
          true,
        );

        if (emaIndicatorId) indicatorIds.push(emaIndicatorId);
      }
    }

    // Keep support for non-EMA Pipscript lines such as 52W High/Low.
    const nonEmaLines = validLines.filter(
      (line) => !/^EMA\s+\d+$/i.test(line.name),
    );

    if (nonEmaLines.length) {
      const valuesByLine = nonEmaLines.map((line) => new Map(
        line.points.map((point) => [Number(point.time) * 1000, Number(point.value)]),
      ));

      const palette = [
        "#d6a84f",
        "#ef5350",
        "#12d98b",
        "#c9daf8",
        "#6d9eeb",
        "#3c78d8",
      ];

      const figures = nonEmaLines.map((line, index) => ({
        key: `line${index}`,
        title: line.name + ": ",
        type: "line",
      }));

      const indicatorId = chart.createIndicator({
        name: "PIPSGOX_SCRIPT_LINES",
        shortName: nonEmaLines.map((line) => line.name).join(" / ").slice(0, 40),
        paneId: "candle_pane",
        series: "price",
        shouldOhlc: false,
        figures,
        styles: {
          lines: nonEmaLines.map((_, index) => ({
            style: "solid",
            color: palette[index % palette.length],
            size: 1,
          })),
        },
        calc: (dataList: KLineData[]) => {
          const result: Record<number, Record<string, number | null>> = {};

          for (const candle of dataList) {
            const row: Record<string, number | null> = {};

            valuesByLine.forEach((values, index) => {
              const value = values.get(candle.timestamp);
              row[`line${index}`] =
                value != null && Number.isFinite(value) ? value : null;
            });

            result[candle.timestamp] = row;
          }

          return result;
        },
      } as any);

      if (indicatorId) indicatorIds.push(indicatorId);
    }

    return () => {
      for (const indicatorId of indicatorIds) {
        try {
          chart.removeIndicator(indicatorId);
        } catch {
          // Chart may already be disposed/recreated.
        }
      }
    };
  }, [pipscriptOutput, symbol, timeframe]);

  return (
    <div className="chart-stage">
      <div ref={containerRef} className="chart-canvas" />
      {pipscriptOutput?.type === "table" && (
        <div className="pipscript-table-overlay">
          <div className="pipscript-table-title">{pipscriptOutput.title}</div>
          <table>
            <thead>
              <tr>{pipscriptOutput.columns.map((column) => <th key={column}>{column}</th>)}</tr>
            </thead>
            <tbody>
              {pipscriptOutput.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {pipscriptOutput.columns.map((column, columnIndex) => {
                    const value = row[columnIndex] ?? "—";
                    const numericValue = Number(value);
                    const isRsCell =
                      pipscriptOutput.title.toUpperCase().includes("RS") &&
                      column.toUpperCase() === "RS" &&
                      Number.isFinite(numericValue);

                    const isPricePositionCell =
                      pipscriptOutput.title.toUpperCase().includes("PRICE POSITION") &&
                      column.toUpperCase().includes("DIST") &&
                      Number.isFinite(numericValue);

                    let className = "";
                    if (isRsCell) {
                      if (numericValue >= 90) className = "rs-90";
                      else if (numericValue >= 80) className = "rs-80";
                      else if (numericValue >= 70) className = "rs-70";
                      else if (numericValue >= 60) className = "rs-60";
                      else if (numericValue >= 50) className = "rs-50";
                      else className = "rs-low";
                    } else if (isPricePositionCell) {
                      const columnName = column.toUpperCase();
                      if (columnName.includes("52W HIGH") || columnName.includes("ATH")) {
                        if (numericValue >= -5) className = "price-strong";
                        else if (numericValue >= -10) className = "price-watch";
                        else className = "price-weak";
                      } else if (columnName.includes("52W LOW")) {
                        if (numericValue >= 50) className = "price-strong";
                        else if (numericValue >= 20) className = "price-watch";
                        else className = "price-weak";
                      }
                    }

                    return (
                      <td key={columnIndex} className={className}>
                        {value}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {crosshairData && (
        <div className="chart-crosshair-data" aria-live="polite">
          <span className="chart-crosshair-date">{formatCrosshairDate(crosshairData.timestamp, timeframe)}</span>
          <span>O <b>{formatNumber(crosshairData.open)}</b></span>
          <span>H <b>{formatNumber(crosshairData.high)}</b></span>
          <span>L <b>{formatNumber(crosshairData.low)}</b></span>
          <span>C <b>{formatNumber(crosshairData.close)}</b></span>
          <span className={crosshairData.change < 0 ? "negative" : "positive"}>
            {crosshairData.change >= 0 ? "+" : ""}{formatNumber(crosshairData.change)} ({crosshairData.changePercent >= 0 ? "+" : ""}{formatNumber(crosshairData.changePercent)}%)
          </span>
          <span>Vol <b>{formatNumber(crosshairData.volume, 0)}</b></span>
          {crosshairData.ma50 != null && <span>MA50 <b>{formatNumber(crosshairData.ma50)}</b></span>}
          {crosshairData.ma200 != null && <span>MA200 <b>{formatNumber(crosshairData.ma200)}</b></span>}
        </div>
      )}
      {showVolume && <div className="chart-pane-label volume-pane-label">VOLUME</div>}
    </div>
  );
}