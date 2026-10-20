"""
Trade-plan chart: candles + the plan drawn on top in plain English.
  * green shaded zone = where you make money, red shaded zone = where you lose
  * a "BUY here" marker on the next session at the entry price
  * one line per exit step (e.g. "Sell 50% (6 sh) at ₹512") and the stop loss
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def plan_chart(d: pd.DataFrame, title: str, entry: float | None = None, stop: float | None = None,
               ladder: list[dict] | None = None, days: int = 90, extra_lines: list[tuple] | None = None,
               rsi_col: str = "RSI", rsi_levels=(30, 50, 70)) -> go.Figure:
    """
    ladder: [{"price": 512.3, "label": "Sell 50% (6 sh)", "color": "#16a34a"}, ...]
    extra_lines: [(price, label, color)] for things like "trend dies below"
    """
    d = d.tail(days)
    has_rsi = rsi_col in d.columns
    fig = make_subplots(rows=3 if has_rsi else 2, cols=1, shared_xaxes=True,
                        row_heights=[0.68, 0.14, 0.18] if has_rsi else [0.8, 0.2], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
                                 name="Price", increasing_line_color="#16a34a", decreasing_line_color="#dc2626"), 1, 1)
    for col, colr, name in [("EMA20", "#2563eb", "20-day avg"), ("EMA50", "#f59e0b", "50-day avg"),
                            ("EMA200", "#6b7280", "200-day avg")]:
        if col in d.columns:
            fig.add_trace(go.Scatter(x=d.index, y=d[col], name=name, line=dict(width=1.2, color=colr)), 1, 1)

    x0, x1 = d.index[-1] - pd.Timedelta(days=12), d.index[-1] + pd.Timedelta(days=10)
    nxt = d.index[-1] + pd.offsets.BDay(1)
    if entry and stop:
        top = max([l["price"] for l in (ladder or [])] + [entry])
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=stop, y1=entry, fillcolor="rgba(220,38,38,0.10)",
                      line_width=0, row=1, col=1)
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=entry, y1=top, fillcolor="rgba(22,163,74,0.10)",
                      line_width=0, row=1, col=1)
        fig.add_hline(y=entry, line_color="#2563eb", line_width=2, row=1, col=1,
                      annotation_text=f"BUY at ₹{entry:,.2f}", annotation_position="top left",
                      annotation_font_color="#2563eb")
        fig.add_hline(y=stop, line_color="#dc2626", line_width=2, line_dash="dash", row=1, col=1,
                      annotation_text=f"STOP LOSS ₹{stop:,.2f} ({100*(stop/entry-1):+.1f}%) — exit everything",
                      annotation_position="bottom left", annotation_font_color="#dc2626")
        fig.add_trace(go.Scatter(x=[nxt], y=[entry], mode="markers+text", text=["  BUY here (next session)"],
                                 textposition="middle right", marker=dict(symbol="triangle-up", size=16, color="#2563eb"),
                                 name="Entry", showlegend=False), 1, 1)
    for l in ladder or []:
        pct = f" ({100*(l['price']/entry-1):+.1f}%)" if entry else ""
        fig.add_hline(y=l["price"], line_color=l.get("color", "#16a34a"), line_dash="dot", row=1, col=1,
                      annotation_text=f"{l['label']} at ₹{l['price']:,.2f}{pct}", annotation_position="top right",
                      annotation_font_color=l.get("color", "#16a34a"))
    for y, lab, colr in extra_lines or []:
        fig.add_hline(y=y, line_color=colr, line_dash="dot", row=1, col=1, annotation_text=lab,
                      annotation_position="bottom right", annotation_font_color=colr)

    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], name="Volume", marker_color="#94a3b8"), 2, 1)
    if has_rsi:
        fig.add_trace(go.Scatter(x=d.index, y=d[rsi_col], name=rsi_col, line=dict(color="#7c3aed", width=1.3)), 3, 1)
        for y in rsi_levels:
            fig.add_hline(y=y, line_dash="dot", line_color="#cbd5e1", row=3, col=1)
    fig.update_layout(height=620, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10),
                      title=title, legend=dict(orientation="h", y=1.04), hovermode="x unified")
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], range=[d.index[0], x1])
    return fig
