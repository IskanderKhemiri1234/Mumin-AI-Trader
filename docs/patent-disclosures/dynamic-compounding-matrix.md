# Preliminary invention disclosure: Realized-Profit Dynamic Compounding Matrix

**Working title:** Dynamic Compounding Matrix (DCM)  
**Status:** Engineering disclosure draft for prior-art review; not a patent filing, legal opinion, or finding of novelty.

## 1. Abstract

A computer-implemented portfolio-sizing method determines an incremental order-allocation budget exclusively from cumulative realized net trading profit, excluding unrealized gains. A momentum-quality score selects a bounded reinvestment fraction, while a portfolio-drawdown gate can suspend reinvestment. The system calculates a maximum profit-funded allocation budget, subtracts profit-funded amounts already committed to open positions, and then constrains the proposed order by a base ticket, per-trade cap, available cash, reserve requirement, and aggregate exposure limit. Partial position reductions release the corresponding committed profit-funded amount proportionally. The resulting sizing decision is persisted with the position ledger to prevent the same realized-profit pool from being simultaneously allocated to multiple open positions.

## 2. Technical field

Computer-implemented portfolio management, dynamic position sizing, realized-profit accounting, momentum-conditioned capital allocation, and risk-constrained reinvestment.

## 3. Problem addressed

A naive compounding rule may use unrealized gains, repeatedly count the same cumulative profit on every new order, or increase order size without accounting for drawdown, cash reserves, or existing open positions. These behaviors can make nominal “profit reinvestment” exceed the available realized-profit pool. The proposed method separates eligible realized profit from open-position commitments and applies multiple independent execution constraints.

## 4. Proposed technical mechanism

Inputs:

- cumulative realized net P/L after recorded fees;
- current strategy momentum-quality score;
- portfolio drawdown relative to its high-water mark;
- open positions and their profit-funded allocation portions;
- available cash, reserve requirement, per-trade cap, and portfolio exposure ceiling.

Decision sequence:

1. Calculate the eligible profit pool as `max(0, cumulative_realized_net_pnl)`. Unrealized P/L is not an input to this pool.
2. If drawdown reaches the configured pause threshold, the score is below the minimum, or the eligible pool is zero, set the incremental reinvestment budget to zero.
3. Select a bounded fraction from score tiers. In the current implementation the tiers are 10%, 20%, and 30%, with a hard maximum of 30%.
4. Calculate the profit-funded budget ceiling as eligible profit multiplied by the selected fraction.
5. Sum the profit-funded portions already committed to open positions and subtract that commitment from the budget ceiling.
6. Add only the remaining available profit-funded amount to a conservative base ticket, then apply the per-trade cap, available-cash limit, reserve, and aggregate-exposure limit.
7. When a position is partially reduced, release its profit-funded commitment in proportion to the quantity reduced. When closed, release the remaining commitment.

## 5. Example implementation in Mumin AI Trader

- Matrix: `src/mumin_trader/strategy/compounding.py`
- Continuous Paper integration: `scripts/run_continuous_paper.py`
- Research/backtest integration: `src/mumin_trader/research/walk_forward.py`
- Tests: `tests/unit/test_compounding.py`, `tests/unit/test_walk_forward.py`, and `tests/test_continuous_paper.py`

The current Paper implementation uses a 1.02 USDT seed ticket so the simulated gross order can clear the 1.00 USDT minimum after fees and slippage. Incremental sizing above that seed is backed only by realized net profit and remains subject to the existing 5 USDT per-trade cap. The seed ticket itself is still exposed to simulated loss; therefore this method does **not** guarantee preservation of the entire 70 USDT starting balance. A strict guarantee that no principal is ever exposed would require disabling all entries until an independently supplied profit pool exists.

## 6. Draft claim concepts for attorney review

1. A computer-implemented method comprising: determining cumulative realized net profit from closed trading transactions; excluding unrealized gains from an eligible reinvestment pool; determining a reinvestment fraction from a momentum-quality score; and suspending incremental reinvestment when a portfolio-drawdown condition is met.
2. The method of claim 1, further comprising calculating a profit-funded allocation ceiling and subtracting profit-funded allocations committed to currently open positions before authorizing an additional position.
3. The method of claim 2, wherein the committed allocation is stored as a per-position accounting field and is reduced proportionally upon a partial position exit.
4. The method of claim 1, wherein a proposed order amount is bounded by the remaining realized-profit allocation, a base ticket, a per-order notional ceiling, a cash reserve, and an aggregate portfolio exposure ceiling.
5. The method of claim 1, wherein the realized-profit pool is reduced by realized net losses and transaction fees and does not include mark-to-market gains.
6. The method of claim 2, further comprising persisting reinvestment decisions and profit-funded position commitments in a durable ledger to prevent concurrent over-allocation of the same profit pool.

These are drafting concepts only. They are not a conclusion that the subject matter is novel, non-obvious, patentable, or free of third-party rights.

## 7. Known prior-art landscape requiring claim differentiation

A preliminary search found earlier disclosures covering dynamic money management based on realized profit levels and reinvestment/pyramiding of realized profits, including:

- US7707098B2, *System and method for money management using a plurality of profit levels in an electronic trading environment*: https://patents.google.com/patent/US7707098B2/en
- US20120209756A1, discussion of pyramiding already realized profit: https://patentimages.storage.googleapis.com/36/e4/b4/ea316aecb52a20/US20120209756A1.pdf
- US20250069138A1, *Method for Constructing Investment Portfolios*, including momentum-factor portfolio construction: https://patents.google.com/patent/US20250069138A1/en

Accordingly, broad claims to “reinvest realized profits based on momentum” would face substantial prior-art risk. A professional search should focus on the narrower combination of profit-only eligibility, momentum-tiered caps, drawdown suspension, per-position committed-profit accounting, proportional release on partial exits, and simultaneous reserve/exposure constraints.

## 8. Experimental validation plan

Use multiple instruments and multiple candle intervals. Compare fixed-size, shield-only, compounding-only, and combined configurations over identical out-of-sample folds. Report net return after fees/slippage, maximum drawdown, profit factor, trade count, turnover, capital utilization, committed-profit budget, and the percentage of incremental allocations funded by realized net profit. Include loss sequences, partial exits, concurrent positions, and periods where realized P/L turns negative. A successful unit test proves accounting behavior, not profitability or guaranteed compounding.
