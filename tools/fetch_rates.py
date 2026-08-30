#!/usr/bin/env python3
"""日銀・財務省から最新金利を取得して data/latest.json に保存する。

GitHub Actions (.github/workflows/update-rates.yml) が平日夕方に自動実行する。
index.html の「最新金利を取得」ボタンはこのJSONを最優先で読み込むため、
外部のCORS中継サービスに依存せずに最新値を表示できる。
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
UA = {'User-Agent': 'Mozilla/5.0 (interest-rates-chart; +https://github.com/kkagemasa-sketch/interest-rates)'}


def fetch_text(url, enc):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode(enc, errors='replace')


def wareki_to_iso(s):
    """和暦 (R8.3.25 等) → 2026-03-25。変換できなければそのまま返す"""
    m = re.match(r'^([RHST])(\d+)\.(\d+)\.(\d+)', s)
    if not m:
        return s
    base = {'R': 2018, 'H': 1988, 'S': 1925, 'T': 1911}[m.group(1)]
    return f"{base + int(m.group(2))}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


def fetch_policy():
    """無担保コールレート（月次・日銀時系列統計）— 検索結果ページ内のCSVリンクを辿る2段階取得"""
    url = ('https://www.stat-search.boj.or.jp/ssi/cgi-bin/famecgi2?cgi=$nme_r030'
           '&chkfrq=MM&rdoheader=SIMPLE&rdodelimitar=COMMA&hdnYyyyFrom=&hdnYyyyTo='
           '&sw_freq=NONE&sw_yearend=NONE&sw_observed=NONE&hdncode=FM02%27STRECLUCON')
    html = fetch_text(url, 'shift_jis')
    m = re.search(r'href="([^"]*\.csv[^"]*)"', html, re.I)
    if not m:
        raise RuntimeError('CSVリンクが見つからない')
    csv_url = m.group(1)
    if not csv_url.startswith('http'):
        csv_url = 'https://www.stat-search.boj.or.jp' + ('' if csv_url.startswith('/') else '/') + csv_url
    csv = fetch_text(csv_url, 'shift_jis')
    for line in reversed([l for l in csv.strip().splitlines() if l.strip()]):
        parts = [p.strip().strip('"') for p in line.split(',')]
        if len(parts) >= 2 and re.match(r'^\d{4}[/\-]', parts[0]):
            try:
                val = float(parts[1])
            except ValueError:
                continue
            return {'date': parts[0].replace('/', '-'), 'value': val}
    raise RuntimeError('データ行が見つからない')


def fetch_prime():
    """短期・長期プライムレート（日銀）。
    テーブル構造: TH=日付(和暦), TD[0]=短プラ最頻値, TD[3]=長プラ。
    「↓」(前回同値) は数値でないためスキップされ、最後に数値が現れた行＝最終改定日を採用する。
    """
    html = fetch_text('https://www.boj.or.jp/statistics/dl/loan/prime/prime.htm', 'utf-8')

    def strip_tags(s):
        return re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', s))

    short = long_ = None
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S | re.I):
        th = re.search(r'<th[^>]*>(.*?)</th>', row, re.S | re.I)
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)
        if not th or len(tds) < 4:
            continue
        date = strip_tags(th.group(1))
        try:
            v = float(strip_tags(tds[0]))
            if v > 0:
                short = {'date': date, 'value': v}
        except ValueError:
            pass
        try:
            v = float(strip_tags(tds[3]))
            if v > 0:
                long_ = {'date': date, 'value': v}
        except ValueError:
            pass
    if not short or not long_:
        raise RuntimeError('プライムレートが取得できない')
    return short, long_


def fetch_jgb():
    """10年物国債金利（財務省・当年分CSV、Shift-JIS・和暦）。10年物は11列目(index=10)"""
    csv = fetch_text('https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv', 'shift_jis')
    for line in reversed([l for l in csv.strip().splitlines() if l.strip()]):
        parts = [p.strip().strip('"') for p in line.split(',')]
        if len(parts) > 10 and re.match(r'^[RHST\d]', parts[0]):
            try:
                val = float(parts[10])
            except ValueError:
                continue
            return {'date': wareki_to_iso(parts[0]), 'value': val}
    raise RuntimeError('データ行が見つからない')


def main():
    out = {'updated_at': datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}
    errors = []
    try:
        out['policy'] = fetch_policy()
    except Exception as e:
        errors.append(f'無担保コールレート: {e}')
    try:
        out['short'], out['long'] = fetch_prime()
    except Exception as e:
        errors.append(f'プライムレート: {e}')
    try:
        out['jgb'] = fetch_jgb()
    except Exception as e:
        errors.append(f'10年国債: {e}')

    if errors:
        print('警告: ' + '; '.join(errors), file=sys.stderr)

    got = [k for k in ('policy', 'short', 'jgb', 'long') if k in out]
    if len(got) < 4:
        # 1件でも欠けたら既存の latest.json を上書きしない（前回の正常データを保持する）
        print(f'取得できたのは {len(got)}/4 件のみのため latest.json は更新しません。', file=sys.stderr)
        sys.exit(1)

    os.makedirs('data', exist_ok=True)
    with open('data/latest.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('data/latest.json を更新しました: ' + json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
