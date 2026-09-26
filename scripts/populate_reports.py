#!/usr/bin/env python3
"""
OASIS — Test verisi yükleme scripti.

API'nin /api/reports/admin/populate-test-data endpoint'ini çağırır:
  1. TÜM tabloları temizler
  2. backend/seed/test_reports.json'daki raporları ekler
  3. backend/seed/images/ altındaki örnek görselleri raporlara dağıtır

Kullanım:
  python scripts/populate_reports.py                 # sadece yükle
  python scripts/populate_reports.py --analyze       # + AI analizini kuyruğa al
  python scripts/populate_reports.py --cluster       # + DBSCAN ile kümeleri yeniden kur

Sadece Python standart kütüphanesi kullanılır. API çalışıyor olmalıdır
(docker compose up -d).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def post(base_url: str, path: str, timeout: float = 60) -> dict:
    req = urllib.request.Request(f"{base_url}{path}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"❌ {path} → HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"❌ API'ye ulaşılamadı ({base_url}): {e.reason}. `docker compose up -d` çalıştırdınız mı?")


def main() -> None:
    # Windows konsolları (cp1254 vb.) emoji basamaz
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="OASIS test verisini yükler.")
    parser.add_argument("--api", default="http://localhost:8000", help="API adresi (varsayılan: %(default)s)")
    parser.add_argument("--analyze", action="store_true", help="Yüklemeden sonra AI analizini başlat")
    parser.add_argument("--cluster", action="store_true", help="Yüklemeden sonra kümeleri DBSCAN ile yeniden kur")
    args = parser.parse_args()
    base = args.api.rstrip("/")

    print("🗑️  Tablolar temizleniyor ve test raporları yükleniyor...")
    data = post(base, "/api/reports/admin/populate-test-data")
    print(f"   ✅ {data.get('reports_inserted')} rapor, {data.get('images_found')} örnek görsel")

    if args.cluster:
        print("\n📍 Kümeler yeniden oluşturuluyor...")
        print(f"   ✅ {post(base, '/api/reports/cluster-all', timeout=300)}")

    if args.analyze:
        print("\n🤖 AI analizi kuyruğa alınıyor...")
        res = post(base, "/api/reports/analyze-all")
        print(f"   ✅ {res.get('status')} — {res.get('count', 0)} rapor (arka planda sırayla işlenir)")
        print("   İlerlemeyi izlemek için: docker compose logs -f api")


if __name__ == "__main__":
    main()
