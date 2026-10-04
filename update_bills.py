"""衆議院の最新掲載回次の法律案をGitで読めるMarkdownとして保存する。

Python標準ライブラリだけで実行可能。コミットや公開は行わない。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import json
import re
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/menu.htm"


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "YoimachiteiBillReader/1.0"}), timeout=40) as response:
                data = response.read()
                charset = response.headers.get_content_charset() or "cp932"
                if charset.lower().replace("-", "_") in ("shift_jis", "sjis"):
                    charset = "cp932"
                return data.decode(charset)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


class Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row, self.links = [], []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "a" and self.row is not None:
            self.links.append(attrs.get("href", ""))

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append((self.row, self.links))
            self.row = None


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "br", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag in ("p", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def body_text(html):
    # Word文書の本文部分を優先し、共通ナビゲーションを除く。
    match = re.search(r"<div\b[^>]*\bclass\s*=\s*['\"]?WordSection\d+[^>]*>", html, re.I)
    if not match:
        match = re.search(r'<div\b[^>]*\bid\s*=\s*[\'"]?mainlayout[\'"]?[^>]*>', html, re.I)
    if not match:
        raise ValueError("本文領域が見つかりません")
    fragment = re.split(r'<div\b[^>]*\bid\s*=\s*[\'"]?FooterBlock', html[match.end():], flags=re.I)[0]
    fragment = re.sub(r'<div\b[^>]*\bid\s*=\s*[\'"]?breadcrumb[\'"]?[^>]*>.*?</div>', '', fragment, flags=re.I | re.S)
    parser = Text()
    parser.feed(fragment)
    lines = [re.sub(r"[\t\r ]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    text = "\n\n".join(line for line in lines if line)
    if len(text) < 50:
        raise ValueError("本文が短すぎます。公式ページの確認が必要です")
    return text


def save(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def collect(bill):
    try:
        html = fetch(bill["body_index"])
        links = re.findall(r'<a\b[^>]*href\s*=\s*[\'"]([^\'"]+)', html, re.I)
        documents = []
        for href in links:
            # 本文、要綱、修正案を対象とする。PDFは公式リンクで閲覧する。
            if not any(part in href.lower() for part in ("houan/", "youkou/", "syuuseian/", "syuseian/", "shuseian/")):
                continue
            url = urljoin(bill["body_index"], href)
            if url in [doc["url"] for doc in documents]:
                continue
            kind = "提出時法律案" if "/houan/" in url else "要綱" if "/youkou/" in url else "修正案"
            number = len(documents) + 1
            relative = f'bills/{bill["id"]}/{number:02d}.md'
            if url.lower().endswith((".htm", ".html")):
                text = body_text(fetch(url))
                save(ROOT / relative, f'# {bill["title"]}\n\n{kind}\n\n出典：{url}\n\n> 公式HTMLを読みやすいテキストに変換した参考資料です。表や字下げなどの書式は保持されません。正確な文言・構造は出典で確認してください。\n\n---\n\n{text}\n')
                documents.append({"kind": kind, "url": url, "file": relative})
            else:
                documents.append({"kind": kind, "url": url, "file": None})
        bill["documents"] = documents
        if not documents:
            bill["warning"] = "本文リンクを抽出できませんでした。公式の本文情報を参照してください。"
    except Exception as error:
        bill["warning"] = str(error)
    return bill


def main():
    html = fetch(SOURCE)
    session = re.search(r"第(\d+)回国会", html).group(1)
    rows = Rows()
    rows.feed(html)
    bills = []
    names = {"05": "衆法", "06": "参法", "09": "閣法"}
    for cells, links in rows.rows:
        body = next((link for link in links if re.search(r"honbun/g\d+(05|06|09)\d{3}\.htm$", link)), None)
        if body is None or len(cells) < 4:
            continue
        code = re.search(r"g(\d+)(05|06|09)(\d{3})\.htm$", body)
        term, category, number = code.groups()
        progress = next((link for link in links if "keika/" in link), "")
        bills.append({"id": f"{term}-{category}-{number}", "session": int(term), "category": names[category], "number": int(number), "title": cells[2], "status": cells[3], "body_index": urljoin(SOURCE, body), "progress": urljoin(SOURCE, progress) if progress else ""})
    if not bills:
        raise RuntimeError("法律案が抽出できませんでした。既存資料は変更しません")
    print(f"第{session}回：法律案 {len(bills)}件", flush=True)
    # 少数の並行取得で待ち時間を抑える。
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = []
        for index, bill in enumerate(pool.map(collect, bills), 1):
            results.append(bill)
            if index % 10 == 0:
                print(f"取得 {index}/{len(bills)}", flush=True)
    save(ROOT / "data/bills.json", json.dumps({"source": SOURCE, "session": int(session), "bills": results}, ensure_ascii=False, indent=2) + "\n")
    index_lines = [f"# 第{session}回国会 法律案一覧", "", f"出典：[衆議院の議案一覧]({SOURCE})", "", "公式サイトの最新掲載回次です。会期中であることを意味しません。審議状況は取得時点のものです。提出回次の異なる継続案件は、その回次を記載しています。", "", "| 法案番号 | 法案名 | 審議状況 | 本文・資料 |", "|---|---|---|---|"]
    for bill in results:
        docs = " / ".join(f'[{doc["kind"]}]({doc["file"] or doc["url"]})' for doc in bill.get("documents", [])) or "取得できず"
        index_lines.append(f'| 第{bill["session"]}回 {bill["category"]}{bill["number"]} | [{bill["title"]}]({bill["body_index"]}) | {bill["status"]} | {docs} |')
    save(ROOT / "INDEX.md", "\n".join(index_lines) + "\n")
    errors = [bill for bill in results if "warning" in bill]
    save(ROOT / "FETCH_LOG.md", f'# 取得記録\n\n取得日時：{datetime.now().astimezone().isoformat(timespec="seconds")}\n\n対象：第{session}回掲載一覧、法律案{len(results)}件\n\n' + ("抽出上の警告なし。\n" if not errors else "\n".join(f'- {b["id"]}：{b["warning"]}' for b in errors) + "\n"))
    print(f"完了：{len(results)}件、警告{len(errors)}件。変更内容を確認してからコミットしてください。", flush=True)


if __name__ == "__main__":
    main()
