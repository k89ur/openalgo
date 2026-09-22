import { useEffect, useRef } from "react";
import { BarSeries, CandlestickSeries, ColorType, HistogramSeries, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";

export type ChartType = "candles" | "bars" | "line";
type Props = { chartType: ChartType };
type Candle = { time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number };

function createSampleData(): Candle[] {
  const result: Candle[] = [];
  let close = 1438;
  const start = new Date("2026-01-05T00:00:00Z");

  for (let i = 0; i < 180; i += 1) {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + i);
    const drift = Math.sin(i / 12) * 3.2 + (i % 31 === 0 ? 8 : 0);
    const open = close;
    const change = drift + Math.sin(i * 1.65) * 7.5;
    close = Math.max(1180, open + change);
    const high = Math.max(open, close) + 5 + Math.abs(Math.sin(i)) * 7;
    const low = Math.min(open, close) - 5 - Math.abs(Math.cos(i)) * 7;
    const volume = Math.round(1_050_000 + Math.abs(Math.sin(i / 5)) * 1_250_000 + (i % 9) * 70_000);
    result.push({ time: Math.floor(date.getTime() / 1000) as UTCTimestamp, open: Number(open.toFixed(2)), high: Number(high.toFixed(2)), low: Number(low.toFixed(2)), close: Number(close.toFixed(2)), volume });
  }
  return result;
}

function movingAverage(data: Candle[], period: number) {
  const output: { time: UTCTimestamp; value: number }[] = [];
  let sum = 0;
  data.forEach((item, index) => {
    sum += item.close;
    if (index >= period) sum -= data[index - period].close;
    if (index >= period - 1) output.push({ time: item.time, value: Number((sum / period).toFixed(2)) });
  });
  return output;
}

const sampleData = createSampleData();

export function Chart({ chartType }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: { background: { type: ColorType.Solid, color: "#0a111a" }, textColor: "#7f8b9d" },
      grid: { vertLines: { color: "#14202c" }, horzLines: { color: "#14202c" } },
      rightPriceScale: { borderColor: "#2a3543", scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: "#2a3543", timeVisible: false, secondsVisible: false, rightOffset: 5, barSpacing: 7 },
      crosshair: { vertLine: { color: "#5b6879" }, horzLine: { color: "#5b6879" } },
    });

    const priceOptions = { upColor: "#12d98b", downColor: "#ff4d5a", borderVisible: false, wickUpColor: "#12d98b", wickDownColor: "#ff4d5a" };

    if (chartType === "candles") {
      const series = chart.addSeries(CandlestickSeries, priceOptions);
      series.setData(sampleData.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    } else if (chartType === "bars") {
      const series = chart.addSeries(BarSeries, { upColor: "#12d98b", downColor: "#ff4d5a" });
      series.setData(sampleData.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    } else {
      const series = chart.addSeries(LineSeries, { color: "#38bdf8", lineWidth: 2, priceLineVisible: false });
      series.setData(sampleData.map(({ time, close }) => ({ time, value: close })));
    }

    const ma20 = chart.addSeries(LineSeries, { color: "#22d3ee", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    ma20.setData(movingAverage(sampleData, 20));

    const ma50 = chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    ma50.setData(movingAverage(sampleData, 50));

    const ma200 = chart.addSeries(LineSeries, { color: "#c084fc", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    ma200.setData(movingAverage(sampleData, 100));

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" }, priceScaleId: "volume", color: "rgba(56, 189, 248, 0.35)", lastValueVisible: true,
    }, 1);

    volume.setData(sampleData.map(({ time, volume, close, open }) => ({
      time, value: volume,
      color: close >= open ? "rgba(18, 217, 139, 0.72)" : "rgba(255, 77, 90, 0.72)",
    })));

    chart.priceScale("volume", 1).applyOptions({ scaleMargins: { top: 0.15, bottom: 0 }, borderVisible: false });

    const resizeObserver = new ResizeObserver(() => chart.resize(container.clientWidth, container.clientHeight));
    resizeObserver.observe(container);
    chart.timeScale().fitContent();

    return () => { resizeObserver.disconnect(); chart.remove(); };
  }, [chartType]);

  return <div ref={containerRef} className="chart-canvas" />;
}
