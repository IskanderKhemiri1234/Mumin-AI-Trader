from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionPolicy:
    allow_market_data: bool = True
    allow_account_read: bool = True
    allow_spot_order_submission: bool = False
    allow_withdrawals: bool = False
    allow_margin: bool = False
    allow_futures: bool = False

    def validate(self) -> None:
        if self.allow_withdrawals or self.allow_margin or self.allow_futures:
            raise ValueError("Forbidden permissions enabled.")
