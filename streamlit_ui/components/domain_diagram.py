"""Plotly horizontal bar of the protein's domain architecture."""

from __future__ import annotations

import plotly.graph_objects as go

from mock.protein_loader import DomainFeature

_COLORS = {
    "Signal": "#94a3b8",
    "Transmembrane": "#f59e0b",
    "Domain": "#2563eb",
}
_DOMAIN_PALETTE = ["#2563eb", "#14b8a6", "#a855f7", "#ef4444", "#0ea5e9", "#84cc16"]


def build_figure(length: int, domains: list[DomainFeature]) -> go.Figure:
    fig = go.Figure()

    # Backbone — full protein length as a thin grey bar.
    fig.add_shape(
        type="rect",
        x0=1,
        x1=length,
        y0=0.42,
        y1=0.58,
        line=dict(width=0),
        fillcolor="#e5e7eb",
        layer="below",
    )

    palette_idx = 0
    for d in domains:
        if d["type"] == "Domain":
            color = _DOMAIN_PALETTE[palette_idx % len(_DOMAIN_PALETTE)]
            palette_idx += 1
        else:
            color = _COLORS.get(d["type"], "#64748b")

        fig.add_trace(
            go.Scatter(
                x=[d["start"], d["end"], d["end"], d["start"], d["start"]],
                y=[0.25, 0.25, 0.75, 0.75, 0.25],
                fill="toself",
                fillcolor=color,
                line=dict(color=color, width=1),
                mode="lines",
                hovertemplate=(
                    f"<b>{d['name']}</b><br>"
                    f"{d['type']}<br>"
                    f"{d['start']}–{d['end']} ({d['end'] - d['start'] + 1} aa)"
                    "<extra></extra>"
                ),
                showlegend=False,
            )
        )

        # Label inside the rectangle if it's wide enough.
        width = d["end"] - d["start"]
        if width > length * 0.04:
            fig.add_annotation(
                x=(d["start"] + d["end"]) / 2,
                y=0.5,
                text=d["name"],
                showarrow=False,
                font=dict(color="white", size=11),
            )

    fig.update_layout(
        height=160,
        margin=dict(l=20, r=20, t=20, b=40),
        plot_bgcolor="white",
        xaxis=dict(
            range=[0, length + 5],
            title="Residue position",
            showgrid=False,
            zeroline=False,
            tickmode="auto",
            nticks=8,
        ),
        yaxis=dict(
            range=[0, 1],
            showticklabels=False,
            showgrid=False,
            zeroline=False,
        ),
    )
    return fig
