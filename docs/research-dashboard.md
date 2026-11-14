# Smart Sniper research dashboard publishing

The Android dashboard is read-only. Its research button opens
\`latest-research-summary.html\` from the \`mumin-paper-ledger\` branch; the matching
\`latest-research-summary.json\` contains the full 768-series breakdown.

The publisher workflow runs only after a successful
\`Smart Sniper Historical Walk-Forward Research\` run on
\`feature/smart-sniper-validation-final\`. It downloads that exact run's artifact,
validates the complete 8-symbol × 6-timeframe × 4-spread × 4-ablation matrix,
and publishes the derived HTML/JSON to the ledger branch. It never executes
artifact code, reads exchange credentials, or sends orders.

The Paper engine preserves both research files when it refreshes its SQLite
snapshot and re-renders the HTML freshness state on every Paper cycle. Once
the source data is older than seven days, all performance and coverage metrics
are hidden while the source run and generation timestamp remain visible. If
the JSON is missing or invalid, the HTML page is removed so Android shows its
unavailable state rather than inventing data. A failed publisher never replaces
a valid ledger report.

The published table reports averages across independent symbol/timeframe
series. Counts and fees are descriptive totals, not one combined account.
Profit Factor values use per-fold values only; zero and 999999 sentinel values
are explicitly identified and are not presented as a single portfolio PF.
