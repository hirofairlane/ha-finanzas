"""Parser for Openbank ``Movimientos de Cuenta.xls``.

The file is actually XHTML in ISO-8859-1 (not real XLS). Structure:

- Header block with title, account number ("Número de Cuenta: 0073 …"),
  description (alias), holder, current balance.
- "Lista de Movimientos" table with columns:
  Fecha Operación | Fecha Valor | Concepto | Importe | Saldo
- Amounts use Spanish notation: "1.497,82" and negative sign for expenses.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from .types import ParsedStatement, ParsedTransaction

_BANK = "Openbank"


def _read(path: str) -> str:
    raw = Path(path).read_bytes()
    # Openbank exports declare charset=iso-8859-1 in the meta tag.
    try:
        return raw.decode("iso-8859-1")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def sniff(path: str) -> bool:
    p = Path(path)
    if p.suffix.lower() not in (".xls", ".html", ".htm"):
        return False
    try:
        head = _read(path)[:4000].lower()
    except OSError:
        return False
    return "cabeceracuerpo" in head and "movimientos" in head


def _parse_amount_es(text: str) -> float:
    # "1.497,82" -> 1497.82  ;  "-8,75" -> -8.75
    text = (text or "").strip().replace("\xa0", "").replace(" ", "")
    if not text:
        return 0.0
    text = text.replace(".", "").replace(",", ".")
    return float(text)


def _parse_date_es(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_IBAN_RE = re.compile(r"([0-9]{4}\s+[0-9]{4}\s+[0-9]{2}\s+[0-9]{10})")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def parse(path: str) -> ParsedStatement:
    html = _read(path)
    soup = BeautifulSoup(html, "lxml")

    # Flatten label→value pairs from the header. Openbank marks values with
    # <b>...</b> inside <font id="CabeceraCuerpo"> siblings; simplest approach
    # is to grab all visible cells in order and scan for anchors.
    all_text = _clean(soup.get_text(" ", strip=True))

    iban_match = _IBAN_RE.search(all_text)
    iban = iban_match.group(1).replace(" ", "") if iban_match else ""

    alias = None
    m = re.search(r"Descripci[oó]n:\s*([A-ZÁÉÍÓÚÑÜa-záéíóúñü0-9 .\-]+?)\s+Titular:",
                  all_text, re.IGNORECASE)
    if m:
        alias = _clean(m.group(1))

    holder = None
    m = re.search(r"Titular:\s*([A-ZÁÉÍÓÚÑÜa-záéíóúñü ,.\-]+?)\s+Saldo:",
                  all_text, re.IGNORECASE)
    if m:
        holder = _clean(m.group(1))

    balance = None
    currency = "EUR"
    m = re.search(r"Saldo:\s*([\-\d.,]+)\s*([A-Z]{3})", all_text)
    if m:
        balance = _parse_amount_es(m.group(1))
        currency = m.group(2)

    # Movements table: find the header row and iterate siblings.
    txs: list[ParsedTransaction] = []
    header_td = soup.find(string=re.compile(r"Fecha Operaci[oó]n", re.IGNORECASE))
    if header_td is None:
        return ParsedStatement(bank=_BANK, iban=iban, holder=holder, alias=alias,
                               currency=currency, balance=balance)

    header_tr = header_td.find_parent("tr")
    if header_tr is None:
        return ParsedStatement(bank=_BANK, iban=iban, holder=holder, alias=alias,
                               currency=currency, balance=balance)

    # Iterate every subsequent <tr> and pick rows that have 5 non-empty cells
    # of the shape [date, date, concept, amount, balance].
    for tr in header_tr.find_next_siblings("tr"):
        tds = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        # Openbank interleaves spacer <td> cells; drop empties.
        cells = [c for c in tds if c]
        if len(cells) < 5:
            continue
        op_date = _parse_date_es(cells[0])
        value_date = _parse_date_es(cells[1])
        if op_date is None:
            continue
        # Amount and balance are the last two numeric-looking cells.
        try:
            balance_val = _parse_amount_es(cells[-1])
            amount_val = _parse_amount_es(cells[-2])
        except ValueError:
            continue
        concept = " ".join(cells[2:-2]) if len(cells) > 4 else cells[2]
        txs.append(ParsedTransaction(
            op_date=op_date,
            value_date=value_date,
            concept=concept,
            amount=amount_val,
            balance=balance_val,
        ))

    return ParsedStatement(
        bank=_BANK, iban=iban, holder=holder, alias=alias,
        currency=currency, balance=balance, transactions=txs,
    )


if __name__ == "__main__":
    import sys
    st = parse(sys.argv[1])
    print(f"Bank={st.bank} IBAN={st.iban} Holder={st.holder} Alias={st.alias}")
    print(f"Balance={st.balance} {st.currency}  Movements={len(st.transactions)}")
    for t in st.transactions[:8]:
        print(f"  {t.op_date}  {t.amount:>10.2f}  {t.concept[:80]}")
