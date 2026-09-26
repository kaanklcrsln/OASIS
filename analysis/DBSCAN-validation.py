"""
DBSCAN Uzamsal Kumeleme — Uretim Algoritmasi Replikasyonu
==========================================================
Amac:
    test_reports.json icindeki 50 raporu, production cluster_service.py
    algoritmasinin birebir kopyasiyla kumuler:
      - Raporlar disaster_type bazinda gruplanir
      - Her grup icinde ozel DBSCAN uygulanir (eps=0.0005 deg, min_pts=3)
      - Gurultu noktalari singleton cluster olur
      - Buffer yaricaplari hesaplanir (min 100m, max 300m)
      - 3+ raporda merkez kilitlenir (is_locked=True)
      - Severity ortalamasi hesaplanir

    Dashboard'daki cluster goruntulerinin matplotlib karsiligidir.

Bagimliliklar:
    pip install pandas matplotlib numpy seaborn

Calistirma:
    cd analysis && python DBSCAN-validation.py

Ciktilar (analysis/outputs/):
    - dbscan_cluster_map.png          : Afet turu renkli cluster haritasi + buffer daireleri
    - dbscan_cluster_detail.png       : Her cluster icin detay (merkez, yaricap, uyeler)
    - dbscan_type_distribution.png    : Afet turune gore cluster dagilimi
    - dbscan_severity_analysis.png    : Cluster bazli severity analizi
    - dbscan_cluster_summary.csv      : Cluster ozet tablosu
"""

import json
import math
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

# ── Dizin ayarlari ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.facecolor": "white",
})

# ── Uretim parametreleri (cluster_service.py ile birebir) ─────────────────
DBSCAN_EPS_DEG = 0.0005    # ~42 metre (derece cinsinden)
DBSCAN_MIN_PTS = 3
BUFFER_INITIAL = 100.0     # Yeni cluster baslangic yaricapi (m)
BUFFER_MAX = 300.0          # Maksimum buffer yaricapi (m)
LOCK_THRESHOLD = 3          # Merkez kilitleme esigi
DEG_TO_M = 111_000          # 1 derece ~ 111 km

# ── Afet turu ground truth (production'da AI belirler) ────────────────────
GROUND_TRUTH_TYPE = {
    "AKY-RPT-001": "heyelan",       "AKY-RPT-002": "heyelan",
    "AKY-RPT-003": "heyelan",       "AKY-RPT-004": "heyelan",
    "AKY-RPT-005": "heyelan",
    "AKY-RPT-006": "sel",           "AKY-RPT-007": "sel",
    "AKY-RPT-008": "sel",           "AKY-RPT-009": "sel",
    "AKY-RPT-010": "sel",
    "AKY-RPT-011": "sel",           "AKY-RPT-012": "sel",
    "AKY-RPT-013": "sel",           "AKY-RPT-014": "sel",
    "AKY-RPT-015": "sel",
    "AKY-RPT-016": "altyapi_hasari", "AKY-RPT-017": "altyapi_hasari",
    "AKY-RPT-018": "altyapi_hasari", "AKY-RPT-019": "altyapi_hasari",
    "AKY-RPT-020": "altyapi_hasari",
    "AKY-RPT-021": "sel",           "AKY-RPT-022": "sel",
    "AKY-RPT-023": "sel",           "AKY-RPT-024": "sel",
    "AKY-RPT-025": "sel",           "AKY-RPT-026": "sel",
    "AKY-RPT-027": "sel",           "AKY-RPT-028": "sel",
    "AKY-RPT-029": "sel",           "AKY-RPT-030": "sel",
    "AKY-RPT-031": "sel",           "AKY-RPT-032": "tibbi_acil",
    "AKY-RPT-033": "yapi_hasari",   "AKY-RPT-034": "yapi_hasari",
    "AKY-RPT-035": "sel",           "AKY-RPT-036": "tibbi_acil",
    "AKY-RPT-037": "altyapi_hasari", "AKY-RPT-038": "yapi_hasari",
    "AKY-RPT-039": "heyelan",       "AKY-RPT-040": "tibbi_acil",
    "AKY-RPT-041": "alakasiz",      "AKY-RPT-042": "alakasiz",
    "AKY-RPT-043": "alakasiz",      "AKY-RPT-044": "alakasiz",
    "AKY-RPT-045": "alakasiz",
    "AKY-RPT-046": "sel",           "AKY-RPT-047": "sel",
    "AKY-RPT-048": "sel",           "AKY-RPT-049": "tibbi_acil",
    "AKY-RPT-050": "sel",
}

