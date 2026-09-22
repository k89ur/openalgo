# ChartLab

A lightweight web charting and custom-indicator application.

## Scope

- Candlestick, bar, and line charts
- Moving Average and Volume
- Custom Python/JavaScript indicators
- Indicator plots, markers, values, and tables
- One watchlist with up to 250 stocks
- Pluggable market-data providers
- Broker/API independent architecture

## Architecture

Frontend (React + TypeScript + Vite)
→ FastAPI backend
→ Market-data provider
→ Indicator engine
→ Chart renderer

The first milestone is the data → chart → indicator pipeline. Real market-data providers will be connected after the core pipeline is working.
