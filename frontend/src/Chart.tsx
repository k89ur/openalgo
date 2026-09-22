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

type Props = {
  chartType: ChartType;
};

type Candle = {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

function createSampleData(): Candle[] {
  const result: Candle[] = [];
  let close = 1420;

  const start = new Date("2026-05-25T00:00:00Z");

  for (let i = 0; i < 120; i += 1) {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + i);

    const drift = Math.sin(i / 8) * 4 + (i % 17 === 0 ? 10 : 0);
    const open = close;
    const change = drift + Math.sin(i * 1.7) * 8;
    close = Math.max(1180, open + change);

    const high = Math.max(open, close) + 7 + Math.abs(Math.sin(i)) * 8;
    const low = Math.min(open, close) - 7 - Math.abs(Math.cos(i)) * 7;
    const volume = Math.round(
      850_000 + Math.abs(Math.sin(i / 5)) * 1_400_000 + (i % 9) * 65_000,
    );

    result.push({
      time: Math.floor(date.getTime() / 1000) as UTCTimestamp,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume,
    });
  }

  return result;
}

const sampleData = createSampleData();

export function Chart({ chartType }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "#0b0e13" },
        textColor: "#8b93a1",
      },
      grid: {
        vertLines: { color: "#171c24" },
        horzLines: { color: "#171c24" },
      },
      rightPriceScale: {
        borderColor: "#202630",
      },
      timeScale: {
        borderColor: "#202630",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
      crosshair: {
        vertLine: { color: "#46505f" },
        horzLine: { color: "#46505f" },
      },
    });

    const priceOptions = {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    };

    if (chartType === "candles") {
      const series = chart.addSeries(CandlestickSeries, priceOptions);
      series.setData(
        sampleData.map(({ time, open, high, low, close }) => ({
          time,
          open,
          high,
          low,
          close,
        })),
      );
    } else if (chartType === "bars") {
      const series = chart.addSeries(BarSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        thinBars: false,
      });
      series.setData(
        sampleData.map(({ time, open, high, low, close }) => ({
          time,
          open,
          high,
          low,
          close,
        })),
      );
    } else {
      const series = chart.addSeries(LineSeries, {
        color: "#60a5fa",
        lineWidth: 2,
        priceLineVisible: false,
      });
      series.setData(
        sampleData.map(({ time, close }) => ({
          time,
          value: close,
        })),
      );
    }

    const volume = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        color: "rgba(96, 165, 250, 0.45)",
      },
      1,
    );

    volume.setData(
      sampleData.map(({ time, volume, close, open }) => ({
        time,
        value: volume,
        color: close >= open ? "rgba(34, 197, 94, 0.45)" : "rgba(239, 68, 68, 0.45)",
      })),
    );

    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.15, bottom: 0 },
      borderVisible: false,
    });

    const resizeObserver = new ResizeObserver(() => {
      chart.resize(container.clientWidth, container.clientHeight);
    });

    resizeObserver.observe(container);
    chart.timeScale().fitContent();

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [chartType]);

  return <div ref={containerRef} className="chart-canvas" />;
}