# Basit severity tahmini (indicator_scoring.py'daki keyword benzeri yaklasim)
SEVERITY_ESTIMATES = {
    "AKY-RPT-001": 6, "AKY-RPT-002": 7, "AKY-RPT-003": 7, "AKY-RPT-004": 6,
    "AKY-RPT-005": 5, "AKY-RPT-006": 6, "AKY-RPT-007": 7, "AKY-RPT-008": 8,
    "AKY-RPT-009": 5, "AKY-RPT-010": 6, "AKY-RPT-011": 7, "AKY-RPT-012": 6,
    "AKY-RPT-013": 7, "AKY-RPT-014": 5, "AKY-RPT-015": 6, "AKY-RPT-016": 5,
    "AKY-RPT-017": 6, "AKY-RPT-018": 7, "AKY-RPT-019": 4, "AKY-RPT-020": 5,
    "AKY-RPT-021": 7, "AKY-RPT-022": 6, "AKY-RPT-023": 8, "AKY-RPT-024": 5,
    "AKY-RPT-025": 6, "AKY-RPT-026": 7, "AKY-RPT-027": 6, "AKY-RPT-028": 5,
    "AKY-RPT-029": 7, "AKY-RPT-030": 6, "AKY-RPT-031": 7, "AKY-RPT-032": 8,
    "AKY-RPT-033": 6, "AKY-RPT-034": 7, "AKY-RPT-035": 6, "AKY-RPT-036": 9,
    "AKY-RPT-037": 5, "AKY-RPT-038": 7, "AKY-RPT-039": 6, "AKY-RPT-040": 8,
    "AKY-RPT-041": 1, "AKY-RPT-042": 1, "AKY-RPT-043": 1, "AKY-RPT-044": 1,
    "AKY-RPT-045": 1, "AKY-RPT-046": 7, "AKY-RPT-047": 6, "AKY-RPT-048": 8,
    "AKY-RPT-049": 9, "AKY-RPT-050": 7,
}

DISASTER_TYPE_LABELS_TR = {
    "sel": "Sel",
    "heyelan": "Heyelan",
    "altyapi_hasari": "Altyapi Hasari",
    "yapi_hasari": "Yapi Hasari",
    "tibbi_acil": "Tibbi Acil",
    "alakasiz": "Alakasiz",
}

DISASTER_TYPE_COLORS = {
    "sel": "#4393c3",
    "heyelan": "#8c6d31",
    "altyapi_hasari": "#e7298a",
    "yapi_hasari": "#d95f02",
    "tibbi_acil": "#e41a1c",
    "alakasiz": "#999999",
}


# ── Production DBSCAN (cluster_service.py'dan birebir kopya) ──────────────

def _euclidean(p1: tuple, p2: tuple) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _region_query(points: list, point_idx: int, eps: float) -> list:
    neighbors = []
    pivot = points[point_idx]
    for idx, point in enumerate(points):
        if _euclidean(pivot, point) < eps:
            neighbors.append(idx)
    return neighbors


def _expand_cluster(points, labels, point_idx, cluster_id, eps, min_pts):
    neighbors = _region_query(points, point_idx, eps)
    if len(neighbors) < min_pts:
        labels[point_idx] = -1
        return False
    labels[point_idx] = cluster_id
    queue = neighbors[:]
    head = 0
    while head < len(queue):
        current_point = queue[head]
        head += 1
        if labels[current_point] == -1:
            labels[current_point] = cluster_id
        elif labels[current_point] == 0:
            labels[current_point] = cluster_id
            new_neighbors = _region_query(points, current_point, eps)
            if len(new_neighbors) >= min_pts:
                queue.extend(new_neighbors)
    return True


