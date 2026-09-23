import { useEffect, useRef, useState } from "react";
import { dispose, init, type Chart as KLineChartInstance } from "klinecharts";

export type ChartType = "candles" | "bars" | "line";
export type Timeframe = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "D" | "W" | "M";

export type ChartTheme = "pipsgox" | "classic" | "light";

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
  showHistoricalPe: boolean;
  previousClose?: number;
};

type HistoryCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

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

type HistoricalPePoint = {
  time: number;
  value: number;
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
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) return parsed;
    }
    return undefined;
  };
  return { dataIndex: readNumber("dataIndex"), timestamp: readNumber("timestamp") };
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
  showHistoricalPe,
  previousClose,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [crosshairData, setCrosshairData] = useState<CrosshairData | null>(null);
  const [historicalPe, setHistoricalPe] = useState<HistoricalPePoint[]>([]);
  const [historicalPeStatus, setHistoricalPeStatus] = useState<"idle" | "loading" | "ready" | "unavailable">("idle");
  const chartRef = useRef<KLineChartInstance | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let disposed = false;

    try {
      const chart = init(container);
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
          tickText: { color: palette.text },
          axisLine: { color: palette.axis },
          tickLine: { color: palette.axis },
        },
        yAxis: {
          tickText: { color: palette.text },
          axisLine: { color: palette.axis },
          tickLine: { color: palette.axis },
        },
      });

      container.style.background = palette.background;

      const crosshairHandler = (event: unknown) => {
        const parsed = parseCrosshairEvent(event);
        const dataList = chart.getDataList();
        let index = parsed.dataIndex != null ? Math.round(parsed.dataIndex) : -1;
        if (index < 0 && parsed.timestamp != null && dataList.length) {
          let bestIndex = 0;
          let bestDistance = Number.POSITIVE_INFINITY;
          dataList.forEach((item, itemIndex) => {
            const distance = Math.abs(item.timestamp - parsed.timestamp!);
            if (distance < bestDistance) { bestDistance = distance; bestIndex = itemIndex; }
          });
          index = bestIndex;
        }
        if (index < 0 || index >= dataList.length) { setCrosshairData(null); return; }
        const candle = dataList[index];
        const previous = index > 0 ? dataList[index - 1] : undefined;
        const change = previous ? candle.close - previous.close : 0;
        const changePercent = previous && previous.close !== 0 ? (change / previous.close) * 100 : 0;
        setCrosshairData({
          timestamp: candle.timestamp,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: Number(candle.volume ?? 0),
          change,
          changePercent,
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
            console.log("PIPSGOX history request:", url);

            const response = await fetch(url, { cache: "no-store" });
            if (!response.ok) {
              throw new Error(`History HTTP ${response.status}`);
            }

            const raw = (await response.json()) as HistoryCandle[];

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

      chart.createIndicator(
        { name: "MA", paneId: "candle_pane", calcParams: [50, 200] },
        true,
      );
      if (showVolume) {
        chart.createIndicator({
          name: "VOL",
          paneId: "volume_pane",
          series: "volume",
          calcParams: [],
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
        });
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
          calc: (dataList) => {
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
        });
      }

      if (showHistoricalPe) {
        setHistoricalPeStatus("loading");
        void (async () => {
          try {
            const response = await fetch("/api/fundamentals/pe?symbol=" + encodeURIComponent(symbol) + "&limit=40", { cache: "no-store" });
            if (!response.ok) throw new Error("Historical P/E unavailable");
            const raw = (await response.json()) as HistoricalPePoint[];
            const points = raw.map((item) => ({ time: Number(item.time), value: Number(item.value) }))
              .filter((item) => Number.isFinite(item.time) && Number.isFinite(item.value) && item.value > 0)
              .sort((a, b) => a.time - b.time);
            if (disposed) return;
            setHistoricalPe(points);
            setHistoricalPeStatus(points.length ? "ready" : "unavailable");
            if (!points.length) return;
            const latestByDate = new Map(points.map((point) => [new Date(point.time * 1000).toISOString().slice(0, 10), point.value]));
            chart.createIndicator({
              name: "PIPSGOX_PE",
              shortName: "P/E",
              paneId: "pe_pane",
              series: "normal",
              precision: 2,
              shouldOhlc: false,
              figures: [{ key: "pe", title: "P/E: ", type: "line" }],
              styles: { lines: [{ style: "solid", color: "#c9a15a", size: 1 }] },
              calc: (dataList) => {
                const result: Record<number, { pe: number | null }> = {};
                let latest: number | null = null;
                for (const candle of dataList) {
                  const exact = latestByDate.get(new Date(candle.timestamp).toISOString().slice(0, 10));
                  if (exact != null) latest = exact;
                  result[candle.timestamp] = { pe: latest };
                }
                return result;
              },
            });
            chart.setPaneOptions({ id: "pe_pane", height: 86, minHeight: 70, dragEnabled: false, order: 10 });
          } catch (error) {
            if (!disposed) {
              setHistoricalPe([]);
              setHistoricalPeStatus("unavailable");
              console.warn("PIPSGOX historical P/E unavailable:", error);
            }
          }
        })();
      } else {
        setHistoricalPe([]);
        setHistoricalPeStatus("idle");
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
    showHistoricalPe,
    previousClose,
  ]);

  return (
    <div className="chart-stage">
      <div ref={containerRef} className="chart-canvas" />
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
      {showHistoricalPe && historicalPeStatus === "ready" && historicalPe.length > 0 && <div className="chart-pane-label pe-pane-label">HISTORICAL P/E</div>}
      {showHistoricalPe && historicalPeStatus === "unavailable" && <div className="chart-pane-status">Historical P/E data unavailable</div>}
    </div>
  );
}
