#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鹿児島・熊本発着 航空・フェリー運航状況スクレイパー（Firebase版）

各社の公式サイトを取得し、キーワードから簡易的に運航状況を判定して
Firebase Realtime Database の /transitStatus に書き込む。
kumamoto-hazard-map と同じFirebaseプロジェクトを使う想定。

環境変数 FIREBASE_SERVICE_ACCOUNT にサービスアカウントJSONの中身(文字列)を、
FIREBASE_DATABASE_URL にRealtime DatabaseのURLを渡して実行する。
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
import firebase_admin
from firebase_admin import credentials, db

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 15


def strip_tags(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def parse_jal(text: str) -> dict:
    if re.search(r"平常どおり|通常どおり|通常運航", text):
        return {"level": "ok", "label": "平常運航", "detail": "国内線は平常どおり運航中の記載あり"}
    if re.search(r"欠航|運休", text):
        return {"level": "warn", "label": "影響あり", "detail": "欠航・運休に関する記載を検出"}
    return {"level": "unknown", "label": "要確認", "detail": "自動判定できませんでした"}


def parse_ana(text: str) -> dict:
    return {"level": "unknown", "label": "リンクのみ", "detail": "検索型ページのため自動取得非対応"}


def parse_generic_ferry(text: str) -> dict:
    """多くのフェリー会社サイトに共通する簡易判定（通常運航／欠航・運休のキーワード判定）"""
    if re.search(r"通常運航|平常運航|平常どおり", text):
        return {"level": "ok", "label": "通常運航", "detail": "「通常運航」等の記載あり"}
    if re.search(r"全便欠航|一部欠航|欠航|運休", text):
        return {"level": "warn", "label": "影響あり", "detail": "欠航・運休に関する記載を検出"}
    if len(text) > 50:
        return {"level": "unknown", "label": "要確認", "detail": "欠航・運休の記載は見つかりませんでした（表現が異なる可能性）"}
    return {"level": "err", "label": "取得失敗", "detail": "本文をうまく取得できませんでした"}


SOURCES = [
    {"id": "jal", "board": "air", "company": "JAL（日本航空グループ）",
     "route": "国内線 悪天候・運航情報",
     "url": "https://www.jal.co.jp/jp/ja/other/weather_info_dom/", "parse": parse_jal},
    {"id": "ana", "board": "air", "company": "ANA（全日空グループ）",
     "route": "国内線 運航状況検索",
     "url": "https://www.ana.co.jp/fs/dom/jp/", "parse": parse_ana},
    {"id": "marix", "board": "ship", "company": "マリックスライン",
     "route": "鹿児島 ⇔ 奄美群島 ⇔ 沖縄(那覇)",
     "url": "https://marixline.com/", "parse": parse_generic_ferry},
    {"id": "aline", "board": "ship", "company": "マルエーフェリー",
     "route": "鹿児島 ⇔ 奄美群島 ⇔ 沖縄(那覇) / 鹿児島 ⇔ 喜界 ⇔ 知名",
     "url": "https://www.aline-ferry.com/", "parse": parse_generic_ferry},
    {"id": "kyushoferry", "board": "ship", "company": "九商フェリー",
     "route": "熊本港 ⇔ 島原港",
     "url": "https://www.kyusho-ferry.co.jp/news/", "parse": parse_generic_ferry},
    {"id": "kyushoport", "board": "ship", "company": "九商ポートサービス",
     "route": "鹿児島港 ⇔ 種子島・屋久島（貨物・車両航送）",
     "url": "https://kyusho-port.jp/category/news", "parse": parse_generic_ferry},
    {"id": "otoda", "board": "ship", "company": "折田汽船（フェリー屋久島2）",
     "route": "鹿児島 ⇔ 屋久島(宮之浦港)",
     "url": "https://ferryyakusima2.com/", "parse": parse_generic_ferry},
    {"id": "cosmoline", "board": "ship", "company": "コスモライン",
     "route": "鹿児島 ⇔ 種子島(西之表港)",
     "url": "https://cosmoline.jp/ship-information", "parse": parse_generic_ferry},
]


def fetch_one(source: dict) -> dict:
    result = {
        "id": source["id"], "board": source["board"], "company": source["company"],
        "route": source["route"], "url": source["url"],
    }
    try:
        res = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        res.encoding = res.apparent_encoding or res.encoding
        text = strip_tags(res.text)
        result.update(source["parse"](text))
    except Exception as exc:  # noqa: BLE001
        result.update({
            "level": "err", "label": "取得失敗",
            "detail": f"取得中にエラーが発生しました: {exc}",
        })
    return result


def init_firebase():
    sa_json = os.environ["FIREBASE_SERVICE_ACCOUNT"]
    db_url = os.environ["FIREBASE_DATABASE_URL"]
    cred = credentials.Certificate(json.loads(sa_json))
    firebase_admin.initialize_app(cred, {"databaseURL": db_url})


def main() -> int:
    results = [fetch_one(s) for s in SOURCES]
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }

    init_firebase()
    db.reference("transitStatus").set(payload)
    print(f"Wrote transitStatus with {len(results)} sources to Firebase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