def _dbscan(points: list, eps: float, min_pts: int) -> list:
    """Production DBSCAN algoritmasi (cluster_service.py ile birebir ayni)."""
    cluster_id = 0
    labels = [0] * len(points)
    for point_idx in range(len(points)):
        if labels[point_idx] == 0:
            if _expand_cluster(points, labels, point_idx, cluster_id + 1, eps, min_pts):
                cluster_id += 1
    return labels


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine mesafesi (metre)."""
    R = 6_371_000.0
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Veri yukleme ──────────────────────────────────────────────────────────

def load_reports() -> pd.DataFrame:
    """test_reports.json dosyasini okuyup DataFrame dondurur."""
    print("  Veri yukleniyor...")
    with open(ROOT / "backend" / "seed" / "test_reports.json", encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for r in data["raw_reports"]:
        lon, lat = r["gps_location"]["coordinates"]
        rid = r["report_id"]
        rows.append({
            "report_id": rid,
            "lon": lon,
            "lat": lat,
            "disaster_type": GROUND_TRUTH_TYPE.get(rid, "bilinmiyor"),
            "severity": SEVERITY_ESTIMATES.get(rid, 1),
            "report_date": r.get("report_date", ""),
        })
    df = pd.DataFrame(rows)
    print(f"  {len(df)} rapor yuklendi.")
    return df


# ── Production cluster algoritmasi ────────────────────────────────────────

def build_clusters(df: pd.DataFrame) -> list:
    """
    Production cluster_service.py rebuild_clusters_with_dbscan() replikasyonu.
    disaster_type bazinda gruplama + DBSCAN + buffer/lock mantigi.
    """
    clusters = []
    cluster_global_id = 0

    grouped = df.groupby("disaster_type")

    for event_type, group in grouped:
        items = group.sort_values("report_date").to_dict("records")
        if not items:
            continue

        points = [(item["lon"], item["lat"]) for item in items]
        labels = _dbscan(points, DBSCAN_EPS_DEG, DBSCAN_MIN_PTS)

        # Etiketlere gore gruplama
        clusters_by_label: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters_by_label.setdefault(label, []).append(idx)

        # Gurultu (-1) noktalari singleton cluster olur (production davranisi)
        next_label = max([l for l in clusters_by_label if l > 0], default=0) + 1
        if -1 in clusters_by_label:
            for noise_idx in clusters_by_label[-1]:
                clusters_by_label[next_label] = [noise_idx]
                next_label += 1
            del clusters_by_label[-1]

        for member_indices in clusters_by_label.values():
            member_items = [items[i] for i in member_indices]
            member_count = len(member_items)
            if member_count == 0:
                continue

            cluster_global_id += 1

            # Ilk merkez: en erken raporun noktasi
            first_item = member_items[0]
            initial_lat, initial_lon = first_item["lat"], first_item["lon"]

            # Cluster center: centroid
            center_lat = sum(it["lat"] for it in member_items) / member_count
            center_lon = sum(it["lon"] for it in member_items) / member_count

            # Radius: en uzak noktaya gore, min 100 max 300
            max_dist = 0.0
            for item in member_items:
                dist = _haversine_m(center_lat, center_lon, item["lat"], item["lon"])
                if dist > max_dist:
                    max_dist = dist
            radius = min(max(max_dist, BUFFER_INITIAL), BUFFER_MAX)

            # Severity ortalamasi
            sev_values = [it["severity"] for it in member_items if it["severity"] is not None]
            severity_avg = round(sum(sev_values) / len(sev_values), 1) if sev_values else None

            is_locked = member_count >= LOCK_THRESHOLD
            is_singleton = member_count == 1

            clusters.append({
                "cluster_id": cluster_global_id,
                "event_type": event_type,
                "report_count": member_count,
                "center_lat": center_lat,
                "center_lon": center_lon,
                "initial_lat": initial_lat,
                "initial_lon": initial_lon,
                "radius_m": round(radius, 1),
                "severity_avg": severity_avg,
                "is_locked": is_locked,
                "is_singleton": is_singleton,
                "report_ids": [it["report_id"] for it in member_items],
                "member_lats": [it["lat"] for it in member_items],
                "member_lons": [it["lon"] for it in member_items],
            })

    return clusters


# ── Gorsel 1: Dashboard benzeri cluster haritasi + buffer daireleri ───────

def plot_cluster_map(df: pd.DataFrame, clusters: list) -> None:
    """Afet turu renkli cluster haritasi, buffer daireleri ve merkez isareti."""
    print("  Cluster haritasi olusturuluyor...")
    fig, ax = plt.subplots(figsize=(14, 10))

    # Arka plan: tum raporlar (soluk)
    ax.scatter(df["lon"], df["lat"], c="#dddddd", s=20, zorder=1, label="_nolegend_")

    legend_handles = []
    type_plotted = set()

    for cl in clusters:
        etype = cl["event_type"]
        color = DISASTER_TYPE_COLORS.get(etype, "#666666")
        label_tr = DISASTER_TYPE_LABELS_TR.get(etype, etype)

        # Rapor noktalarini ciz
        ax.scatter(
            cl["member_lons"], cl["member_lats"],
            c=color, s=60, zorder=4, edgecolors="white", linewidths=0.5,
        )

        # Buffer dairesi (derece cinsine cevir)
        radius_deg = cl["radius_m"] / DEG_TO_M
        circle = plt.Circle(
            (cl["center_lon"], cl["center_lat"]),
            radius_deg, fill=True, facecolor=color, alpha=0.15,
            edgecolor=color, linewidth=1.5, linestyle="--", zorder=2,
        )
        ax.add_patch(circle)

        # Merkez isareti
        if cl["is_locked"]:
            # Kilitli merkez: kare
            ax.scatter(
                cl["center_lon"], cl["center_lat"],
                marker="s", c=color, s=120, zorder=5,
                edgecolors="black", linewidths=1.5,
            )
        else:
            # Acik merkez: ucgen
            ax.scatter(
                cl["center_lon"], cl["center_lat"],
                marker="^", c=color, s=100, zorder=5,
                edgecolors="black", linewidths=1.0,
            )

        # Cluster ID etiketi
        ax.annotate(
            f"C{cl['cluster_id']}",
            (cl["center_lon"], cl["center_lat"]),
            textcoords="offset points", xytext=(8, 8),
            fontsize=7, fontweight="bold", color=color,
            zorder=6,
        )

        if etype not in type_plotted:
            legend_handles.append(
                mpatches.Patch(color=color, label=f"{label_tr}")
            )
            type_plotted.add(etype)

    # Efsane eklemeleri
    legend_handles.append(
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="gray",
                   markersize=8, markeredgecolor="black", label="Kilitli Merkez (3+)")
    )
    legend_handles.append(
        plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="gray",
                   markersize=8, markeredgecolor="black", label="Acik Merkez (<3)")
    )

    n_multi = sum(1 for c in clusters if not c["is_singleton"])
    n_single = sum(1 for c in clusters if c["is_singleton"])

    ax.set_xlabel("Boylam", fontsize=11)
    ax.set_ylabel("Enlem", fontsize=11)
    ax.set_title(
        f"Production DBSCAN Cluster Haritasi\n"
        f"eps={DBSCAN_EPS_DEG} derece (~{int(DBSCAN_EPS_DEG * DEG_TO_M)}m), "
        f"min_pts={DBSCAN_MIN_PTS} | "
        f"{n_multi} cluster + {n_single} singleton | "
        f"buffer: {int(BUFFER_INITIAL)}-{int(BUFFER_MAX)}m",
        fontsize=12,
    )
    ax.legend(handles=legend_handles, loc="upper left", fontsize=8, framealpha=0.9)
    ax.set_aspect("equal")
    fig.tight_layout()
    out = OUTPUT_DIR / "dbscan_cluster_map.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {out}")


# ── Gorsel 2: Cluster detay paneli ────────────────────────────────────────

def plot_cluster_detail(clusters: list) -> None:
    """Her cluster icin detay: rapor sayisi, yaricap, severity, lock durumu."""
    print("  Cluster detay paneli olusturuluyor...")

    # Singleton olmayan cluster'lar
    multi_clusters = [c for c in clusters if not c["is_singleton"]]
    if not multi_clusters:
        print("  Coklu cluster bulunamadi, detay paneli atlanıyor.")
        return

    n = len(multi_clusters)
    cols = min(n, 3)
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 5 * rows))
    if rows * cols == 1:
        axes = np.array([axes])
    axes = axes.flatten()

    for i, cl in enumerate(multi_clusters):
        ax = axes[i]
        etype = cl["event_type"]
        color = DISASTER_TYPE_COLORS.get(etype, "#666666")

        # Uyeler
        ax.scatter(
            cl["member_lons"], cl["member_lats"],
            c=color, s=80, zorder=4, edgecolors="white", linewidths=0.8,
        )

        # Buffer dairesi
        radius_deg = cl["radius_m"] / DEG_TO_M
        circle = plt.Circle(
            (cl["center_lon"], cl["center_lat"]),
            radius_deg, fill=True, facecolor=color, alpha=0.12,
            edgecolor=color, linewidth=2, linestyle="--", zorder=2,
        )
        ax.add_patch(circle)

        # Merkez
        marker = "s" if cl["is_locked"] else "^"
        ax.scatter(
            cl["center_lon"], cl["center_lat"],
            marker=marker, c="red", s=100, zorder=5,
            edgecolors="black", linewidths=1.5,
        )

        # Initial center (ilk raporun konumu)
        ax.scatter(
            cl["initial_lon"], cl["initial_lat"],
            marker="*", c="gold", s=150, zorder=5,
            edgecolors="black", linewidths=0.8,
        )

        # Rapor ID etiketleri
        for lon, lat, rid in zip(cl["member_lons"], cl["member_lats"], cl["report_ids"]):
            ax.annotate(
                rid.split("-")[-1],
                (lon, lat), textcoords="offset points", xytext=(5, 5),
                fontsize=6, color="#333333",
            )

        lock_str = "KILITLI" if cl["is_locked"] else "ACIK"
        ax.set_title(
            f"C{cl['cluster_id']}: {DISASTER_TYPE_LABELS_TR.get(etype, etype)}\n"
            f"{cl['report_count']} rapor | r={cl['radius_m']}m | "
            f"sev={cl['severity_avg']} | {lock_str}",
            fontsize=9,
        )
        ax.set_aspect("equal")
        ax.tick_params(labelsize=7)

    # Bos panelleri gizle
    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)

    # Efsane
    fig.legend(
        handles=[
            plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="red",
                       markersize=8, markeredgecolor="black", label="Cluster Merkez"),
            plt.Line2D([0], [0], marker="*", color="w", markerfacecolor="gold",
                       markersize=10, markeredgecolor="black", label="Ilk Rapor (Initial)"),
        ],
        loc="lower center", ncol=2, fontsize=9, framealpha=0.9,
    )

    fig.suptitle("Cluster Detay Goruntuleri (Singleton Haric)", fontsize=13, y=1.02)
    fig.tight_layout()
    out = OUTPUT_DIR / "dbscan_cluster_detail.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {out}")


# ── Gorsel 3: Afet turune gore dagilim ────────────────────────────────────

def plot_type_distribution(clusters: list) -> None:
    """Afet turune gore cluster sayisi, rapor sayisi ve singleton orani."""
    print("  Tur dagilimi grafigi olusturuluyor...")

    type_stats = {}
    for cl in clusters:
        et = cl["event_type"]
        if et not in type_stats:
            type_stats[et] = {"clusters": 0, "reports": 0, "singletons": 0, "locked": 0}
        type_stats[et]["clusters"] += 1
        type_stats[et]["reports"] += cl["report_count"]
        if cl["is_singleton"]:
            type_stats[et]["singletons"] += 1
        if cl["is_locked"]:
            type_stats[et]["locked"] += 1

    types = sorted(type_stats.keys())
    labels = [DISASTER_TYPE_LABELS_TR.get(t, t) for t in types]
    colors = [DISASTER_TYPE_COLORS.get(t, "#666") for t in types]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # Panel 1: Cluster sayisi (multi vs singleton)
    multi_counts = [type_stats[t]["clusters"] - type_stats[t]["singletons"] for t in types]
    single_counts = [type_stats[t]["singletons"] for t in types]
    x = np.arange(len(types))
    w = 0.4

    axes[0].bar(x - w / 2, multi_counts, w, label="Coklu Cluster", color=colors, edgecolor="white")
    axes[0].bar(x + w / 2, single_counts, w, label="Singleton", color=colors, alpha=0.4, edgecolor="white")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    axes[0].set_ylabel("Cluster Sayisi")
    axes[0].set_title("Cluster Sayisi (Ture Gore)")
    axes[0].legend(fontsize=8)

    # Sayilari yaz
    for i, (mc, sc) in enumerate(zip(multi_counts, single_counts)):
        if mc > 0:
            axes[0].text(i - w / 2, mc + 0.1, str(mc), ha="center", fontsize=8)
        if sc > 0:
            axes[0].text(i + w / 2, sc + 0.1, str(sc), ha="center", fontsize=8)

    # Panel 2: Rapor sayisi
    report_counts = [type_stats[t]["reports"] for t in types]
    axes[1].bar(labels, report_counts, color=colors, edgecolor="white")
    for i, rc in enumerate(report_counts):
        axes[1].text(i, rc + 0.2, str(rc), ha="center", fontsize=9)
    axes[1].set_ylabel("Rapor Sayisi")
    axes[1].set_title("Rapor Sayisi (Ture Gore)")
    axes[1].tick_params(axis="x", rotation=30)

    # Panel 3: Pasta — rapor dagilimi
    axes[2].pie(
        report_counts, labels=labels, colors=colors,
        autopct="%1.1f%%", startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
    )
    axes[2].set_title("Rapor Dagilimi (%)")

    fig.suptitle(
        f"Afet Turune Gore Cluster Analizi — Toplam {len(clusters)} cluster, "
        f"{sum(report_counts)} rapor",
        fontsize=13,
    )
    fig.tight_layout()
    out = OUTPUT_DIR / "dbscan_type_distribution.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {out}")


# ── Gorsel 4: Severity analizi ────────────────────────────────────────────

def plot_severity_analysis(clusters: list) -> None:
    """Cluster bazli severity ortalamasi ve yaricap/severity iliskisi."""
    print("  Severity analizi grafigi olusturuluyor...")

    multi_clusters = [c for c in clusters if not c["is_singleton"]]
    if not multi_clusters:
        print("  Coklu cluster yok, severity analizi atlaniyor.")
        return

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # Panel 1: Cluster bazli severity cubuklari
    cids = [f"C{c['cluster_id']}" for c in multi_clusters]
    sevs = [c["severity_avg"] or 0 for c in multi_clusters]
    colors = [DISASTER_TYPE_COLORS.get(c["event_type"], "#666") for c in multi_clusters]

    axes[0].barh(cids, sevs, color=colors, edgecolor="white")
    axes[0].set_xlabel("Severity Ortalamasi")
    axes[0].set_title("Cluster Severity Ortalamalari")
    axes[0].set_xlim(0, 10)
    axes[0].axvline(5, color="orange", linestyle="--", alpha=0.7, label="Orta (5)")
    axes[0].axvline(7, color="red", linestyle="--", alpha=0.7, label="Yuksek (7)")
    axes[0].legend(fontsize=7)

    for i, (cid, sev) in enumerate(zip(cids, sevs)):
        axes[0].text(sev + 0.1, i, f"{sev:.1f}", va="center", fontsize=8)

    # Panel 2: Yaricap vs Severity scatter
    radii = [c["radius_m"] for c in multi_clusters]
    sizes = [c["report_count"] * 30 for c in multi_clusters]

    axes[1].scatter(radii, sevs, c=colors, s=sizes, edgecolors="black", linewidths=0.8, zorder=3)
    axes[1].set_xlabel("Buffer Yaricapi (m)")
    axes[1].set_ylabel("Severity Ortalamasi")
    axes[1].set_title("Yaricap vs Severity")
    axes[1].axhline(5, color="orange", linestyle="--", alpha=0.5)
    axes[1].axhline(7, color="red", linestyle="--", alpha=0.5)

    for c in multi_clusters:
        axes[1].annotate(
            f"C{c['cluster_id']}",
            (c["radius_m"], c["severity_avg"] or 0),
            textcoords="offset points", xytext=(5, 5), fontsize=7,
        )

    # Panel 3: Rapor sayisi vs Severity
    counts = [c["report_count"] for c in multi_clusters]
    axes[2].scatter(counts, sevs, c=colors, s=120, edgecolors="black", linewidths=0.8, zorder=3)
    axes[2].set_xlabel("Rapor Sayisi")
    axes[2].set_ylabel("Severity Ortalamasi")
    axes[2].set_title("Rapor Sayisi vs Severity")

    for c in multi_clusters:
        axes[2].annotate(
            f"C{c['cluster_id']}",
            (c["report_count"], c["severity_avg"] or 0),
            textcoords="offset points", xytext=(5, 5), fontsize=7,
        )

    # Renk efsanesi
    legend_patches = [
        mpatches.Patch(color=c, label=DISASTER_TYPE_LABELS_TR.get(t, t))
        for t, c in DISASTER_TYPE_COLORS.items()
        if any(cl["event_type"] == t for cl in multi_clusters)
    ]
    fig.legend(handles=legend_patches, loc="lower center", ncol=6, fontsize=8, framealpha=0.9)

    fig.suptitle("Cluster Severity Analizi", fontsize=13)
    fig.tight_layout(rect=[0, 0.06, 1, 0.95])
    out = OUTPUT_DIR / "dbscan_severity_analysis.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {out}")


# ── CSV: Cluster ozet tablosu ─────────────────────────────────────────────

def save_cluster_summary(clusters: list) -> None:
    """Cluster ozet tablosunu CSV olarak kaydeder."""
    print("  Cluster ozet tablosu olusturuluyor...")
    rows = []
    for cl in clusters:
        rows.append({
            "cluster_id": cl["cluster_id"],
            "event_type": cl["event_type"],
            "event_type_tr": DISASTER_TYPE_LABELS_TR.get(cl["event_type"], cl["event_type"]),
            "report_count": cl["report_count"],
            "center_lat": round(cl["center_lat"], 6),
            "center_lon": round(cl["center_lon"], 6),
            "initial_lat": round(cl["initial_lat"], 6),
            "initial_lon": round(cl["initial_lon"], 6),
            "radius_m": cl["radius_m"],
            "severity_avg": cl["severity_avg"],
            "is_locked": cl["is_locked"],
            "is_singleton": cl["is_singleton"],
            "reports": ",".join(cl["report_ids"]),
        })

    csv_df = pd.DataFrame(rows)
    out = OUTPUT_DIR / "dbscan_cluster_summary.csv"
    csv_df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  {out}")


# ── Ana akis ──────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("DBSCAN Production Cluster Replikasyonu")
    print("=" * 60)
    print(f"  Parametreler: eps={DBSCAN_EPS_DEG} derece (~{int(DBSCAN_EPS_DEG * DEG_TO_M)}m), "
          f"min_pts={DBSCAN_MIN_PTS}")
    print(f"  Buffer: {int(BUFFER_INITIAL)}-{int(BUFFER_MAX)}m, "
          f"lock_threshold={LOCK_THRESHOLD}\n")

    df = load_reports()
    clusters = build_clusters(df)

    # Ozet istatistikler
    n_multi = sum(1 for c in clusters if not c["is_singleton"])
    n_single = sum(1 for c in clusters if c["is_singleton"])
    n_locked = sum(1 for c in clusters if c["is_locked"])
    total_reports = sum(c["report_count"] for c in clusters)

    print(f"\n  Production DBSCAN Sonuclari:")
    print(f"    Toplam cluster      : {len(clusters)}")
    print(f"    Coklu cluster       : {n_multi}")
    print(f"    Singleton           : {n_single}")
    print(f"    Kilitli merkez      : {n_locked}")
    print(f"    Toplam rapor        : {total_reports}")

    # Tur bazli ozet
    type_summary = Counter(c["event_type"] for c in clusters)
    print(f"\n  Tur bazli cluster dagılımı:")
    for etype, count in sorted(type_summary.items()):
        label = DISASTER_TYPE_LABELS_TR.get(etype, etype)
        print(f"    {label:20s}: {count} cluster")

    # Detayli cluster ciktisi
    print(f"\n  Cluster detaylari:")
    for cl in clusters:
        if cl["is_singleton"]:
            continue
        lock_str = "KILITLI" if cl["is_locked"] else "ACIK"
        print(f"    C{cl['cluster_id']:2d} [{cl['event_type']:15s}] "
              f"{cl['report_count']} rapor, r={cl['radius_m']:5.1f}m, "
              f"sev={cl['severity_avg']}, {lock_str}")
        print(f"         Uyeler: {cl['report_ids']}")

    print()
    plot_cluster_map(df, clusters)
    plot_cluster_detail(clusters)
    plot_type_distribution(clusters)
    plot_severity_analysis(clusters)
    save_cluster_summary(clusters)

    print(f"\n{'=' * 60}")
    print("Tum ciktilar olusturuldu -> analysis/outputs/")
    print("=" * 60)


if __name__ == "__main__":
    main()
