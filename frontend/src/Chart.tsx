import { useEffect, useRef } from "react";
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
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
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

  return <div ref={containerRef} className="chart-canvas" />;
}
