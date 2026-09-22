import { useEffect, useRef } from "react";
import {
  BarSeries,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";

export type ChartType = "candles" | "bars" | "line";
export type Timeframe = "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "D" | "W" | "M";

type Candle = {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type Props = {
  chartType: ChartType;
  dark: boolean;
  symbol: string;
  timeframe: Timeframe;
};

function movingAverage(data: Candle[], period: number) {
  const output: { time: UTCTimestamp; value: number }[] = [];
  let sum = 0;

  data.forEach((item, index) => {
    sum += item.close;
    if (index >= period) sum -= data[index - period].close;
    if (index >= period - 1) {
      output.push({ time: item.time, value: Number((sum / period).toFixed(2)) });
    }
  });

  return output;
}

export function Chart({ chartType, dark, symbol, timeframe }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let cancelled = false;

    const bg = dark ? "#050a0f" : "#f7f9fb";
    const text = dark ? "#8d9aaa" : "#566273";
    const grid = dark ? "#121d27" : "#e4e9ef";
    const border = dark ? "#2a3543" : "#cfd7e2";

    const chart = createChart(container, {
      layout: { background: { type: ColorType.Solid, color: bg }, textColor: text },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: border, timeVisible: timeframe !== "D" && timeframe !== "W" && timeframe !== "M", secondsVisible: false, rightOffset: 5, barSpacing: 7 },
      crosshair: { vertLine: { color: dark ? "#5b6879" : "#9aa7b7" }, horzLine: { color: dark ? "#5b6879" : "#9aa7b7" } },
    });

    const load = async () => {
      try {
        const response = await fetch(
          `/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${timeframe === "D" ? 1000 : timeframe === "W" ? 500 : timeframe === "M" ? 300 : 500}`,
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const raw = (await response.json()) as Array<{
          time: number;
          open: number;
          high: number;
          low: number;
          close: number;
          volume: number;
        }>;

        if (cancelled || raw.length === 0) return;

        const data: Candle[] = raw.map((item) => ({
          ...item,
          time: item.time as UTCTimestamp,
        }));

        const priceOptions = {
          upColor: "#12d98b",
          downColor: "#ff4d5a",
          borderVisible: false,
          wickUpColor: "#12d98b",
          wickDownColor: "#ff4d5a",
        };

        if (chartType === "candles") {
          const series = chart.addSeries(CandlestickSeries, priceOptions);
          series.setData(data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        } else if (chartType === "bars") {
          const series = chart.addSeries(BarSeries, {
            upColor: "#12d98b",
            downColor: "#ff4d5a",
          });
          series.setData(data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        } else {
          const series = chart.addSeries(LineSeries, {
            color: "#38bdf8",
            lineWidth: 2,
            priceLineVisible: false,
          });
          series.setData(data.map(({ time, close }) => ({ time, value: close })));
        }

        const ma20 = chart.addSeries(LineSeries, {
          color: "#22d3ee",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        ma20.setData(movingAverage(data, 20));

        const ma50 = chart.addSeries(LineSeries, {
          color: "#f59e0b",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        ma50.setData(movingAverage(data, 50));

        const ma200 = chart.addSeries(LineSeries, {
          color: "#c084fc",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        ma200.setData(movingAverage(data, 200));

        const volume = chart.addSeries(
          HistogramSeries,
          {
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
            color: "rgba(56, 189, 248, 0.35)",
            base: 0,
            priceLineVisible: false,
            lastValueVisible: true,
          },
          1,
        );

        volume.setData(
          data.map(({ time, volume: value, close, open }) => ({
            time,
            value,
            color: close >= open ? "rgba(18, 217, 139, 0.72)" : "rgba(255, 77, 90, 0.72)",
          })),
        );

        chart.priceScale("volume", 1).applyOptions({
          scaleMargins: { top: 0.55, bottom: 0.02 },
          borderVisible: false,
          visible: true,
        });

        chart.timeScale().fitContent();
      } catch (error) {
        console.error("PIPSGOX chart data error", error);
      }
    };

    void load();

    const resizeObserver = new ResizeObserver(() => {
      chart.resize(container.clientWidth, container.clientHeight);
    });
    resizeObserver.observe(container);

    return () => {
      cancelled = true;
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [chartType, dark, symbol, timeframe]);

  return <div ref={containerRef} className="chart-canvas" />;
}
