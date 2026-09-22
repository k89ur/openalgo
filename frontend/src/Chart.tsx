import { useEffect, useRef } from "react";
import { dispose, init } from "klinecharts";

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

function periodFor(timeframe: Timeframe) {
  const map: Record<Timeframe, { span: number; type: "minute" | "hour" | "day" | "week" | "month" }> = {
    "1m": { span: 1, type: "minute" },
    "3m": { span: 3, type: "minute" },
    "5m": { span: 5, type: "minute" },
    "15m": { span: 15, type: "minute" },
    "30m": { span: 30, type: "minute" },
    "1h": { span: 1, type: "hour" },
    D: { span: 1, type: "day" },
    W: { span: 1, type: "week" },
    M: { span: 1, type: "month" },
  };
  return map[timeframe];
}

export function Chart({ chartType, dark, symbol, timeframe }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = init(container, {
      locale: "en-US",
      timezone: "Asia/Kolkata",
      layout: {
        barSpaceLimit: { min: 2, max: 14 },
        yAxis: {
          position: "right",
          inside: false,
          scrollZoomEnabled: true,
        },
      },
      styles: {
        grid: {
          horizontal: {
            color: dark ? "#121d27" : "#e4e9ef",
          },
          vertical: {
            color: dark ? "#121d27" : "#e4e9ef",
          },
        },
        candle: {
          type: chartType === "bars" ? "ohlc" : chartType === "line" ? "area" : "candle_solid",
          bar: {
            upColor: "#12d98b",
            downColor: "#ff4d5a",
            noChangeColor: "#8d9aaa",
            upBorderColor: "#12d98b",
            downBorderColor: "#ff4d5a",
            noChangeBorderColor: "#8d9aaa",
            upWickColor: "#12d98b",
            downWickColor: "#ff4d5a",
            noChangeWickColor: "#8d9aaa",
          },
          area: {
            lineColor: "#38bdf8",
            lineSize: 2,
            backgroundColor: [
              { offset: 0, color: "rgba(56, 189, 248, 0.08)" },
              { offset: 1, color: "rgba(56, 189, 248, 0)" },
            ],
          },
        },
        xAxis: {
          tickText: {
            color: dark ? "#8d9aaa" : "#566273",
          },
        },
        yAxis: {
          tickText: {
            color: dark ? "#8d9aaa" : "#566273",
          },
        },
        crosshair: {
          horizontal: {
            line: { color: dark ? "#5b6879" : "#9aa7b7" },
          },
          vertical: {
            line: { color: dark ? "#5b6879" : "#9aa7b7" },
          },
        },
      },
    });

    chart.setSymbol({
      ticker: symbol,
      pricePrecision: 2,
      volumePrecision: 0,
    });
    chart.setPeriod(periodFor(timeframe));

    chart.setDataLoader({
      getBars: async ({ callback }) => {
        try {
          const limit =
            timeframe === "D" ? 500 :
            timeframe === "W" ? 250 :
            timeframe === "M" ? 120 : 500;

          const response = await fetch(
            `/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`,
          );

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }

          const raw = (await response.json()) as HistoryCandle[];

          const bars = raw
            .map((item) => ({
              timestamp: item.time * 1000,
              open: Number(item.open),
              high: Number(item.high),
              low: Number(item.low),
              close: Number(item.close),
              volume: Number(item.volume || 0),
            }))
            .sort((a, b) => a.timestamp - b.timestamp);

          callback(bars, { forward: false, backward: false });
        } catch (error) {
          console.error("PIPSGOX KLineChart data error", error);
          callback([], { forward: false, backward: false });
        }
      },
    });

    // Use KLineChart's built-in indicators for the first stable prototype.
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
      resizeObserver.disconnect();
      dispose(chart);
    };
  }, [chartType, dark, symbol, timeframe]);

  return <div ref={containerRef} className="chart-canvas" />;
}
