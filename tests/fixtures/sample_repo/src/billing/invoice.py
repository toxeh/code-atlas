"""Billing helpers for the sample fixture."""


class Invoice:
    def __init__(self, amount: int) -> None:
        self.amount = amount


def create_invoice(amount: int) -> Invoice:
    return Invoice(amount)


def total(invoices: list[Invoice]) -> int:
    return sum(i.amount for i in invoices)
