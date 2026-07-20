from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class ParsedTransaction:
    op_date: date
    value_date: Optional[date]
    concept: str
    amount: float
    balance: Optional[float] = None


@dataclass
class ParsedStatement:
    bank: str
    iban: str
    holder: Optional[str] = None
    alias: Optional[str] = None
    currency: str = "EUR"
    balance: Optional[float] = None
    transactions: List[ParsedTransaction] = field(default_factory=list)
