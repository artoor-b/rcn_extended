"""Stage-5 evaluation: cluster-bootstrap CIs, SHAP, error maps -> reports/05_evaluation.md."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

from rcn_wroclaw import config  # noqa: E402
from rcn_wroclaw.features.build import FEATURES_PATH  # noqa: E402
from rcn_wroclaw.model.bootstrap import compare  # noqa: E402
from rcn_wroclaw.model.dataset import (  # noqa: E402
    DATASET_PATH,
    location_columns,
    spatial_blocks,
    variants,
)
from rcn_wroclaw.model.evaluate import PREDICTIONS_PATH, TEST_START, metrics  # noqa: E402
from rcn_wroclaw.model.explain import (  # noqa: E402
    RCN_GROUPS,
    SHAP_PATH,
    explain_temporal,
    feature_group,
    group_importance,
)
from rcn_wroclaw.reports.eda_rcn import (  # noqa: E402
    SEQ_BLUE,
    SURFACE,
    TEXT_SECONDARY,
    _md_table,
    _style,
)
from rcn_wroclaw.reports.models import MODEL_LABEL, VARIANT_LABEL  # noqa: E402

BOOTSTRAP_PATH = config.PROCESSED_DIR / "bootstrap.parquet"
N_BOOT = 2000
DIVERGING = LinearSegmentedColormap.from_list("div", ["#2a78d6", "#f0efec", "#e34948"])
VARIANT_LABEL = {**VARIANT_LABEL, "rcn_lok_sel": "RCN + lokalizacja (51 cech)"}

COMPARISONS = [  # (A = reference, B = candidate)
    ("rcn", "rcn_lok"),
    ("rcn", "rcn_lok_sel"),
    ("rcn_lok", "rcn_lok_xy"),
    ("rcn_bez_osiedla", "rcn"),
]


def run_bootstrap(pred: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """All comparisons × models × evaluation subsets with appropriate clusters."""
    meta = df[["gid", "location_id", "x_2180", "y_2180", "month"]].copy()
    meta["block"] = spatial_blocks(meta["x_2180"], meta["y_2180"], 1000.0)
    train_locs = set(df.loc[df["month"] < TEST_START, "location_id"])
    p = pred.merge(meta[["gid", "location_id", "block"]], on="gid", how="left")
    seen = p["location_id"].isin(train_locs)
    subsets = [
        ("temporal", "test 2026 – wszystkie", None, "location_id"),
        ("temporal", "test 2026 – nowe lokalizacje", ~seen, "location_id"),
        ("temporal", "test 2026 – lokalizacje z treningu", seen, "location_id"),
        ("spatial_cv", "przestrzenna CV", None, "block"),
        ("spatial_cv", "przestrzenna CV (klastry = budynki)", None, "location_id"),
        ("temporal_trend", "test 2026 – indeks z trendem", None, "location_id"),
    ]
    rows = []
    for split, label, mask, cluster in subsets:
        avail = set(p.loc[p["split"] == split, "variant"])
        for model in ("lgbm", "ridge"):
            for a, b in COMPARISONS:
                if not {a, b} <= avail:
                    continue
                r = compare(p, split, model, a, b, cluster, mask=mask, n_boot=N_BOOT, seed=7)
                r = r.rename(columns={"A": "val_a", "B": "val_b"}).reset_index()
                rows.append(
                    r.assign(split=split, subset=label, model=model, variant_a=a, variant_b=b,
                             cluster=cluster)
                )  # fmt: skip
    return pd.concat(rows, ignore_index=True)


def _fmt_ci(row, col_lo, col_hi, digits=0, pct=False) -> str:
    f = f"{{:,.{digits}f}}"
    return f"[{f.format(row[col_lo])}; {f.format(row[col_hi])}]"


def _boot_table(boot: pd.DataFrame, model: str, metric: str, comps=None) -> pd.DataFrame:
    b = boot[(boot["model"] == model) & (boot["metric"] == metric)].copy()
    if comps is not None:
        b = b[b.apply(lambda r: (r["variant_a"], r["variant_b"]) in comps, axis=1)]
    digits = 3 if metric == "R2" else (2 if metric == "MAPE" else 0)
    b["porównanie"] = b["variant_b"].map(VARIANT_LABEL) + " vs " + b["variant_a"].map(VARIANT_LABEL)
    b["wartość A"] = b["val_a"].map(lambda v: f"{v:,.{digits}f}")
    b["wartość B"] = b["val_b"].map(lambda v: f"{v:,.{digits}f}")
    b["Δ (B−A)"] = b["diff"].map(lambda v: f"{v:+,.{digits}f}")
    b["95% CI Δ"] = b.apply(lambda r: _fmt_ci(r, "diff_lo", "diff_hi", digits), axis=1)
    b["p (B nie lepszy)"] = b["p_b_not_better"].map(lambda v: f"{v:.3f}")
    b["n / klastry"] = b["n"].map("{:,}".format) + " / " + b["n_clusters"].map("{:,}".format)
    return b.set_index(["subset", "porównanie"])[
        ["wartość A", "wartość B", "Δ (B−A)", "95% CI Δ", "p (B nie lepszy)", "n / klastry"]
    ]


def _fig_groups(imp: pd.DataFrame, path: Path) -> None:
    imp = imp.sort_values("approx_%_price")
    colors = ["#eb6834" if g in RCN_GROUPS else "#2a78d6" for g in imp.index]
    fig, ax = plt.subplots(figsize=(8, 0.4 * len(imp) + 1.2))
    ax.barh(imp.index, imp["approx_%_price"], color=colors, height=0.7, edgecolor=SURFACE)
    for y, v in enumerate(imp["approx_%_price"]):
        ax.text(v, y, f" {v:.1f}%", va="center", fontsize=8, color=TEXT_SECONDARY)
    ax.set_xlabel("średni |SHAP| grupy ≈ % ceny/m²")
    ax.set_title("Wkład grup cech (LightGBM, RCN + lokalizacja, test 2026)")
    ax.plot([], [], color="#eb6834", lw=6, label="RCN / czas / osiedle")
    ax.plot([], [], color="#2a78d6", lw=6, label="lokalizacja")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_dependence(test: pd.DataFrame, shap: pd.DataFrame, feats: list[str], path: Path) -> None:
    fig, axes = plt.subplots(1, len(feats), figsize=(3.2 * len(feats), 3.2), sharey=True)
    for ax, f in zip(np.atleast_1d(axes), feats, strict=True):
        x = test[f].astype(float)
        y = 100 * (np.exp(shap[f]) - 1)
        hi = x.quantile(0.99)
        ax.scatter(x.clip(upper=hi), y, s=3, alpha=0.3, color="#2a78d6", linewidths=0)
        ax.axhline(0, color=TEXT_SECONDARY, lw=0.8)
        ax.set_title(f, fontsize=9)
    np.atleast_1d(axes)[0].set_ylabel("wkład SHAP ≈ % ceny")
    fig.suptitle("Zależności SHAP – najważniejsze cechy lokalizacyjne", fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_osiedla(
    os_stats: gpd.GeoDataFrame, col: str, title: str, label: str, path: Path, diverging: bool
) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    kw = {}
    if diverging:
        lim = np.nanmax(np.abs(os_stats[col]))
        kw = {"cmap": DIVERGING, "norm": TwoSlopeNorm(0, -lim, lim)}
    else:
        kw = {"cmap": SEQ_BLUE}
    missing = {"color": SURFACE, "hatch": "///", "edgecolor": "#a3a29c"}
    os_stats.plot(
        ax=ax, column=col, edgecolor=SURFACE, linewidth=0.8, legend=True,
        legend_kwds={"shrink": 0.6, "label": label}, missing_kwds=missing, **kw,
    )  # fmt: skip
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def build_report(out_md: Path, fig_dir: Path) -> Path:
    _style()
    fig_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(DATASET_PATH)
    pred = pd.read_parquet(PREDICTIONS_PATH)

    boot = run_bootstrap(pred, df)
    boot.to_parquet(BOOTSTRAP_PATH)

    loc_cols = location_columns(gpd.read_parquet(FEATURES_PATH))
    num, cat = variants(loc_cols)["rcn_lok"]
    _, test, shap = explain_temporal(df, num, cat, "rcn_lok")
    shap.assign(gid=test["gid"].to_numpy()).to_parquet(SHAP_PATH)
    gimp = group_importance(shap)
    feats = [c for c in shap.columns if c != "bias"]
    fimp = shap[feats].abs().mean().sort_values(ascending=False)

    L: list[str] = []
    add = L.append
    add("# Etap 5 – ewaluacja: przedziały ufności, SHAP, błędy przestrzenne\n")
    add(f"Wygenerowano: {dt.datetime.now():%Y-%m-%d %H:%M}.\n")

    add("## 1. Metoda\n")
    add(
        f"- **Sparowany bootstrap klastrowy** ({N_BOOT} replikacji): losujemy całe klastry ze "
        "zwracaniem i liczymy metryki obu wariantów na tej samej próbie. Klaster = budynek "
        "(`location_id`) w teście czasowym, blok 1×1 km w przestrzennej CV (sprawdzająco "
        "również budynek). Transakcje z jednego budynku nie są niezależne – zwykły bootstrap "
        "zawyżyłby pewność."
    )
    add(
        "- Przedział 95% percentylowy dla różnicy Δ = B − A; *p (B nie lepszy)* = odsetek "
        "replikacji, w których wariant B nie jest lepszy (jednostronny bootstrapowy p)."
    )
    add(
        "- **Nowe lokalizacje** = punkty (budynki) bez żadnej transakcji w okresie treningowym; "
        "to główna miara, czy cechy dostępności *uogólniają się*, a nie tylko identyfikują "
        "budynki."
    )
    train_locs = set(df.loc[df["month"] < TEST_START, "location_id"])
    te = df[df["month"] >= TEST_START]
    new = ~te["location_id"].isin(train_locs)
    add(
        f"- Skład testu 2026: nowe lokalizacje to {new.sum():,} transakcji, w tym "
        f"**{100 * te.loc[new, 'is_primary'].mean():.0f}% rynek pierwotny** (lokalizacje z "
        f"treningu: {100 * te.loc[~new, 'is_primary'].mean():.0f}%). To głównie nowe "
        "inwestycje – inna populacja niż w przestrzennej CV.\n"
    )

    add("## 2. Wyniki – MAE [zł/m²]\n")
    for model in ("lgbm", "ridge"):
        add(f"### {MODEL_LABEL[model]}\n")
        add(_md_table(_boot_table(boot, model, "MAE")) + "\n")
    add("## 3. Wyniki – R² i MAPE (LightGBM, porównanie główne)\n")
    main = [("rcn", "rcn_lok")]
    add("**R²**\n")
    add(_md_table(_boot_table(boot, "lgbm", "R2", main)) + "\n")
    add("**MAPE [%]**\n")
    add(_md_table(_boot_table(boot, "lgbm", "MAPE", main)) + "\n")

    add("## 4. SHAP – co model wykorzystuje (LightGBM, RCN + lokalizacja, test 2026)\n")
    f_groups = fig_dir / "eval_shap_groups.png"
    _fig_groups(gimp, f_groups)
    add(f"![Grupy SHAP]({fig_dir.name}/{f_groups.name})\n")
    loc_share = gimp.loc[~gimp.index.isin(RCN_GROUPS), "share_%"].sum()
    add(
        f"Udział grup lokalizacyjnych w łącznym |SHAP|: **{loc_share:.1f}%** (pozostałe: lokal, "
        "czas, osiedle, strony transakcji). Średni |SHAP| liczony po zsumowaniu wkładów cech "
        "w grupie, więc cechy znoszące się wzajemnie nie są zawyżane.\n"
    )
    add(_md_table(gimp, floatfmt=".3f") + "\n")
    top = fimp.head(20).rename("mean_abs_shap").to_frame()
    top["grupa"] = [feature_group(f) for f in top.index]
    top["≈ % ceny"] = 100 * (np.exp(top["mean_abs_shap"]) - 1)
    add("Top 20 pojedynczych cech:\n")
    add(_md_table(top, floatfmt=".3f") + "\n")
    loc_top = [f for f in fimp.index if feature_group(f) not in RCN_GROUPS][:4]
    f_dep = fig_dir / "eval_shap_dependence.png"
    _fig_dependence(test, shap, loc_top, f_dep)
    add(f"![Zależności SHAP]({fig_dir.name}/{f_dep.name})\n")

    add("## 5. Błędy w przestrzeni (przestrzenna CV, LightGBM)\n")
    osiedla = gpd.read_parquet(config.OSIEDLA)
    sp = pred[(pred["split"] == "spatial_cv") & (pred["model"] == "lgbm")]
    per = (
        sp[sp["variant"].isin(["rcn", "rcn_lok"])]
        .groupby(["osiedle", "variant"])
        .apply(lambda g: metrics(g["price_m2"], g["pred_m2"])["MAE"], include_groups=False)
        .unstack("variant")
    )
    per["n"] = sp[sp["variant"] == "rcn"].groupby("osiedle").size()
    per["delta"] = per["rcn_lok"] - per["rcn"]
    os_stats = osiedla.merge(per, left_on="osiedle", right_index=True, how="left")
    os_stats.loc[os_stats["n"] < 30, ["rcn_lok", "delta"]] = np.nan
    f1, f2 = fig_dir / "eval_map_mae.png", fig_dir / "eval_map_delta.png"
    _fig_osiedla(
        os_stats, "rcn_lok", "MAE wg osiedla – RCN + lokalizacja", "MAE zł/m²", f1, diverging=False
    )
    _fig_osiedla(os_stats, "delta", "ΔMAE: + lokalizacja vs bazowy (niebieski = poprawa)",
                 "ΔMAE zł/m²", f2, diverging=True)  # fmt: skip
    add(f"![MAE]({fig_dir.name}/{f1.name})\n")
    add(f"![ΔMAE]({fig_dir.name}/{f2.name})\n")
    ok = per[per["n"] >= 30]
    better = int((ok["delta"] < 0).sum())
    add(
        f"Cechy lokalizacyjne zmniejszają MAE w **{better} z {len(ok)}** osiedli z ≥ 30 "
        "transakcjami (pozostałe kreskowane na mapach).\n"
    )
    tbl = ok.sort_values("delta")[["n", "rcn", "rcn_lok", "delta"]]
    tbl.columns = ["n", "MAE bazowy", "MAE + lokalizacja", "Δ"]
    add("Największa poprawa / pogorszenie:\n")
    add(_md_table(pd.concat([tbl.head(5), tbl.tail(5)]), floatfmt=",.0f") + "\n")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")
    return out_md
