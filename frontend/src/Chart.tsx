import { useEffect, useRef } from "react";
import { dispose, init, type Chart as KLineChartInstance } from "klinecharts";

export type ChartType = "candles" | "bars" | "line";
export type Timeframe = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "D" | "W" | "M";

type Props = {
  chartType: ChartType;
  dark: boolean;
  symbol: string;
  timeframe: Timeframe;
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
  if (timeframe === "D") return 500;
  if (timeframe === "W") return 250;
  if (timeframe === "M") return 120;
  return 500;
}

export function Chart({ chartType, dark, symbol, timeframe }: Props) {
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

      chart.setDataLoader({
        getBars: async ({ callback }) => {
          try {
            const url =
              `/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${getLimit(timeframe)}`;

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

            console.log("PIPSGOX history bars:", bars.length);

            callback(bars, {
              forward: false,
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
              lineColor: dark ? "#38bdf8" : "#1976d2",
              lineSize: 2,
              backgroundColor: [
                { offset: 0, color: dark ? "rgba(56,189,248,0.12)" : "rgba(25,118,210,0.12)" },
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
              upColor: dark ? "#12d98b" : "#168a59",
              downColor: dark ? "#ff4d5a" : "#c93643",
              noChangeColor: "#8d9aaa",
              upBorderColor: dark ? "#12d98b" : "#168a59",
              downBorderColor: dark ? "#ff4d5a" : "#c93643",
              noChangeBorderColor: "#8d9aaa",
              upWickColor: dark ? "#12d98b" : "#168a59",
              downWickColor: dark ? "#ff4d5a" : "#c93643",
              noChangeWickColor: "#8d9aaa",
            },
          },
        });
      }

      chart.createIndicator(
        { name: "MA", paneId: "candle_pane", calcParams: [20, 50, 200] },
        true,
      );
      chart.createIndicator("VOL");

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
  }, [chartType, dark, symbol, timeframe]);

  return <div ref={containerRef} className="chart-canvas" />;
}
